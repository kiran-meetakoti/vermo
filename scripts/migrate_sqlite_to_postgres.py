"""One-time migration: copy data/portfolio.db (SQLite) into Supabase Postgres.

Stage 2 of MULTI_USER_ROADMAP.md. Apply migrations/postgres/001_initial_schema.sql
to the Supabase project first, and create your account there (Supabase Auth),
then run:

    uv run python scripts/migrate_sqlite_to_postgres.py \
        --user-map <old-sqlite-user-id>=<new-supabase-auth-uuid> \
        --dry-run                     # inspect the plan first
    uv run python scripts/migrate_sqlite_to_postgres.py \
        --user-map <old-sqlite-user-id>=<new-supabase-auth-uuid> \
        --execute

Connection comes from DATABASE_URL in .env (gitignored) or the environment —
use the Supabase dashboard's "Connect" pooler URI with the service-role
password, since inserts must bypass RLS.

Design:
  * Only rows whose user_id appears in --user-map are migrated (auth.users
    rows must exist first; anything else would violate the FK — and unmapped
    users shouldn't silently end up owned by someone new).
  * Original UUID ids are preserved, so broker_transactions.import_id keeps
    pointing at the right broker_imports row.
  * Idempotent: every INSERT is ON CONFLICT DO NOTHING; re-running after a
    partial failure is safe.
  * SQLite's ISO-8601 strings pass straight into timestamptz/date columns;
    is_recurring int -> bool and refresh_runs.details text -> jsonb are cast
    explicitly.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SQLITE_DB = BASE_DIR / "data" / "portfolio.db"

# table -> (columns copied verbatim, per-column SQL cast overrides)
TABLES: dict[str, tuple[tuple[str, ...], dict[str, str]]] = {
    "holdings": (
        ("id", "user_id", "name", "ticker", "market", "value_eur", "return_percent",
         "asset_class", "quantity", "average_cost", "current_price", "invested_eur",
         "source_currency", "updated_at", "asset_category", "cap_bucket",
         "barbell_role", "barbell_reason"),
        {},
    ),
    "imports": (
        ("id", "user_id", "filename", "source", "imported", "updated", "total_rows", "created_at"),
        {},
    ),
    "snapshots": (
        ("user_id", "snapshot_date", "market", "net_worth_eur", "invested_eur",
         "holdings_count", "created_at"),
        {},
    ),
    "refresh_runs": (
        ("id", "user_id", "refreshed", "skipped", "failed", "fx_rate_inr", "details", "created_at"),
        {"details": "%s::jsonb"},
    ),
    "budget_settings": (
        ("user_id", "key", "value", "updated_at"),
        {},
    ),
    "budget_expenses": (
        ("id", "user_id", "name", "category", "amount_eur", "expense_date",
         "created_at", "updated_at", "is_recurring"),
        {"is_recurring": "%s::int::bool"},
    ),
    "manual_assets": (
        ("id", "user_id", "name", "category", "value_eur", "cost_eur", "currency",
         "original_value", "notes", "created_at", "updated_at"),
        {},
    ),
    "broker_imports": (
        ("id", "user_id", "broker", "filename", "imported", "created_at"),
        {},
    ),
    "broker_transactions": (
        ("id", "user_id", "import_id", "broker", "trade_date", "transaction_type",
         "name", "ticker", "market", "quantity", "price", "amount", "fee",
         "currency", "created_at"),
        {},
    ),
}

CONFLICT_KEYS = {
    "snapshots": "(user_id, snapshot_date, market)",
    "budget_settings": "(user_id, key)",
    "settings": "(key)",
}


def load_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    env_file = BASE_DIR / ".env"
    if not url and env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip("'\"")
                break
    if not url:
        sys.exit("DATABASE_URL not set (put it in .env or the environment). "
                 "Use the Supabase 'Connect' URI with the service-role password.")
    return url


def parse_user_map(pairs: list[str]) -> dict[str, str]:
    mapping = {}
    for pair in pairs:
        old, _, new = pair.partition("=")
        if not old or not new:
            sys.exit(f"--user-map expects old-id=new-uuid, got: {pair!r}")
        mapping[old] = new
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sqlite", default=str(SQLITE_DB), help="source SQLite file")
    parser.add_argument("--user-map", action="append", default=[], metavar="OLD=NEW",
                        help="map an old SQLite user_id to a Supabase auth.users id (repeatable)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="report what would be copied")
    group.add_argument("--execute", action="store_true", help="actually write to Postgres")
    args = parser.parse_args()

    user_map = parse_user_map(args.user_map)
    if not user_map:
        sys.exit("At least one --user-map old-id=new-uuid is required.")

    source = sqlite3.connect(args.sqlite)
    source.row_factory = sqlite3.Row

    plan: dict[str, tuple[list[tuple], int]] = {}
    for table, (columns, _) in TABLES.items():
        rows = source.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608 - fixed table names
        mapped, skipped = [], 0
        for row in rows:
            if row["user_id"] not in user_map:
                skipped += 1
                continue
            record = {col: row[col] for col in columns}
            record["user_id"] = user_map[row["user_id"]]
            mapped.append(tuple(record[col] for col in columns))
        plan[table] = (mapped, skipped)

    fx_rows = [(r["key"], r["value"], r["updated_at"])
               for r in source.execute("SELECT * FROM settings").fetchall()]

    total = sum(len(rows) for rows, _ in plan.values()) + len(fx_rows)
    for table, (rows, skipped) in plan.items():
        note = f"  (skipping {skipped} rows of unmapped users)" if skipped else ""
        print(f"{table:22s} {len(rows):5d} rows{note}")
    print(f"{'settings (global FX)':22s} {len(fx_rows):5d} rows")
    print(f"{'TOTAL':22s} {total:5d} rows")

    if args.dry_run:
        print("\nDry run only — nothing written. Re-run with --execute to migrate.")
        return

    import psycopg  # imported late so --dry-run works without the driver

    with psycopg.connect(load_database_url()) as conn, conn.cursor() as cur:
        for table, (columns, casts) in TABLES.items():
            rows, _ = plan[table]
            if not rows:
                continue
            placeholders = ", ".join(casts.get(col, "%s") for col in columns)
            conflict = CONFLICT_KEYS.get(table, "(id)")
            cur.executemany(
                f"INSERT INTO public.{table} ({', '.join(columns)}) "
                f"VALUES ({placeholders}) ON CONFLICT {conflict} DO NOTHING",
                rows,
            )
            print(f"inserted {table}: {cur.rowcount if cur.rowcount >= 0 else len(rows)}")
        if fx_rows:
            cur.executemany(
                "INSERT INTO public.settings (key, value, updated_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (key) DO NOTHING",
                fx_rows,
            )
        conn.commit()

        print("\nRow counts in Postgres after migration:")
        for table in list(TABLES) + ["settings"]:
            cur.execute(f"SELECT COUNT(*) FROM public.{table}")  # noqa: S608
            print(f"{table:22s} {cur.fetchone()[0]:5d}")


if __name__ == "__main__":
    main()
