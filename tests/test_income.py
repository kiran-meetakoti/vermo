"""Tests for income_db — dividends/interest/rent tracking and aggregates.

These numbers render as headline tiles and yields on the Income page; the
risk profile is quiet aggregation errors (wrong window, cross-user leaks,
yield division) rather than crashes.
"""

from datetime import date, timedelta

import pytest

import income_db

USER = "user-a"
TODAY = date(2026, 7, 7)


@pytest.fixture()
def db_file(tmp_path):
    path = tmp_path / "portfolio.db"
    income_db.ensure_schema(path)
    with income_db.connect(path) as conn:
        conn.execute("""
            CREATE TABLE holdings (id TEXT PRIMARY KEY, user_id TEXT, name TEXT,
                ticker TEXT, value_eur REAL)
        """)
        conn.execute("INSERT INTO holdings VALUES ('h1', ?, 'Vanguard All-World', 'VWCE', 20000.0)", (USER,))
    return path


def add(db_file, amount, on, source_type="Dividend", name="VWCE payout", **kwargs):
    return income_db.add_income(USER, source_type, name, amount, on, db_file=db_file, **kwargs)


def test_add_list_delete_roundtrip(db_file):
    event_id = add(db_file, 120.0, date(2026, 6, 1), notes="Q2")
    events = income_db.income_events(USER, db_file=db_file)
    assert len(events) == 1
    assert events[0]["amount_eur"] == 120.0
    assert events[0]["income_date"][:10] == "2026-06-01"

    income_db.delete_income(USER, event_id, db_file=db_file)
    assert income_db.income_events(USER, db_file=db_file) == []


def test_validation(db_file):
    with pytest.raises(ValueError):
        add(db_file, 100.0, TODAY, source_type="Salary")  # not an income type
    with pytest.raises(ValueError):
        add(db_file, 0.0, TODAY)


def test_summary_windows(db_file):
    add(db_file, 100.0, TODAY - timedelta(days=30))            # in both windows
    add(db_file, 50.0, date(2026, 1, 15), source_type="Interest")  # this year + trailing
    add(db_file, 999.0, TODAY - timedelta(days=400))           # outside both

    summary = income_db.income_summary(USER, today=TODAY, db_file=db_file)
    assert summary["trailing_12m"] == pytest.approx(150.0)
    assert summary["this_year"] == pytest.approx(150.0)
    assert summary["monthly_avg"] == pytest.approx(12.5)
    assert summary["by_type"] == {"Dividend": 100.0, "Interest": 50.0}


def test_summary_scoped_to_user(db_file):
    add(db_file, 100.0, TODAY - timedelta(days=10))
    income_db.add_income("user-b", "Rent", "Flat", 1000.0, TODAY, db_file=db_file)
    assert income_db.income_summary(USER, today=TODAY, db_file=db_file)["trailing_12m"] == 100.0


def test_income_by_month_groups_and_sums(db_file):
    add(db_file, 60.0, date(2026, 6, 5))
    add(db_file, 40.0, date(2026, 6, 20))
    add(db_file, 30.0, date(2026, 6, 25), source_type="Interest")
    rows = income_db.income_by_month(USER, months=12, today=TODAY, db_file=db_file)
    assert {"month": "2026-06", "source_type": "Dividend", "amount_eur": 100.0} in rows
    assert {"month": "2026-06", "source_type": "Interest", "amount_eur": 30.0} in rows


def test_dividend_yields_from_linked_holding(db_file):
    add(db_file, 300.0, TODAY - timedelta(days=100), holding_id="h1")
    add(db_file, 100.0, TODAY - timedelta(days=10), holding_id="h1")
    add(db_file, 500.0, TODAY - timedelta(days=10))  # unlinked — excluded

    yields = income_db.dividend_yields(USER, db_file=db_file)
    assert len(yields) == 1
    assert yields[0]["ticker"] == "VWCE"
    assert yields[0]["income_12m"] == pytest.approx(400.0)
    assert yields[0]["yield_percent"] == pytest.approx(2.0)  # 400 / 20,000
