"""GDPR self-service: export everything a user owns, or erase the account.

Plain module (no Streamlit import; docs/DEVELOPMENT.md "testability rule").
The sidebar Account panel delegates here.

Export: one JSON-serializable dict with every row the user owns across all
per-user tables — their data, complete and unfiltered.

Deletion: on Postgres a single `DELETE FROM auth.users` — the ON DELETE
CASCADE foreign keys (001+ migrations) erase every app row atomically, a
chain verified repeatedly to leave zero orphans. On SQLite (dev), rows are
deleted table by table plus the local auth user/sessions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import db

# Every table with a user_id column. New per-user tables MUST be added here —
# tests/test_user_data.py cross-checks this list against DATA_MODEL.md's rule.
PER_USER_TABLES = (
    "holdings",
    "imports",
    "snapshots",
    "refresh_runs",
    "budget_settings",
    "budget_expenses",
    "manual_assets",
    "broker_imports",
    "broker_transactions",
    "income_events",
    "goals",
)


def export_user_data(user_id: str, db_file: Path | str | None = None) -> dict:
    """All rows the user owns, keyed by table — the GDPR data-portability
    export. Values are JSON-serializable (the db layer already returns
    str/float/int for Postgres types)."""
    tables: dict[str, list[dict]] = {}
    with db.connect(db_file) as connection:
        for table in PER_USER_TABLES:
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE user_id = ?", (user_id,)  # noqa: S608 — fixed table list
            ).fetchall()
            tables[table] = [dict(row) for row in rows]
    return {
        "format": "vermo-export-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "row_counts": {table: len(rows) for table, rows in tables.items()},
        "tables": tables,
    }


def delete_account(user_id: str, db_file: Path | str | None = None) -> dict:
    """Permanently erase the user: every app row and the auth identity.
    Returns per-table deleted counts. Irreversible by design."""
    deleted: dict[str, int] = {}
    with db.connect(db_file) as connection:
        if db_file is None and db.is_postgres():
            # Count first (for the receipt), then let the auth.users cascade
            # do the actual erasure atomically.
            for table in PER_USER_TABLES:
                row = connection.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE user_id = ?", (user_id,)  # noqa: S608
                ).fetchall()[0]
                deleted[table] = row["n"]
            connection.execute("DELETE FROM auth.users WHERE id = ?", (user_id,))
            deleted["auth.users"] = 1
            return deleted

        for table in PER_USER_TABLES:
            cursor = connection.execute(
                f"DELETE FROM {table} WHERE user_id = ?", (user_id,)  # noqa: S608
            )
            deleted[table] = max(cursor.rowcount, 0)

    # SQLite dev mode: the local auth identity lives in auth.db.
    if db_file is None and not db.is_postgres():
        from auth import local_auth

        with local_auth._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        deleted["auth users/sessions"] = 1
    return deleted
