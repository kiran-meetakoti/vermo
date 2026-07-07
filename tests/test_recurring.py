"""Tests for budget_db.ensure_recurring_expenses — monthly materialization of
recurring expenses (Rent, Loan, …).

The failure modes worth pinning down are the quiet ones: a month silently
skipped or doubled after a rollover, re-runs inserting again, or one user's
recurring template leaking into another's ledger.
"""

from datetime import date

import pytest

import budget_db

USER = "user-a"


@pytest.fixture()
def db(tmp_path):
    db_file = tmp_path / "portfolio.db"
    budget_db.ensure_schema(db_file)
    return db_file


def months(db_file, user_id=USER):
    with budget_db.connect(db_file) as conn:
        rows = conn.execute(
            "SELECT expense_date, amount_eur FROM budget_expenses "
            "WHERE user_id = ? ORDER BY expense_date",
            (user_id,),
        ).fetchall()
    return [(r["expense_date"][:7], r["amount_eur"]) for r in rows]


def add_recurring(db_file, name, amount, on, category="Rent", user_id=USER):
    budget_db.add_expense(user_id, name, category, amount, on, is_recurring=True, db_file=db_file)


def test_backfills_from_first_occurrence_to_current_month(db):
    add_recurring(db, "Rent", 1200.0, date(2026, 3, 15))
    inserted = budget_db.ensure_recurring_expenses(USER, db, today=date(2026, 6, 20))
    assert inserted == 3  # Apr, May, Jun
    assert months(db) == [("2026-03", 1200.0), ("2026-04", 1200.0), ("2026-05", 1200.0), ("2026-06", 1200.0)]


def test_idempotent_on_rerun(db):
    add_recurring(db, "Rent", 1200.0, date(2026, 3, 15))
    budget_db.ensure_recurring_expenses(USER, db, today=date(2026, 6, 20))
    assert budget_db.ensure_recurring_expenses(USER, db, today=date(2026, 6, 20)) == 0
    assert len(months(db)) == 4


def test_year_rollover(db):
    add_recurring(db, "Loan", 350.0, date(2025, 11, 1), category="Loan")
    inserted = budget_db.ensure_recurring_expenses(USER, db, today=date(2026, 2, 10))
    assert inserted == 3
    assert [m for m, _ in months(db)] == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_latest_amount_is_the_template(db):
    # Rent raised in May — backfilled June must use the May amount.
    add_recurring(db, "Rent", 1200.0, date(2026, 3, 1))
    add_recurring(db, "Rent", 1300.0, date(2026, 5, 1))
    budget_db.ensure_recurring_expenses(USER, db, today=date(2026, 6, 15))
    assert months(db) == [
        ("2026-03", 1200.0),
        ("2026-04", 1300.0),  # backfilled gap uses the latest template
        ("2026-05", 1300.0),
        ("2026-06", 1300.0),
    ]


def test_existing_month_never_doubled(db):
    add_recurring(db, "Rent", 1200.0, date(2026, 3, 1))
    add_recurring(db, "Rent", 1200.0, date(2026, 4, 20))  # manually entered April
    budget_db.ensure_recurring_expenses(USER, db, today=date(2026, 4, 25))
    assert [m for m, _ in months(db)] == ["2026-03", "2026-04"]


def test_groups_by_name_and_category_independently(db):
    add_recurring(db, "Rent", 1200.0, date(2026, 5, 1), category="Rent")
    add_recurring(db, "Gym", 40.0, date(2026, 4, 1), category="Other")
    budget_db.ensure_recurring_expenses(USER, db, today=date(2026, 6, 1))
    by_month = months(db)
    assert ("2026-06", 1200.0) in by_month and ("2026-06", 40.0) in by_month
    # Gym backfills May from its own April start; Rent doesn't reach back to April.
    assert ("2026-05", 40.0) in by_month
    assert ("2026-04", 1200.0) not in by_month


def test_non_recurring_expenses_are_ignored(db):
    budget_db.add_expense(USER, "One-off dinner", "Eating out", 60.0, date(2026, 3, 1), db_file=db)
    assert budget_db.ensure_recurring_expenses(USER, db, today=date(2026, 6, 1)) == 0
    assert len(months(db)) == 1


def test_scoped_to_the_given_user(db):
    add_recurring(db, "Rent", 1200.0, date(2026, 5, 1), user_id="user-a")
    add_recurring(db, "Rent", 900.0, date(2026, 5, 1), user_id="user-b")
    budget_db.ensure_recurring_expenses("user-a", db, today=date(2026, 6, 1))
    assert [m for m, _ in months(db, "user-a")] == ["2026-05", "2026-06"]
    # user-b untouched until their own run.
    assert [m for m, _ in months(db, "user-b")] == ["2026-05"]


def test_current_month_only_inserts_nothing_extra(db):
    add_recurring(db, "Rent", 1200.0, date(2026, 6, 1))
    assert budget_db.ensure_recurring_expenses(USER, db, today=date(2026, 6, 30)) == 0
