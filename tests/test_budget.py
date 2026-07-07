"""Tests for the budget-expense duplicate-import guard.

These cover the exact failure that caused two rounds of manual duplicate
cleanup: re-importing the same (or an overlapping) bank statement double-booked
transactions, and a category-sensitive check let a second batch through because
auto-categorization had relabeled the rows.
"""

from datetime import date

import pytest

import budget_db

USER = "test-user"
OTHER_USER = "someone-else"


@pytest.fixture()
def db(tmp_path):
    """A fresh, isolated budget_expenses DB for each test."""
    db_file = tmp_path / "portfolio.db"
    budget_db.ensure_schema(db_file)
    return db_file


def _count(db_file):
    with budget_db.connect(db_file) as conn:
        return conn.execute("SELECT COUNT(*) FROM budget_expenses").fetchone()[0]


def test_expense_absent_on_empty_db(db):
    assert budget_db.expense_exists(USER, "Rent", 1542.0, "2026-05-01", db) is False


def test_added_expense_is_detected(db):
    budget_db.add_expense(USER, "Rent", "Rent", 1542.0, date(2026, 5, 1), db_file=db)
    assert budget_db.expense_exists(USER, "Rent", 1542.0, "2026-05-01", db) is True


def test_dedup_ignores_category(db):
    """The real bug: same txn re-imported under a different category must still
    be recognized as a duplicate."""
    budget_db.add_expense(USER, "Filx SE", "Travel", 177.97, date(2026, 5, 15), db_file=db)
    # Second import auto-categorized it as Transport instead — still a duplicate.
    assert budget_db.expense_exists(USER, "Filx SE", 177.97, "2026-05-15", db) is True


def test_dedup_survives_float_representation(db):
    budget_db.add_expense(USER, "Coffee", "Eating out", 12.99, date(2026, 5, 2), db_file=db)
    # A value that isn't bit-identical to 12.99 must still match after rounding.
    assert budget_db.expense_exists(USER, "Coffee", 12.99 + 1e-9, "2026-05-02", db) is True


def test_different_date_is_not_duplicate(db):
    budget_db.add_expense(USER, "Coffee", "Eating out", 12.99, date(2026, 5, 2), db_file=db)
    assert budget_db.expense_exists(USER, "Coffee", 12.99, "2026-05-03", db) is False


def test_different_amount_is_not_duplicate(db):
    budget_db.add_expense(USER, "Coffee", "Eating out", 12.99, date(2026, 5, 2), db_file=db)
    assert budget_db.expense_exists(USER, "Coffee", 13.99, "2026-05-02", db) is False


def test_dedup_is_per_user(db):
    budget_db.add_expense(USER, "Rent", "Rent", 1542.0, date(2026, 5, 1), db_file=db)
    # A different user with the same expense is not a duplicate for them.
    assert budget_db.expense_exists(OTHER_USER, "Rent", 1542.0, "2026-05-01", db) is False


def test_reimport_of_same_statement_skips_all_rows(db):
    """End-to-end shape of the importer: importing a statement, then importing
    the identical statement again, must add nothing the second time."""
    statement = [
        ("Rent", "Rent", 1542.0, date(2026, 5, 1)),
        ("Filx SE", "Travel", 177.97, date(2026, 5, 15)),
        ("Coffee", "Eating out", 12.99, date(2026, 5, 2)),
    ]

    def import_statement(rows):
        imported = skipped = 0
        for name, category, amount, when in rows:
            if budget_db.expense_exists(USER, name, amount, when.isoformat(), db):
                skipped += 1
                continue
            budget_db.add_expense(USER, name, category, amount, when, db_file=db)
            imported += 1
        return imported, skipped

    assert import_statement(statement) == (3, 0)
    assert _count(db) == 3

    # Re-import the exact same file — everything is a duplicate now.
    assert import_statement(statement) == (0, 3)
    assert _count(db) == 3
