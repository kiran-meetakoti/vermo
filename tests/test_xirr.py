"""Tests for XIRR: the solver (finance_math.xirr) against Excel-verified
values, and the snapshot→cash-flow derivation (portfolio_core).

This number renders on the Overview header as the portfolio's true annual
return — a wrong sign or a diverged solver would be worse than showing
nothing, which is why the solver returns None instead of guessing.
"""

from datetime import date

import pytest

import db
import portfolio_core
from finance_math import xirr


# ── Solver vs known values ───────────────────────────────────────────────────

def test_xirr_simple_one_year_gain():
    flows = [(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), 1100.0)]
    assert xirr(flows) == pytest.approx(0.10, abs=1e-4)


def test_xirr_excel_documentation_example():
    # Microsoft's canonical XIRR example; Excel returns 0.373362535.
    flows = [
        (date(2008, 1, 1), -10_000.0),
        (date(2008, 3, 1), 2_750.0),
        (date(2008, 10, 30), 4_250.0),
        (date(2009, 2, 15), 3_250.0),
        (date(2009, 4, 1), 2_750.0),
    ]
    assert xirr(flows) == pytest.approx(0.373362535, abs=1e-4)


def test_xirr_loss():
    flows = [(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), 800.0)]
    assert xirr(flows) == pytest.approx(-0.20, abs=1e-4)


def test_xirr_monthly_contributions_beat_naive_percent():
    # 12 monthly 100€ contributions ending worth 1,260€: naive P/L is +5%,
    # but the money was only invested ~half the year on average — the true
    # annualized rate is roughly double that. XIRR must reflect it.
    flows = [(date(2025, m, 1), -100.0) for m in range(1, 13)]
    flows.append((date(2025, 12, 31), 1260.0))
    rate = xirr(flows)
    assert rate is not None and 0.09 < rate < 0.13


def test_xirr_degenerate_inputs_return_none():
    assert xirr([]) is None
    assert xirr([(date(2025, 1, 1), -1000.0)]) is None
    assert xirr([(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), -500.0)]) is None  # one-signed
    assert xirr([(date(2025, 1, 1), 0.0), (date(2026, 1, 1), 0.0)]) is None


def test_xirr_total_loss_is_solvable():
    flows = [(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), 1.0)]
    rate = xirr(flows)
    assert rate is not None and rate < -0.9


# ── Snapshot → cash-flow derivation ──────────────────────────────────────────

def snap(day, net, invested):
    return {"snapshot_date": day, "net_worth_eur": net, "invested_eur": invested}


def test_snapshot_cash_flows_opening_contribution_and_terminal_value():
    flows = portfolio_core.snapshot_cash_flows([
        snap("2025-01-01", 10_000.0, 10_000.0),
        snap("2025-06-01", 11_000.0, 10_000.0),
        snap("2025-12-31", 12_000.0, 10_000.0),
    ])
    assert flows == [
        (date(2025, 1, 1), -10_000.0),
        (date(2025, 12, 31), 12_000.0),
    ]


def test_snapshot_cash_flows_detects_contributions_and_withdrawals():
    flows = portfolio_core.snapshot_cash_flows([
        snap("2025-01-01", 10_000.0, 10_000.0),
        snap("2025-06-01", 13_500.0, 13_000.0),   # +3,000 contributed
        snap("2025-09-01", 12_400.0, 12_000.0),   # -1,000 withdrawn
        snap("2025-12-31", 13_000.0, 12_000.0),
    ])
    assert flows == [
        (date(2025, 1, 1), -10_000.0),
        (date(2025, 6, 1), -3_000.0),
        (date(2025, 9, 1), 1_000.0),
        (date(2025, 12, 31), 13_000.0),
    ]


def test_snapshot_cash_flows_too_short():
    assert portfolio_core.snapshot_cash_flows([snap("2025-01-01", 1.0, 1.0)]) == []


def test_portfolio_xirr_end_to_end(tmp_path):
    db_file = tmp_path / "portfolio.db"
    with db.connect(db_file) as conn:
        conn.execute("""
            CREATE TABLE snapshots (user_id TEXT, snapshot_date TEXT, market TEXT,
                net_worth_eur REAL, invested_eur REAL, holdings_count INTEGER, created_at TEXT,
                PRIMARY KEY (user_id, snapshot_date, market))
        """)
        rows = [
            ("u1", "2025-01-01", "All", 10_000.0, 10_000.0, 1, "t"),
            ("u1", "2026-01-01", "All", 11_000.0, 10_000.0, 1, "t"),
            # other user's rows must not leak in
            ("u2", "2025-01-01", "All", 99_999.0, 1.0, 1, "t"),
        ]
        conn.executemany("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    rate = portfolio_core.portfolio_xirr("u1", connect_fn=lambda: db.connect(db_file))
    assert rate == pytest.approx(0.10, abs=1e-3)


def test_portfolio_xirr_requires_90_days(tmp_path):
    db_file = tmp_path / "portfolio.db"
    with db.connect(db_file) as conn:
        conn.execute("""
            CREATE TABLE snapshots (user_id TEXT, snapshot_date TEXT, market TEXT,
                net_worth_eur REAL, invested_eur REAL, holdings_count INTEGER, created_at TEXT,
                PRIMARY KEY (user_id, snapshot_date, market))
        """)
        conn.executemany("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("u1", "2026-01-01", "All", 10_000.0, 10_000.0, 1, "t"),
            ("u1", "2026-02-01", "All", 10_500.0, 10_000.0, 1, "t"),
        ])
    assert portfolio_core.portfolio_xirr("u1", connect_fn=lambda: db.connect(db_file)) is None
