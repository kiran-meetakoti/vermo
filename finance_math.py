"""Pure financial math extracted from streamlit_app.py: portfolio growth
projections, debt payoff schedules, and interest-rate inference.

No Streamlit or DB dependency, so tests can import it directly (see
docs/DEVELOPMENT.md, "The testability rule"). Functions that depend on the
current date take an optional `today` so tests can pin it; the app calls them
without it.
"""

from __future__ import annotations

import math
from datetime import date


def future_value(value: float, annual_return_percent: float, years: float) -> float:
    return value * ((1 + annual_return_percent / 100) ** years)


def months_until(target_month: int, target_year: int, today: date | None = None) -> int:
    today = today or date.today()
    return max((target_year - today.year) * 12 + target_month - today.month, 0)


def add_months(start_date: date, months: int) -> date:
    month_index = start_date.month - 1 + months
    year = start_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start_date.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def portfolio_projection(current_value: float, months: int, monthly_addition: float, one_time_addition: float, annual_return_percent: float, today: date | None = None) -> dict:
    today = today or date.today()
    monthly_rate = (1 + annual_return_percent / 100) ** (1 / 12) - 1 if annual_return_percent else 0
    value = current_value + one_time_addition
    rows = []
    for month in range(1, months + 1):
        value *= 1 + monthly_rate
        value += monthly_addition
        if month <= 12 or month % 12 == 0 or month == months:
            rows.append(
                {
                    "month": month,
                    "date": add_months(today, month),
                    "value": value,
                    "contributed": one_time_addition + monthly_addition * month,
                }
            )
    contributed = one_time_addition + monthly_addition * months
    return {
        "months": months,
        "target_date": add_months(today, months),
        "value": value,
        "contributed": contributed,
        "growth": value - current_value - contributed,
        "rows": rows,
    }


def debt_projection(balance: float, scheduled_payment: float, extra_monthly: float = 0, one_time_extra: float = 0, annual_interest: float = 0, today: date | None = None) -> dict:
    today = today or date.today()
    balance = max(balance - one_time_extra, 0)
    monthly_rate = annual_interest / 100 / 12
    monthly_payment = scheduled_payment + extra_monthly
    total_paid = one_time_extra
    total_interest = 0.0
    months = 0
    schedule = []

    if balance <= 0:
        return {"months": 0, "payoff_date": today, "total_paid": total_paid, "total_interest": 0.0, "schedule": []}
    if monthly_payment <= 0:
        return {"months": math.inf, "payoff_date": None, "total_paid": total_paid, "total_interest": math.inf, "schedule": []}

    while balance > 0.01 and months < 1200:
        interest = balance * monthly_rate
        balance += interest
        payment = min(monthly_payment, balance)
        principal = max(payment - interest, 0)
        balance -= payment
        months += 1
        total_paid += payment
        total_interest += interest
        schedule.append(
            {
                "Month": months,
                "Date": add_months(today, months),
                "Payment": payment,
                "Principal": min(principal, payment),
                "Interest": interest,
                "Remaining": max(balance, 0),
            }
        )
        if interest >= monthly_payment and monthly_rate > 0:
            return {"months": math.inf, "payoff_date": None, "total_paid": math.inf, "total_interest": math.inf, "schedule": schedule}

    return {
        "months": months,
        "payoff_date": add_months(today, months),
        "total_paid": total_paid,
        "total_interest": total_interest,
        "schedule": schedule,
    }


def infer_annual_interest_rate(balance: float, payment: float, months: int) -> float:
    """Back out the annual interest rate implied by a balance, level payment,
    and remaining term, via bisection on the standard annuity formula. Returns
    0.0 when the payment doesn't even cover straight-line principal."""
    if months <= 0 or payment <= balance / months:
        return 0.0
    low = 0.0
    high = 0.05
    for _ in range(100):
        monthly_rate = (low + high) / 2
        implied_payment = balance * monthly_rate / (1 - (1 + monthly_rate) ** (-months))
        if implied_payment < payment:
            low = monthly_rate
        else:
            high = monthly_rate
    monthly_rate = (low + high) / 2
    return ((1 + monthly_rate) ** 12 - 1) * 100


def xirr(cash_flows: list[tuple[date, float]]) -> float | None:
    """Money-weighted annualized return (Excel-XIRR-compatible: actual days,
    365-day year). Sign convention: money in is negative (a purchase), money
    out / terminal value is positive.

    Returns the annual rate as a fraction (0.12 = 12% p.a.), or None when no
    meaningful rate exists (fewer than two flows, all flows one-signed, or no
    root in (-99.99%, +1000%)). Bisection, not Newton — slower but cannot
    diverge, and this renders on the Overview page where a wrong number is
    worse than no number.
    """
    flows = sorted((d, a) for d, a in cash_flows if abs(a) > 1e-9)
    if len(flows) < 2:
        return None
    if not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return None
    start = flows[0][0]
    times = [(d - start).days / 365.0 for d, _ in flows]
    amounts = [a for _, a in flows]

    def npv(rate: float) -> float:
        return sum(a / (1 + rate) ** t for a, t in zip(amounts, times))

    low, high = -0.9999, 10.0
    npv_low = npv(low)
    if npv_low * npv(high) > 0:
        return None
    for _ in range(200):
        mid = (low + high) / 2
        if npv_low * npv(mid) <= 0:
            high = mid
        else:
            low = mid
            npv_low = npv(low)
    return round((low + high) / 2, 6)


def required_monthly_contribution(current_value: float, target_value: float, months: int, annual_return_percent: float) -> float:
    """Monthly amount needed to grow current_value to target_value in
    `months`, assuming monthly compounding at the annual rate (the same
    convention portfolio_projection uses). 0.0 when the target is already
    reached or no time remains."""
    if months <= 0 or target_value <= current_value:
        return 0.0
    monthly_rate = (1 + annual_return_percent / 100) ** (1 / 12) - 1 if annual_return_percent else 0.0
    grown_current = current_value * (1 + monthly_rate) ** months
    if grown_current >= target_value:
        return 0.0
    if monthly_rate == 0.0:
        return round((target_value - current_value) / months, 2)
    annuity_factor = ((1 + monthly_rate) ** months - 1) / monthly_rate
    return round((target_value - grown_current) / annuity_factor, 2)
