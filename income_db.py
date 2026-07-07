"""Income data layer: dividends, interest, rent, and other income events.

Plain module (no Streamlit import) so it is unit-testable — see
docs/DEVELOPMENT.md, "The testability rule". streamlit_app.py's Income page
delegates here. Postgres schema comes from
migrations/postgres/003_income_events.sql; ensure_schema() is the SQLite
equivalent (dev/tests) with the same shape.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import db

INCOME_TYPES = ("Dividend", "Interest", "Rent", "Other")


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def connect(db_file: Path | str | None = None):
    """db_file=None → the configured backend; explicit db_file → SQLite on
    that file (tests use tmp paths)."""
    return db.connect(db_file)


def ensure_schema(db_file: Path | str | None = None) -> None:
    """SQLite twin of the 003 Postgres migration (skip when on Postgres —
    migrations own that schema)."""
    if db_file is None and db.is_postgres():
        return
    with connect(db_file) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS income_events (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'local-user',
                source_type TEXT NOT NULL,
                holding_id TEXT,
                name TEXT NOT NULL,
                amount_eur REAL NOT NULL,
                income_date TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def add_income(
    user_id: str,
    source_type: str,
    name: str,
    amount_eur: float,
    income_date: date,
    holding_id: str | None = None,
    notes: str | None = None,
    db_file: Path | str | None = None,
) -> str:
    if source_type not in INCOME_TYPES:
        raise ValueError(f"source_type must be one of {INCOME_TYPES}")
    if amount_eur <= 0:
        raise ValueError("amount_eur must be positive")
    event_id = str(uuid4())
    now = _utc_now()
    with connect(db_file) as connection:
        connection.execute(
            "INSERT INTO income_events "
            "(id, user_id, source_type, holding_id, name, amount_eur, income_date, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, user_id, source_type, holding_id, name, amount_eur,
             income_date.isoformat(), notes, now, now),
        )
    return event_id


def delete_income(user_id: str, event_id: str, db_file: Path | str | None = None) -> None:
    with connect(db_file) as connection:
        connection.execute(
            "DELETE FROM income_events WHERE id = ? AND user_id = ?", (event_id, user_id)
        )


def income_events(user_id: str, db_file: Path | str | None = None) -> list[dict]:
    with connect(db_file) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM income_events WHERE user_id = ? ORDER BY income_date DESC, created_at DESC",
            (user_id,),
        ).fetchall()]


def income_summary(user_id: str, today: date | None = None, db_file: Path | str | None = None) -> dict:
    """Aggregates for the Income page: trailing-12-month total, current-year
    total, monthly average (trailing 12m), and per-type totals (trailing 12m)."""
    today = today or date.today()
    year_start = today.replace(month=1, day=1).isoformat()
    trailing_start = (today - timedelta(days=365)).isoformat()
    events = income_events(user_id, db_file=db_file)

    trailing = [e for e in events if e["income_date"] >= trailing_start]
    this_year = [e for e in events if e["income_date"] >= year_start]
    by_type: dict[str, float] = {}
    for event in trailing:
        by_type[event["source_type"]] = by_type.get(event["source_type"], 0.0) + event["amount_eur"]
    return {
        "trailing_12m": round(sum(e["amount_eur"] for e in trailing), 2),
        "this_year": round(sum(e["amount_eur"] for e in this_year), 2),
        "monthly_avg": round(sum(e["amount_eur"] for e in trailing) / 12, 2),
        "by_type": {k: round(v, 2) for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])},
    }


def income_by_month(user_id: str, months: int = 12, today: date | None = None,
                    db_file: Path | str | None = None) -> list[dict]:
    """Chart data: one row per (month, source_type) with the summed amount,
    covering the last `months` months (empty months absent)."""
    today = today or date.today()
    start = (today.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1).isoformat()
    buckets: dict[tuple[str, str], float] = {}
    for event in income_events(user_id, db_file=db_file):
        if event["income_date"] < start:
            continue
        key = (event["income_date"][:7], event["source_type"])
        buckets[key] = buckets.get(key, 0.0) + event["amount_eur"]
    return [
        {"month": month, "source_type": source_type, "amount_eur": round(amount, 2)}
        for (month, source_type), amount in sorted(buckets.items())
    ]


def dividend_yields(user_id: str, db_file: Path | str | None = None) -> list[dict]:
    """Trailing-12-month income per linked holding with its yield on current
    value: [{name, ticker, income_12m, value_eur, yield_percent}]."""
    trailing_start = (date.today() - timedelta(days=365)).isoformat()
    with connect(db_file) as connection:
        rows = connection.execute(
            """
            SELECT h.id, h.name, h.ticker, h.value_eur, SUM(i.amount_eur) AS income_12m
            FROM income_events i
            JOIN holdings h ON h.id = i.holding_id AND h.user_id = i.user_id
            WHERE i.user_id = ? AND i.income_date >= ?
            GROUP BY h.id, h.name, h.ticker, h.value_eur
            ORDER BY income_12m DESC
            """,
            (user_id, trailing_start),
        ).fetchall()
    return [
        {
            "name": row["name"],
            "ticker": row["ticker"],
            "income_12m": round(row["income_12m"], 2),
            "value_eur": row["value_eur"],
            "yield_percent": round(row["income_12m"] / row["value_eur"] * 100, 2) if row["value_eur"] else 0.0,
        }
        for row in rows
    ]
