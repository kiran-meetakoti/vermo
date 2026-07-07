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
            if (symbol := market_data.yahoo_symbol(holding["ticker"], holding["market"])) and holding["quantity"] is not None
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
            symbol = market_data.yahoo_symbol(holding["ticker"], holding["market"])
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
