"""Budget-expense data layer, kept separate from streamlit_app.py so it can be
imported and unit-tested without running the Streamlit app (streamlit_app.py
executes login, network calls, and page rendering at import time).

streamlit_app.py delegates its expense_exists / add_budget_expense to the
functions here, so tests against this module exercise the real code paths —
in particular the duplicate-import guard, whose absence caused earlier
duplicate-transaction cleanups.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATABASE_FILE = DATA_DIR / "portfolio.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_file: Path | str = DATABASE_FILE) -> sqlite3.Connection:
    Path(db_file).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_file)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_schema(db_file: Path | str = DATABASE_FILE) -> None:
    """Create the budget_expenses table if it doesn't exist. The app has its own
    migration-aware init in streamlit_app.py; this canonical CREATE exists so
    tests can spin up a fresh DB with the same shape."""
    with connect(db_file) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS budget_expenses (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'local-user',
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                amount_eur REAL NOT NULL,
                expense_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_recurring INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def expense_exists(
    user_id: str,
    name: str,
    amount_eur: float,
    expense_date_iso: str,
    db_file: Path | str = DATABASE_FILE,
) -> bool:
    """True if this user already has an expense with the same name, amount, and
    date. Matches on name+amount+date only — deliberately ignoring category,
    because auto-categorization isn't stable across runs, which is how earlier
    duplicate batches slipped past a category-sensitive check. ROUND avoids
    float-representation misses."""
    with connect(db_file) as connection:
        row = connection.execute(
            "SELECT 1 FROM budget_expenses "
            "WHERE user_id = ? AND name = ? AND expense_date = ? "
            "AND ROUND(amount_eur, 2) = ROUND(?, 2) LIMIT 1",
            (user_id, name, expense_date_iso, amount_eur),
        ).fetchone()
    return row is not None


def add_expense(
    user_id: str,
    name: str,
    category: str,
    amount_eur: float,
    expense_date: date | None = None,
    is_recurring: bool = False,
    db_file: Path | str = DATABASE_FILE,
) -> None:
    now = _utc_now()
    with connect(db_file) as connection:
        connection.execute(
            "INSERT INTO budget_expenses "
            "(id, user_id, name, category, amount_eur, expense_date, created_at, updated_at, is_recurring) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid4()), user_id, name, category, amount_eur,
                (expense_date or date.today()).isoformat(), now, now, int(is_recurring),
            ),
        )
