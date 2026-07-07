"""Tests for finance_math.py — projection and debt-payoff math.

These numbers render directly on the Overview and Debt tracker pages; a wrong
sign or off-by-one month here shows a user a wrong payoff date or net-worth
projection with no error anywhere.
"""

import math
from datetime import date

import pytest

import finance_math as fm

TODAY = date(2026, 7, 7)


# ── add_months ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2026, 1, 15), 1, date(2026, 2, 15)),
        (date(2026, 11, 15), 3, date(2027, 2, 15)),        # year rollover
        (date(2026, 1, 31), 1, date(2026, 2, 28)),          # clamp to month end
        (date(2024, 1, 31), 1, date(2024, 2, 29)),          # leap year
        (date(2100, 1, 31), 1, date(2100, 2, 28)),          # century non-leap
        (date(2026, 5, 10), 0, date(2026, 5, 10)),
        (date(2026, 3, 31), 12, date(2027, 3, 31)),
    ],
)
def test_add_months(start, months, expected):
    assert fm.add_months(start, months) == expected


# ── future_value / months_until ─────────────────────────────────────────────

def test_future_value_compounds_annually():
    assert fm.future_value(1000, 10, 2) == pytest.approx(1210.0)
    assert fm.future_value(1000, 0, 5) == 1000


def test_months_until():
    assert fm.months_until(12, 2026, today=TODAY) == 5
    assert fm.months_until(7, 2027, today=TODAY) == 12
    assert fm.months_until(1, 2020, today=TODAY) == 0  # past target clamps to 0


# ── portfolio_projection ─────────────────────────────────────────────────────

def test_projection_zero_return_is_pure_contributions():
    p = fm.portfolio_projection(10_000, 12, 100, 500, 0, today=TODAY)
    assert p["value"] == pytest.approx(10_000 + 500 + 100 * 12)
    assert p["contributed"] == 500 + 100 * 12
    assert p["growth"] == pytest.approx(0)
    assert p["target_date"] == date(2027, 7, 7)


def test_projection_with_return_matches_annual_compounding():
    # No contributions: 12 months at the monthly-compounded equivalent of 6%/yr
    # must equal one year of annual compounding.
    p = fm.portfolio_projection(10_000, 12, 0, 0, 6, today=TODAY)
    assert p["value"] == pytest.approx(10_600.0)
    assert p["growth"] == pytest.approx(600.0)


def test_projection_rows_are_monthly_then_yearly():
    p = fm.portfolio_projection(10_000, 36, 100, 0, 5, today=TODAY)
    months = [r["month"] for r in p["rows"]]
    assert months == list(range(1, 13)) + [24, 36]
    assert p["rows"][-1]["value"] == p["value"]


# ── debt_projection ──────────────────────────────────────────────────────────

def test_debt_zero_interest_divides_evenly():
    p = fm.debt_projection(12_000, 1_000, today=TODAY)
    assert p["months"] == 12
    assert p["total_interest"] == 0
    assert p["total_paid"] == pytest.approx(12_000)
    assert p["payoff_date"] == fm.add_months(TODAY, 12)
    assert p["schedule"][-1]["Remaining"] == pytest.approx(0, abs=0.01)


def test_debt_interest_extends_term_and_costs_more():
    no_interest = fm.debt_projection(12_000, 1_000, today=TODAY)
    with_interest = fm.debt_projection(12_000, 1_000, annual_interest=12, today=TODAY)
    assert with_interest["months"] > no_interest["months"] or with_interest["total_paid"] > no_interest["total_paid"]
    assert with_interest["total_interest"] > 0


def test_debt_extra_payments_shorten_payoff():
    base = fm.debt_projection(12_000, 1_000, annual_interest=10, today=TODAY)
    extra = fm.debt_projection(12_000, 1_000, extra_monthly=500, annual_interest=10, today=TODAY)
    one_time = fm.debt_projection(12_000, 1_000, one_time_extra=6_000, annual_interest=10, today=TODAY)
    assert extra["months"] < base["months"]
    assert one_time["months"] < base["months"]
    assert one_time["total_interest"] < base["total_interest"]


def test_debt_already_paid_off():
    p = fm.debt_projection(5_000, 1_000, one_time_extra=5_000, today=TODAY)
    assert p["months"] == 0
    assert p["payoff_date"] == TODAY
    assert p["schedule"] == []


def test_debt_no_payment_never_pays_off():
    p = fm.debt_projection(5_000, 0, today=TODAY)
    assert p["months"] is math.inf
    assert p["payoff_date"] is None


def test_debt_payment_below_interest_flagged_infinite():
    # 1000/mo interest on day one vs 500/mo payment — balance only grows.
    p = fm.debt_projection(100_000, 500, annual_interest=12, today=TODAY)
    assert p["months"] is math.inf
    assert p["payoff_date"] is None


# ── infer_annual_interest_rate ───────────────────────────────────────────────

def test_infer_rate_roundtrip():
    # Payment for 10k over 24 months at 1%/mo (annuity formula), then invert.
    balance, months, monthly_rate = 10_000, 24, 0.01
    payment = balance * monthly_rate / (1 - (1 + monthly_rate) ** (-months))
    annual = fm.infer_annual_interest_rate(balance, payment, months)
    assert annual == pytest.approx(((1.01 ** 12) - 1) * 100, rel=1e-3)


def test_infer_rate_zero_when_payment_covers_no_interest():
    assert fm.infer_annual_interest_rate(12_000, 1_000, 12) == 0.0
    assert fm.infer_annual_interest_rate(12_000, 500, 0) == 0.0
