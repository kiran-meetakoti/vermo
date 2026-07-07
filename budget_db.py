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


def ensure_recurring_expenses(
    user_id: str,
    db_file: Path | str = DATABASE_FILE,
    today: date | None = None,
) -> int:
    """Materialize a concrete row for each month a recurring expense (e.g. Rent,
    Loan) should appear in, from its earliest occurrence through the current
    month — so marking something recurring means it never has to be re-entered
    by hand.

    Grouped by (name, category); the most recent row's amount is the template
    for the backfilled months. Idempotent: months that already have a row are
    left untouched. `today` exists so tests can pin the current month.
    Returns the number of rows inserted."""
    current_month = (today or date.today()).strftime("%Y-%m")
    now = _utc_now()
    inserted = 0
    with connect(db_file) as connection:
        rows = connection.execute(
            "SELECT * FROM budget_expenses WHERE user_id = ? AND is_recurring = 1", (user_id,)
        ).fetchall()
        if not rows:
            return 0
        groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for row in rows:
            groups.setdefault((row["name"], row["category"]), []).append(row)

        for (name, category), group in groups.items():
            group_sorted = sorted(group, key=lambda r: r["expense_date"] or "")
            start_month = (group_sorted[0]["expense_date"] or "")[:7]
            template_amount = group_sorted[-1]["amount_eur"]
            existing_months = {(r["expense_date"] or "")[:7] for r in group}
            if not start_month:
                continue

            year, month = map(int, start_month.split("-"))
            month_cursor = start_month
            while month_cursor <= current_month:
                if month_cursor not in existing_months:
                    connection.execute(
                        "INSERT INTO budget_expenses "
                        "(id, user_id, name, category, amount_eur, expense_date, created_at, updated_at, is_recurring) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                        (str(uuid4()), user_id, name, category, template_amount, f"{month_cursor}-01", now, now),
                    )
                    inserted += 1
                month += 1
                if month > 12:
                    month = 1
                    year += 1
                month_cursor = f"{year:04d}-{month:02d}"
    return inserted


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
