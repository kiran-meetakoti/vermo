"""Shared portfolio domain logic: FX settings, daily snapshots, price refresh.

Extracted from main.py so the Streamlit app can refresh prices in-process
instead of calling the FastAPI service over HTTP — a hosted Streamlit
deployment (Community Cloud) runs without a localhost API. main.py keeps
thin wrappers so its routes and tests are unchanged.

No Streamlit/FastAPI imports. Works on dict-shaped holding rows (sqlite3.Row
or psycopg dict_row), not the pydantic model. Callers pass `connect_fn` so
each process keeps its own backend/test wiring (main.py's monkeypatchable
DATABASE_FILE, tests' tmp files).
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from threading import Lock
from typing import Callable, Optional
from uuid import uuid4

import market_data

DEFAULT_FX_RATES = {"EUR": 1.0, "INR": 97.3, "USD": 1.14, "GBP": 0.86}

# Serializes refresh runs within one process. (Cross-process serialization has
# never existed — acceptable while refresh is a manual button; the Stage 3
# scheduled-job refresh removes the concurrency question entirely.)
_refresh_lock = Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def inferred_invested(value_eur: float, return_percent: float) -> float:
    return round(value_eur / (1 + return_percent / 100), 2) if return_percent > -100 else value_eur


def load_fx_rates(connection) -> dict[str, float]:
    rows = connection.execute("SELECT key, value FROM settings WHERE key LIKE 'fx_%'").fetchall()
    rates = {row["key"].replace("fx_", ""): float(row["value"]) for row in rows}
    return {**DEFAULT_FX_RATES, **rates}


def set_fx_rates(connection, rates: dict[str, float]) -> None:
    for currency, rate in rates.items():
        connection.execute(
            """
            INSERT INTO settings VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (f"fx_{currency}", str(rate), utc_now()),
        )


def record_snapshots(connection, user_id: str, snapshot_date: Optional[str] = None) -> None:
    """Upsert today's net-worth snapshot per market (at most one row per
    user/day/market — the performance chart's data source)."""
    snapshot_date = snapshot_date or date.today().isoformat()
    rows = connection.execute("SELECT * FROM holdings WHERE user_id = ?", (user_id,)).fetchall()
    for market in ("All", "India", "Global"):
        holdings = rows if market == "All" else [row for row in rows if row["market"] == market]
        net_worth = round(sum(row["value_eur"] for row in holdings), 2)
        invested = round(sum(row["invested_eur"] or 0 for row in holdings), 2)
        connection.execute(
            """
            INSERT INTO snapshots (user_id, snapshot_date, market, net_worth_eur, invested_eur, holdings_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, snapshot_date, market) DO UPDATE SET
                net_worth_eur = excluded.net_worth_eur,
                invested_eur = excluded.invested_eur,
                holdings_count = excluded.holdings_count,
                created_at = excluded.created_at
            """,
            (user_id, snapshot_date, market, net_worth, invested, len(holdings), utc_now()),
        )


def holding_symbol(holding: dict) -> Optional[str]:
    """The Yahoo symbol for a holding: the per-row mapping chosen via search
    wins; the curated maps in market_data are the fallback for holdings
    created before symbol search existed."""
    return holding.get("yahoo_symbol") or market_data.yahoo_symbol(holding["ticker"], holding["market"])


def refresh_prices(user_id: str, connect_fn: Callable) -> dict:
    """Refresh FX and delayed quotes for one user's holdings; returns the run
    summary that also gets written to refresh_runs. Moved verbatim from
    main.py's /api/prices/refresh route (holdings as dict rows instead of the
    pydantic model)."""
    details = []
    refreshed = 0
    skipped = 0
    failed = 0
    with _refresh_lock, connect_fn() as connection:
        fx_rates = load_fx_rates(connection)
        previous_fx_rates = dict(fx_rates)
        try:
            fx_rates = market_data.fetch_reference_fx()
            set_fx_rates(connection, fx_rates)
            details.append({"type": "fx", "status": "refreshed", "message": f'EUR/INR reference rate: {fx_rates["INR"]:.4f}'})
        except Exception as error:
            details.append({"type": "fx", "status": "failed", "message": f"FX refresh failed; retained previous rate. {error}"})

        holdings = [
            dict(row) for row in connection.execute(
                "SELECT * FROM holdings WHERE user_id = ?", (user_id,)
            ).fetchall()
        ]
        symbols = {
            symbol for holding in holdings
            if (symbol := holding_symbol(holding)) and holding["quantity"] is not None
        }
        quotes = {}
        quote_errors = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(market_data.fetch_yahoo_quote, symbol): symbol for symbol in symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    quotes[symbol] = future.result()
                except Exception as error:
                    quote_errors[symbol] = str(error)
        for holding in holdings:
            invested_eur = holding["invested_eur"] or inferred_invested(holding["value_eur"], holding["return_percent"])
            if holding["source_currency"] == "INR":
                if holding["quantity"] is not None and holding["average_cost"] is not None:
                    invested_eur = holding["quantity"] * holding["average_cost"] / fx_rates["INR"]
                else:
                    invested_eur *= previous_fx_rates["INR"] / fx_rates["INR"]
                if holding["quantity"] is not None and holding["current_price"] is not None:
                    fx_value_eur = holding["quantity"] * holding["current_price"] / fx_rates["INR"]
                else:
                    fx_value_eur = holding["value_eur"] * previous_fx_rates["INR"] / fx_rates["INR"]
                fx_return = (fx_value_eur / invested_eur - 1) * 100 if invested_eur else 0
                connection.execute(
                    "UPDATE holdings SET value_eur = ?, invested_eur = ?, return_percent = ?, updated_at = ? WHERE id = ?",
                    (round(fx_value_eur, 2), round(invested_eur, 2), round(fx_return, 2), utc_now(), holding["id"]),
                )
            symbol = holding_symbol(holding)
            if not symbol or holding["quantity"] is None:
                skipped += 1
                details.append({"ticker": holding["ticker"], "status": "skipped", "message": "FX updated; no verified live-quote mapping."})
                continue
            try:
                if symbol in quote_errors:
                    raise ValueError(quote_errors[symbol])
                quote, quote_currency = quotes[symbol]
                if quote_currency not in fx_rates:
                    raise ValueError(f"Unsupported quote currency: {quote_currency}")
                price_eur = quote / fx_rates[quote_currency]
                value_eur = holding["quantity"] * price_eur
                return_percent = (value_eur / invested_eur - 1) * 100 if invested_eur else 0
                displayed_price = quote if holding["market"] == "India" else price_eur
                connection.execute(
                    """
                    UPDATE holdings
                    SET current_price = ?, value_eur = ?, return_percent = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (round(displayed_price, 4), round(value_eur, 2), round(return_percent, 2), utc_now(), holding["id"]),
                )
                refreshed += 1
            except Exception as error:
                failed += 1
                details.append({"ticker": holding["ticker"], "symbol": symbol, "status": "failed", "message": str(error)})

        record_snapshots(connection, user_id=user_id)
        run = {
            "id": str(uuid4()),
            "refreshed": refreshed,
            "skipped": skipped,
            "failed": failed,
            "fx_rate_inr": fx_rates["INR"],
            "details": details,
            "created_at": utc_now(),
        }
        connection.execute(
            "INSERT INTO refresh_runs (id, user_id, refreshed, skipped, failed, fx_rate_inr, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run["id"], user_id, refreshed, skipped, failed, run["fx_rate_inr"], json.dumps(details), run["created_at"]),
        )
    return run


# ── History backfill ─────────────────────────────────────────────────────────

def build_daily_series(
    positions: list[dict],
    histories: dict[str, tuple[dict[str, float], str]],
    fx_by_day: dict[str, dict[str, float]],
) -> dict[str, dict[str, tuple[float, float, int]]]:
    """Pure core of the backfill: daily {date: {market: (net_worth_eur,
    invested_eur, count)}} from per-symbol close histories and EUR-base FX.

    positions: holding dicts (market, quantity, value_eur, invested_eur, and
    holding_symbol() resolvable). Positions with a symbol+quantity are valued
    at close/fx per day (forward-filling closes and FX over weekends and
    holidays); everything else contributes its CURRENT value as a constant —
    an approximation, but it keeps totals honest relative to today.
    Days before a symbol's first close are skipped for that symbol (listing
    date); invested is today's cost basis held constant (purchase dates are
    not tracked here — XIRR work will refine this).
    """
    all_days = sorted({day for closes, _ in histories.values() for day in closes})
    if not all_days:
        return {}

    fx_days = sorted(fx_by_day)
    series: dict[str, dict[str, tuple[float, float, int]]] = {}
    last_close: dict[str, float] = {}
    last_fx: dict[str, float] = dict(DEFAULT_FX_RATES)
    fx_index = 0

    for day in all_days:
        # Forward-fill FX up to this day.
        while fx_index < len(fx_days) and fx_days[fx_index] <= day:
            last_fx.update(fx_by_day[fx_days[fx_index]])
            fx_index += 1
        totals: dict[str, list[float]] = {"All": [0.0, 0.0, 0], "India": [0.0, 0.0, 0], "Global": [0.0, 0.0, 0]}
        for position in positions:
            symbol = holding_symbol(position)
            valued = None
            if symbol and symbol in histories and position.get("quantity") is not None:
                closes, currency = histories[symbol]
                if day in closes:
                    last_close[symbol] = closes[day]
                if symbol in last_close:
                    rate = last_fx.get(currency, 1.0) if currency != "EUR" else 1.0
                    valued = position["quantity"] * last_close[symbol] / rate
            if valued is None:
                # No history for this instrument (or before its listing):
                # contribute today's value as a constant.
                valued = position["value_eur"]
            invested = position["invested_eur"] or 0.0
            for market in ("All", position["market"]):
                if market in totals:
                    totals[market][0] += valued
                    totals[market][1] += invested
                    totals[market][2] += 1
        series[day] = {
            market: (round(net, 2), round(invested, 2), count)
            for market, (net, invested, count) in totals.items()
        }
    return series


def backfill_history(user_id: str, connect_fn: Callable, range_: str = "1y") -> dict:
    """Populate past daily snapshots from Yahoo close history + ECB FX, so a
    new user sees a real performance chart immediately instead of waiting
    weeks for daily snapshots to accumulate.

    Never overwrites genuinely recorded snapshots (ON CONFLICT DO NOTHING) —
    today's live snapshot and any historical real ones always win."""
    with connect_fn() as connection:
        holdings = [dict(row) for row in connection.execute(
            "SELECT * FROM holdings WHERE user_id = ?", (user_id,)
        ).fetchall()]
    if not holdings:
        return {"days": 0, "symbols": 0, "inserted": 0}

    symbols = {s for h in holdings if (s := holding_symbol(h)) and h.get("quantity") is not None}
    histories: dict[str, tuple[dict[str, float], str]] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(market_data.fetch_yahoo_history, symbol, range_): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                histories[symbol] = future.result()
            except Exception:
                pass  # symbol contributes its current value as a constant instead

    all_days = sorted({day for closes, _ in histories.values() for day in closes})
    if not all_days:
        return {"days": 0, "symbols": 0, "inserted": 0}
    try:
        fx_by_day = market_data.fetch_fx_timeseries(all_days[0], all_days[-1])
    except Exception:
        fx_by_day = {}

    series = build_daily_series(holdings, histories, fx_by_day)
    today = date.today().isoformat()
    inserted = 0
    with connect_fn() as connection:
        for day, markets in series.items():
            if day >= today:
                continue  # today belongs to the live record_snapshots path
            for market, (net_worth, invested, count) in markets.items():
                cursor = connection.execute(
                    "INSERT INTO snapshots (user_id, snapshot_date, market, net_worth_eur, invested_eur, holdings_count, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(user_id, snapshot_date, market) DO NOTHING",
                    (user_id, day, market, net_worth, invested, count, utc_now()),
                )
                inserted += max(cursor.rowcount, 0)
    return {"days": len(series), "symbols": len(histories), "inserted": inserted}
