"""Tests for goals_db and the required-contribution math.

The Goals page tells users whether they're on track and what monthly amount
would close the gap — a wrong "on track" is a broken promise, so the status
math is pinned with a fixed today.
"""

from datetime import date

import pytest

import goals_db
from finance_math import portfolio_projection, required_monthly_contribution

USER = "user-a"
TODAY = date(2026, 7, 7)


# ── required_monthly_contribution ────────────────────────────────────────────

def test_required_contribution_zero_return_is_linear():
    assert required_monthly_contribution(10_000, 22_000, 12, 0.0) == pytest.approx(1000.0)


def test_required_contribution_roundtrips_with_projection():
    need = required_monthly_contribution(10_000, 60_000, 36, 5.0)
    projected = portfolio_projection(10_000, 36, need, 0, 5.0, today=TODAY)["value"]
    assert projected == pytest.approx(60_000, abs=1.0)


def test_required_contribution_already_reached_or_growth_suffices():
    assert required_monthly_contribution(60_000, 50_000, 12, 5.0) == 0.0
    # 10k at 10% for 10 years comfortably exceeds 12k without contributions.
    assert required_monthly_contribution(10_000, 12_000, 120, 10.0) == 0.0
    assert required_monthly_contribution(10_000, 60_000, 0, 5.0) == 0.0


# ── goals_db CRUD ────────────────────────────────────────────────────────────

@pytest.fixture()
def db_file(tmp_path):
    path = tmp_path / "portfolio.db"
    goals_db.ensure_schema(path)
    return path


def test_add_list_delete_roundtrip(db_file):
    goal_id = goals_db.add_goal(USER, "House deposit", 60_000.0, date(2028, 7, 1),
                                monthly_contribution=500.0, db_file=db_file)
    rows = goals_db.goals(USER, db_file=db_file)
    assert len(rows) == 1 and rows[0]["name"] == "House deposit"
    goals_db.delete_goal(USER, goal_id, db_file=db_file)
    assert goals_db.goals(USER, db_file=db_file) == []


def test_goals_scoped_to_user(db_file):
    goals_db.add_goal(USER, "Mine", 10_000.0, date(2027, 1, 1), db_file=db_file)
    goals_db.add_goal("user-b", "Theirs", 99_000.0, date(2027, 1, 1), db_file=db_file)
    assert [g["name"] for g in goals_db.goals(USER, db_file=db_file)] == ["Mine"]


def test_add_goal_validation(db_file):
    with pytest.raises(ValueError):
        goals_db.add_goal(USER, "  ", 10_000.0, date(2027, 1, 1), db_file=db_file)
    with pytest.raises(ValueError):
        goals_db.add_goal(USER, "Zero", 0.0, date(2027, 1, 1), db_file=db_file)


# ── goal_status ──────────────────────────────────────────────────────────────

def goal(target=60_000.0, target_date="2028-07-01", monthly=500.0, ret=5.0):
    return {"target_eur": target, "target_date": target_date,
            "monthly_contribution": monthly, "expected_return_percent": ret}


def test_status_reached():
    status = goals_db.goal_status(goal(target=50_000.0), current_total_eur=55_000.0, today=TODAY)
    assert status["reached"] and status["on_track"]
    assert status["progress_percent"] == 100.0
    assert status["required_monthly"] == 0.0


def test_status_on_track_with_contributions():
    # 40k + 800/mo for 24 months at 5% easily exceeds 60k.
    status = goals_db.goal_status(goal(monthly=800.0), current_total_eur=40_000.0, today=TODAY)
    assert status["months_left"] == 24
    assert status["on_track"] and not status["reached"]
    assert status["projected_eur"] > 60_000
    # Needing less than the planned 800/mo is exactly why it's on track.
    assert 0 < status["required_monthly"] < 800.0


def test_status_behind_names_the_gap():
    # 10k + 100/mo won't reach 60k in 24 months.
    status = goals_db.goal_status(goal(monthly=100.0), current_total_eur=10_000.0, today=TODAY)
    assert not status["on_track"]
    assert status["required_monthly"] > 100.0
    # The named amount must actually close the gap.
    projected = portfolio_projection(10_000.0, status["months_left"],
                                     status["required_monthly"], 0, 5.0, today=TODAY)["value"]
    assert projected == pytest.approx(60_000.0, abs=2.0)


def test_status_past_target_date():
    status = goals_db.goal_status(goal(target_date="2026-01-01"), current_total_eur=10_000.0, today=TODAY)
    assert status["months_left"] == 0
    assert not status["on_track"]
    assert status["projected_eur"] == 10_000.0
