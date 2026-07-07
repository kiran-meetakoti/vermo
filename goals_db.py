"""Named financial goals: data layer + status math.

Plain module (no Streamlit import; docs/DEVELOPMENT.md "testability rule").
Postgres schema: migrations/postgres/004_goals.sql; ensure_schema() is the
SQLite twin. A goal is a lens on total wealth ("net worth €60k by 2028"),
not an allocation — several goals may reference the same money, which the
UI states plainly.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import db
from finance_math import portfolio_projection, required_monthly_contribution


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_file: Path | str | None = None):
    """db_file=None → configured backend; explicit db_file → SQLite (tests)."""
    return db.connect(db_file)


def ensure_schema(db_file: Path | str | None = None) -> None:
    """SQLite twin of the 004 Postgres migration (no-op on Postgres)."""
    if db_file is None and db.is_postgres():
        return
    with connect(db_file) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS goals (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'local-user',
                name TEXT NOT NULL,
                target_eur REAL NOT NULL,
                target_date TEXT NOT NULL,
                monthly_contribution REAL NOT NULL DEFAULT 0,
                expected_return_percent REAL NOT NULL DEFAULT 5.0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def add_goal(
    user_id: str,
    name: str,
    target_eur: float,
    target_date: date,
    monthly_contribution: float = 0.0,
    expected_return_percent: float = 5.0,
    db_file: Path | str | None = None,
) -> str:
    if not name.strip():
        raise ValueError("Goal name is required")
    if target_eur <= 0:
        raise ValueError("target_eur must be positive")
    goal_id = str(uuid4())
    now = _utc_now()
    with connect(db_file) as connection:
        connection.execute(
            "INSERT INTO goals (id, user_id, name, target_eur, target_date, monthly_contribution, "
            "expected_return_percent, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (goal_id, user_id, name.strip(), target_eur, target_date.isoformat(),
             monthly_contribution, expected_return_percent, now, now),
        )
    return goal_id


def delete_goal(user_id: str, goal_id: str, db_file: Path | str | None = None) -> None:
    with connect(db_file) as connection:
        connection.execute("DELETE FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id))


def goals(user_id: str, db_file: Path | str | None = None) -> list[dict]:
    with connect(db_file) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM goals WHERE user_id = ? ORDER BY target_date", (user_id,)
        ).fetchall()]


def goal_status(goal: dict, current_total_eur: float, today: date | None = None) -> dict:
    """Pure status computation for one goal against today's total wealth.

    Returns: months_left, progress_percent (capped at 100), projected_eur
    (current total grown with the planned monthly contribution at the
    expected return until target_date), on_track, reached, and
    required_monthly — the contribution that would reach the target from
    today regardless of the plan (0 when growth alone suffices or the
    target is reached; the UI surfaces it when behind)."""
    today = today or date.today()
    target_date = date.fromisoformat(str(goal["target_date"])[:10])
    months_left = max((target_date.year - today.year) * 12 + target_date.month - today.month, 0)
    target = goal["target_eur"]
    reached = current_total_eur >= target
    projection = portfolio_projection(
        current_total_eur, months_left, goal["monthly_contribution"], 0,
        goal["expected_return_percent"], today=today,
    ) if months_left else None
    projected = projection["value"] if projection else current_total_eur
    return {
        "months_left": months_left,
        "progress_percent": round(min(current_total_eur / target * 100, 100.0), 1) if target else 0.0,
        "projected_eur": round(projected, 2),
        "reached": reached,
        "on_track": reached or projected >= target,
        "required_monthly": required_monthly_contribution(
            current_total_eur, target, months_left, goal["expected_return_percent"]
        ),
    }
