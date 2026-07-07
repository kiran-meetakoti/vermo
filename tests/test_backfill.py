"""Tests for symbol resolution, market-data parsing, and history backfill.

The backfill writes historical snapshot rows — wrong math here means a wrong
performance chart that looks authoritative. The pure series builder is tested
directly; the DB layer is tested for its one hard guarantee: recorded (real)
snapshots are never overwritten.
"""

from datetime import date, timedelta

import pytest

import db
import market_data
import portfolio_core


# ── Symbol resolution precedence ─────────────────────────────────────────────

def test_holding_symbol_prefers_per_row_mapping():
    holding = {"ticker": "RELINDEQ", "market": "India", "yahoo_symbol": "RELIANCE.BO"}
    assert portfolio_core.holding_symbol(holding) == "RELIANCE.BO"


def test_holding_symbol_falls_back_to_curated_map():
    holding = {"ticker": "RELINDEQ", "market": "India", "yahoo_symbol": None}
    assert portfolio_core.holding_symbol(holding) == "RELIANCE.NS"


def test_holding_symbol_none_for_unmapped():
    holding = {"ticker": "SOMETHING-NEW", "market": "Global"}  # no yahoo_symbol key at all
    assert portfolio_core.holding_symbol(holding) is None


# ── Yahoo/Frankfurter response parsing ───────────────────────────────────────

def test_search_symbols_parses_and_limits(monkeypatch):
    monkeypatch.setattr(market_data, "fetch_json", lambda url: {
        "quotes": [
            {"symbol": "VWCE.AS", "shortname": "Vanguard FTSE All-World", "exchDisp": "Amsterdam", "typeDisp": "ETF"},
            {"symbol": "VWCE.DE", "longname": "Vanguard FTSE All-World UCITS", "exchDisp": "XETRA", "typeDisp": "ETF"},
            {"noSymbol": True},
        ],
    })
    results = market_data.search_symbols("vwce", limit=2)
    assert [r["symbol"] for r in results] == ["VWCE.AS", "VWCE.DE"]
    assert results[0]["name"] == "Vanguard FTSE All-World"
    assert results[1]["name"] == "Vanguard FTSE All-World UCITS"  # longname fallback


def test_fetch_yahoo_history_drops_null_closes(monkeypatch):
    day1, day2 = 1750982400, 1751068800  # consecutive UTC days
    monkeypatch.setattr(market_data, "fetch_json", lambda url: {
        "chart": {"result": [{
            "timestamp": [day1, day2],
            "indicators": {"quote": [{"close": [100.5, None]}]},
            "meta": {"currency": "USD"},
        }]},
    })
    series, currency = market_data.fetch_yahoo_history("NVDA")
    assert currency == "USD"
    assert list(series.values()) == [100.5]


# ── Pure daily-series math ───────────────────────────────────────────────────

POSITIONS = [
    # mapped Global holding quoted in USD
    {"ticker": "X", "market": "Global", "yahoo_symbol": "XX", "quantity": 10.0,
     "value_eur": 999.0, "invested_eur": 800.0},
    # unmapped holding: contributes its current value as a constant
    {"ticker": "MANUAL", "market": "India", "yahoo_symbol": None, "quantity": None,
     "value_eur": 500.0, "invested_eur": 400.0},
]


def test_build_daily_series_converts_and_forward_fills():
    histories = {"XX": ({"2026-01-05": 100.0, "2026-01-07": 110.0}, "USD")}
    fx = {"2026-01-05": {"USD": 1.25}}  # nothing on the 6th/7th → forward-fill
    series = portfolio_core.build_daily_series(POSITIONS, histories, fx)

    assert sorted(series) == ["2026-01-05", "2026-01-07"]
    # 10 × 100 / 1.25 = 800 plus the 500 constant
    net, invested, count = series["2026-01-05"]["All"]
    assert net == pytest.approx(1300.0)
    assert invested == pytest.approx(1200.0)
    assert count == 2
    # close moved to 110, FX forward-filled at 1.25 → 880 + 500
    assert series["2026-01-07"]["All"][0] == pytest.approx(1380.0)
    # market split: the unmapped India holding only appears under India
    assert series["2026-01-07"]["India"][0] == pytest.approx(500.0)
    assert series["2026-01-07"]["Global"][0] == pytest.approx(880.0)


def test_build_daily_series_before_listing_uses_constant():
    histories = {"XX": ({"2026-01-07": 110.0}, "USD")}
    fx = {"2026-01-05": {"USD": 1.10}}
    # A second symbol provides an earlier day than XX's first close.
    positions = POSITIONS + [{"ticker": "Y", "market": "Global", "yahoo_symbol": "YY",
                              "quantity": 1.0, "value_eur": 50.0, "invested_eur": 50.0}]
    histories["YY"] = ({"2026-01-05": 55.0}, "EUR")
    series = portfolio_core.build_daily_series(positions, histories, fx)
    # On the 5th, XX has no close yet → contributes its current value_eur.
    assert series["2026-01-05"]["All"][0] == pytest.approx(999.0 + 500.0 + 55.0)


def test_build_daily_series_empty_histories():
    assert portfolio_core.build_daily_series(POSITIONS, {}, {}) == {}


# ── DB integration: never overwrite real snapshots ───────────────────────────

@pytest.fixture()
def portfolio_db(tmp_path):
    db_file = tmp_path / "portfolio.db"
    with db.connect(db_file) as conn:
        conn.execute("""
            CREATE TABLE holdings (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, ticker TEXT,
                market TEXT, value_eur REAL, return_percent REAL, asset_class TEXT,
                quantity REAL, average_cost REAL, current_price REAL, invested_eur REAL,
                source_currency TEXT, updated_at TEXT, asset_category TEXT, cap_bucket TEXT,
                barbell_role TEXT, barbell_reason TEXT, yahoo_symbol TEXT)
        """)
        conn.execute("""
            CREATE TABLE snapshots (user_id TEXT, snapshot_date TEXT, market TEXT,
                net_worth_eur REAL, invested_eur REAL, holdings_count INTEGER, created_at TEXT,
                PRIMARY KEY (user_id, snapshot_date, market))
        """)
        conn.execute(
            "INSERT INTO holdings (id, user_id, name, ticker, market, value_eur, return_percent, "
            "asset_class, quantity, invested_eur, source_currency, updated_at, yahoo_symbol) "
            "VALUES ('h1', 'u1', 'X', 'X', 'Global', 999.0, 0, 'Equities', 10.0, 800.0, 'EUR', 't', 'XX')"
        )
    return db_file


def test_backfill_inserts_history_but_never_overwrites(portfolio_db, monkeypatch):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    two_days_ago = (date.today() - timedelta(days=2)).isoformat()
    monkeypatch.setattr(market_data, "fetch_yahoo_history",
                        lambda symbol, range_="1y": ({two_days_ago: 100.0, yesterday: 110.0}, "EUR"))
    monkeypatch.setattr(market_data, "fetch_fx_timeseries", lambda start, end: {})

    connect_fn = lambda: db.connect(portfolio_db)
    # A REAL recorded snapshot already exists for yesterday.
    with connect_fn() as conn:
        conn.execute(
            "INSERT INTO snapshots VALUES ('u1', ?, 'All', 12345.0, 800.0, 1, 't')", (yesterday,)
        )

    result = portfolio_core.backfill_history("u1", connect_fn)
    assert result["symbols"] == 1
    assert result["inserted"] > 0

    with connect_fn() as conn:
        rows = {(r["snapshot_date"], r["market"]): r["net_worth_eur"] for r in
                conn.execute("SELECT * FROM snapshots WHERE user_id = 'u1'").fetchall()}
    assert rows[(two_days_ago, "All")] == pytest.approx(1000.0)   # 10 × 100 backfilled
    assert rows[(yesterday, "All")] == pytest.approx(12345.0)     # real snapshot untouched

    # Idempotent: a second run adds nothing.
    assert portfolio_core.backfill_history("u1", connect_fn)["inserted"] == 0
