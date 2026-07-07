from __future__ import annotations

import csv
import io
import math
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import budget_db
import db
from finance_math import (
    add_months,
    debt_projection,
    future_value,
    infer_annual_interest_rate,
    months_until,
    portfolio_projection,
)
from statement_parser import extract_pdf_transactions
from auth.local_auth import current_user, logout, require_login


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATABASE_FILE = DATA_DIR / "portfolio.db"
CSV_COLUMNS = ("name", "ticker", "market", "value_eur", "return_percent", "asset_class")
DEFAULT_FX_RATES = {"EUR": 1.0, "INR": 97.3, "USD": 1.14}

# Fallback default before login completes (and for bare-mode/standalone use).
# Overwritten below with the real session user's id once require_login() succeeds.
LOCAL_USER_ID = "local-user"
# Fallback defaults for a brand-new account with no debt_* row of its own yet —
# must be a genuine "no debt" empty state, not a real person's numbers. Each
# user's actual loan figures live in their own budget_settings rows (set via
# the Debt tracker page) and always take priority over these.
DEBT_ORIGINAL_PRINCIPAL_EUR = 0.0
DEBT_BALANCE_EUR = 0.0
DEBT_MONTHLY_PAYMENT_EUR = 0.0
DEBT_ORIGINAL_TERM_MONTHS = 0
DEBT_PAID_MONTHS = 0
DEBT_REMAINING_MONTHS = DEBT_ORIGINAL_TERM_MONTHS - DEBT_PAID_MONTHS
BUDGET_CATEGORIES = [
    "Rent",
    "Loan",
    "Subscriptions",
    "Utilities",
    "Groceries",
    "Transport",
    "Insurance",
    "Eating out",
    "Shopping",
    "Travel",
    "Investments",
    "Other",
]
BUDGET_CATEGORY_ICONS = {
    "Rent": "🏠",
    "Loan": "🏦",
    "Subscriptions": "🔁",
    "Utilities": "💡",
    "Groceries": "🛒",
    "Transport": "🚗",
    "Insurance": "🛡️",
    "Eating out": "🍽️",
    "Shopping": "🛍️",
    "Travel": "✈️",
    "Investments": "📈",
    "Other": "🧾",
}
BROKERS = ["HDFC", "INDmoney", "Trade Republic"]
TRANSACTION_COLUMN_ALIASES = {
    "trade_date": ("date", "trade date", "transaction date", "executed at", "time", "settlement date"),
    "transaction_type": ("type", "transaction type", "action", "side", "activity", "category"),
    "name": ("name", "asset", "instrument", "security", "company", "description", "stock name"),
    "ticker": ("ticker", "symbol", "isin", "instrument id", "wkn", "stock name"),
    "market": ("market", "exchange", "segment", "country"),
    "quantity": ("quantity", "qty", "shares", "units", "no. of shares"),
    "price": ("price", "rate", "execution price", "average price", "nav", "cmp"),
    "amount": ("amount", "value", "total", "net amount", "invested value", "turnover"),
    "fee": ("fee", "fees", "brokerage", "charges", "commission", "taxes"),
    "currency": ("currency", "ccy"),
}

MUTUAL_FUND_TICKERS = {
    "MF-QUANT-MIDCAP", "MF-PGIM-INDIA-MIDCAP", "MF-BANDHAN-NIFTY50", "MF-HELIOS-FLEXICAP",
    "MF-QUANT-SMALLCAP-1", "MF-MOTILAL-MIDCAP", "MF-QUANT-SMALLCAP-2", "MF-PPFAS-FLEXICAP",
    "MF-AXIS-SMALLCAP",
}
ETF_TICKERS = {"HDFCMFGETFEQ", "LIQBENEQ", "IE00BGV5VN51", "IE00BFMXXD54", "IE00B4ND3602", "IE00BK5BQT80"}
BARBELL_CORE_TICKERS = {"LIQBENEQ", "HDFCMFGETFEQ", "IE00B4ND3602", "IE00BFMXXD54", "IE00BK5BQT80", "MF-BANDHAN-NIFTY50", "MF-PPFAS-FLEXICAP"}
BARBELL_UPSIDE_TICKERS = {"MF-QUANT-MIDCAP", "MF-PGIM-INDIA-MIDCAP", "MF-QUANT-SMALLCAP-1", "MF-QUANT-SMALLCAP-2", "MF-AXIS-SMALLCAP", "MF-MOTILAL-MIDCAP", "IE00BGV5VN51", "US00217D1000", "US69608A1088", "US7811541090", "US26740W1099"}

CAP_BUCKETS = {
    "Large cap": {"BAJFINEQ", "ICIBANEQ", "HDFBANEQ", "KOTMAHEQ", "TCSLTDEQ", "RELINDEQ", "TRELTDEQ", "EICMOTEQ", "JIOFINEQ", "ASIPAIEQ", "ITCLTDEQ", "HLLLTDEQ", "HDFCLIFEEQ", "US67066G1040", "US5949181045", "US02079K3059", "US64110L1061", "US30303M1027", "US0231351067", "NL0010273215", "US8740391003", "US81762P1021", "US11135F1012"},
    "Mid cap": {"DIXONEQ", "NIITECEQ", "FINEORGEQ", "CLEANEQ", "RAINBOWEQ", "SAGILITYEQ", "BHELTDEQ", "ALKAMIEQ", "HOMEFIRSTEQ", "GODIGITEQ", "HDBFSEQ", "ROSSARIEQ", "US69608A1088"},
    "Small cap": {"AMIORGEQ", "RATEGAINIQ", "LUMINDEQ", "RSYINTEQ", "UNIECOMEQ", "MASFINEQ", "LOGMICEQ", "SUBLTDEQ", "DCALEQ", "TARSONSIQ", "EXIINDEQ", "KNRCONEQ", "ITCHOTELSEQ", "RELFOOEQ", "KWILEQ", "US00217D1000", "US7811541090", "US26740W1099"},
}
CAP_BUCKET_LOOKUP: dict[str, str] = {ticker: bucket for bucket, tickers in CAP_BUCKETS.items() for ticker in tickers}


st.set_page_config(page_title="Vermo", page_icon="V", layout="wide")

# Blocks (via st.stop()) until the user logs in or signs up. Every function below
# that reads LOCAL_USER_ID does so as a module global resolved at call time, so
# reassigning it here after a successful login scopes every query that follows
# for the rest of this script run.
_session_user = require_login()
LOCAL_USER_ID = _session_user["id"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect():
    """Configured backend: Postgres when VERMO_BACKEND=postgres, else the
    local SQLite file (see db.py)."""
    if db.is_postgres():
        return db.connect()
    return db.connect(DATABASE_FILE)


def load_rows(query: str, params: tuple = ()) -> list[dict]:
    with connect() as connection:
        return [dict(row) for row in connection.execute(query, params).fetchall()]


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _add_user_id_column(connection: sqlite3.Connection, table: str) -> None:
    if not _table_exists(connection, table):
        return
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if "user_id" not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT NOT NULL DEFAULT '{LOCAL_USER_ID}'")


def _rebuild_budget_settings_for_multiuser(connection: sqlite3.Connection) -> None:
    """budget_settings.PRIMARY KEY(key) predates multi-user support — keys like
    'monthly_salary' would collide across users. Rebuild as PK(user_id, key)."""
    if not _table_exists(connection, "budget_settings"):
        return
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(budget_settings)").fetchall()}
    if "user_id" in columns:
        return
    connection.execute("ALTER TABLE budget_settings RENAME TO budget_settings_old")
    connection.execute(
        """
        CREATE TABLE budget_settings (
            user_id TEXT NOT NULL DEFAULT 'local-user',
            key TEXT NOT NULL,
            value REAL NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        )
        """
    )
    connection.execute(
        f"""
        INSERT INTO budget_settings (user_id, key, value, updated_at)
        SELECT '{LOCAL_USER_ID}', key, value, updated_at FROM budget_settings_old
        """
    )
    connection.execute("DROP TABLE budget_settings_old")


@st.cache_resource
def init_budget_tables() -> None:
    if db.is_postgres():
        return  # schema managed by migrations/postgres/*.sql
    with connect() as connection:
        _rebuild_budget_settings_for_multiuser(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS budget_settings (
                user_id TEXT NOT NULL DEFAULT 'local-user',
                key TEXT NOT NULL,
                value REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, key)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS budget_expenses (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'local-user',
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                amount_eur REAL NOT NULL,
                expense_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _add_user_id_column(connection, "budget_expenses")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(budget_expenses)").fetchall()}
        if "expense_date" not in columns:
            connection.execute("ALTER TABLE budget_expenses ADD COLUMN expense_date TEXT")
        if "is_recurring" not in columns:
            connection.execute("ALTER TABLE budget_expenses ADD COLUMN is_recurring INTEGER NOT NULL DEFAULT 0")
        # Backfill existing rows (added before date tracking existed) using the
        # date portion of created_at, so historical expenses still show up in
        # the weekly/monthly analytics instead of being excluded.
        connection.execute(
            "UPDATE budget_expenses SET expense_date = substr(created_at, 1, 10) WHERE expense_date IS NULL"
        )


def ensure_recurring_expenses() -> None:
    """Materialize monthly rows for recurring expenses (Rent, Loan, …).
    Delegates to budget_db so the month-rollover/idempotency logic is
    unit-tested there — subtle bugs here mean silently missing or doubled
    rent/loan rows for some month."""
    budget_db.ensure_recurring_expenses(LOCAL_USER_ID)


MANUAL_ASSET_CATEGORIES = [
    "Real estate",
    "Cash & savings",
    "Private investment",
    "Crypto",
    "Vehicles & collectibles",
    "Other",
]

@st.cache_resource
def init_manual_assets_table() -> None:
    if db.is_postgres():
        return  # schema managed by migrations/postgres/*.sql
    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_assets (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'local-user',
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                value_eur REAL NOT NULL,
                cost_eur REAL,
                currency TEXT NOT NULL DEFAULT 'EUR',
                original_value REAL,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _add_user_id_column(connection, "manual_assets")


@st.cache_resource
def init_broker_tables() -> None:
    if db.is_postgres():
        return  # schema managed by migrations/postgres/*.sql
    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS broker_imports (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'local-user',
                broker TEXT NOT NULL,
                filename TEXT NOT NULL,
                imported INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        _add_user_id_column(connection, "broker_imports")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS broker_transactions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'local-user',
                import_id TEXT NOT NULL,
                broker TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                transaction_type TEXT NOT NULL,
                name TEXT NOT NULL,
                ticker TEXT NOT NULL,
                market TEXT NOT NULL,
                quantity REAL,
                price REAL,
                amount REAL,
                fee REAL,
                currency TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        _add_user_id_column(connection, "broker_transactions")


def clean_number(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    cleaned = (
        cleaned.replace("€", "")
        .replace("$", "")
        .replace("₹", "")
        .replace(",", "")
        .replace(" ", "")
    )
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    try:
        return float(cleaned)
    except ValueError:
        return None


def normalize_transaction_type(value: str) -> str:
    normalized = value.strip().upper()
    if normalized in {"BUY", "BOUGHT", "PURCHASE", "MARKET BUY"}:
        return "BUY"
    if normalized in {"SELL", "SOLD", "MARKET SELL"}:
        return "SELL"
    if "DIV" in normalized:
        return "DIVIDEND"
    if "FEE" in normalized or "CHARGE" in normalized:
        return "FEE"
    if "DEPOSIT" in normalized:
        return "DEPOSIT"
    if "WITHDRAW" in normalized:
        return "WITHDRAWAL"
    return normalized or "UNKNOWN"


def pick_column(row: dict[str, str], aliases: tuple[str, ...]) -> str:
    normalized = {key.strip().lower(): value for key, value in row.items()}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return ""


def parse_broker_csv(uploaded_file, broker: str, default_market: str) -> list[dict]:
    text = uploaded_file.getvalue().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV file has no header row.")

    parsed = []
    for row_number, row in enumerate(reader, start=2):
        transaction_type = normalize_transaction_type(pick_column(row, TRANSACTION_COLUMN_ALIASES["transaction_type"]))
        trade_date = pick_column(row, TRANSACTION_COLUMN_ALIASES["trade_date"]) or date.today().isoformat()
        ticker = pick_column(row, TRANSACTION_COLUMN_ALIASES["ticker"]).strip().upper()
        name = pick_column(row, TRANSACTION_COLUMN_ALIASES["name"]).strip() or ticker
        if not ticker and not name:
            continue
        parsed.append(
            {
                "broker": broker,
                "trade_date": trade_date,
                "transaction_type": transaction_type,
                "name": name,
                "ticker": ticker or name.upper().replace(" ", "-")[:20],
                "market": pick_column(row, TRANSACTION_COLUMN_ALIASES["market"]).strip() or default_market,
                "quantity": clean_number(pick_column(row, TRANSACTION_COLUMN_ALIASES["quantity"])),
                "price": clean_number(pick_column(row, TRANSACTION_COLUMN_ALIASES["price"])),
                "amount": clean_number(pick_column(row, TRANSACTION_COLUMN_ALIASES["amount"])),
                "fee": clean_number(pick_column(row, TRANSACTION_COLUMN_ALIASES["fee"])) or 0.0,
                "currency": (pick_column(row, TRANSACTION_COLUMN_ALIASES["currency"]).strip().upper() or "EUR"),
            }
        )
    if not parsed:
        raise ValueError("No recognizable transaction rows found.")
    return parsed


def import_broker_transactions(uploaded_file, broker: str, default_market: str) -> int:
    transactions = parse_broker_csv(uploaded_file, broker, default_market)
    import_id = str(uuid4())
    now = utc_now()
    with connect() as connection:
        connection.execute(
            "INSERT INTO broker_imports (id, user_id, broker, filename, imported, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (import_id, LOCAL_USER_ID, broker, uploaded_file.name, len(transactions), now),
        )
        for transaction in transactions:
            connection.execute(
                """
                INSERT INTO broker_transactions (
                    id, user_id, import_id, broker, trade_date, transaction_type, name, ticker,
                    market, quantity, price, amount, fee, currency, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    str(uuid4()),
                    LOCAL_USER_ID,
                    import_id,
                    transaction["broker"],
                    transaction["trade_date"],
                    transaction["transaction_type"],
                    transaction["name"],
                    transaction["ticker"],
                    transaction["market"],
                    transaction["quantity"],
                    transaction["price"],
                    transaction["amount"],
                    transaction["fee"],
                    transaction["currency"],
                    now,
                ),
            )
    return len(transactions)




def broker_transactions(limit: int = 200) -> list[dict]:
    return load_rows(
        """
        SELECT * FROM broker_transactions
        WHERE user_id = ?
        ORDER BY trade_date DESC, created_at DESC
        LIMIT ?
        """,
        (LOCAL_USER_ID, limit),
    )


def monthly_salary() -> float:
    rows = load_rows("SELECT value FROM budget_settings WHERE key = 'monthly_salary' AND user_id = ?", (LOCAL_USER_ID,))
    return float(rows[0]["value"]) if rows else 0.0


def setting_value(key: str, default: float) -> float:
    rows = load_rows("SELECT value FROM budget_settings WHERE key = ? AND user_id = ?", (key, LOCAL_USER_ID))
    return float(rows[0]["value"]) if rows else default


def set_setting_value(key: str, value: float) -> None:
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO budget_settings (user_id, key, value, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (LOCAL_USER_ID, key, value, utc_now()),
        )


def debt_settings() -> dict[str, float]:
    original_term = int(setting_value("debt_original_term_months", DEBT_ORIGINAL_TERM_MONTHS))
    paid_months = int(setting_value("debt_paid_months", DEBT_PAID_MONTHS))
    return {
        "original_principal": setting_value("debt_original_principal_eur", DEBT_ORIGINAL_PRINCIPAL_EUR),
        "balance": setting_value("debt_balance_eur", DEBT_BALANCE_EUR),
        "monthly_payment": setting_value("debt_monthly_payment_eur", DEBT_MONTHLY_PAYMENT_EUR),
        "original_term": original_term,
        "paid_months": paid_months,
        "remaining_months": max(original_term - paid_months, 0),
        "annual_interest": setting_value("debt_annual_interest_pct", 0.0),
    }


def set_monthly_salary(value: float) -> None:
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO budget_settings (user_id, key, value, updated_at) VALUES (?, 'monthly_salary', ?, ?)
            ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (LOCAL_USER_ID, value, utc_now()),
        )


def budget_expenses(order_by_date: bool = False) -> list[dict]:
    order = "expense_date DESC, category, amount_eur DESC, name" if order_by_date else "category, amount_eur DESC, name"
    return load_rows(f"SELECT * FROM budget_expenses WHERE user_id = ? ORDER BY {order}", (LOCAL_USER_ID,))


def add_budget_expense(
    name: str, category: str, amount_eur: float, expense_date: date | None = None, is_recurring: bool = False
) -> None:
    budget_db.add_expense(LOCAL_USER_ID, name, category, amount_eur, expense_date, is_recurring)


def expense_exists(name: str, amount_eur: float, expense_date_iso: str) -> bool:
    """Skip-duplicate guard for (re)imports. Delegates to budget_db so the logic
    is unit-tested there. See budget_db.expense_exists for the matching rules."""
    return budget_db.expense_exists(LOCAL_USER_ID, name, amount_eur, expense_date_iso)


def delete_budget_expense(expense_id: str) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM budget_expenses WHERE id = ? AND user_id = ?", (expense_id, LOCAL_USER_ID))


def manual_assets() -> list[dict]:
    return load_rows("SELECT * FROM manual_assets WHERE user_id = ? ORDER BY category, value_eur DESC", (LOCAL_USER_ID,))


def upsert_manual_asset(asset_id: str | None, name: str, category: str, value_eur: float,
                        cost_eur: float | None, currency: str, original_value: float | None, notes: str) -> None:
    now = utc_now()
    with connect() as connection:
        if asset_id:
            connection.execute(
                "UPDATE manual_assets SET name=?,category=?,value_eur=?,cost_eur=?,currency=?,"
                "original_value=?,notes=?,updated_at=? WHERE id=? AND user_id=?",
                (name, category, value_eur, cost_eur, currency, original_value, notes, now, asset_id, LOCAL_USER_ID),
            )
        else:
            connection.execute(
                "INSERT INTO manual_assets (id, user_id, name, category, value_eur, cost_eur, currency, original_value, notes, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid4()), LOCAL_USER_ID, name, category, value_eur, cost_eur, currency, original_value, notes, now, now),
            )


def delete_manual_asset(asset_id: str) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM manual_assets WHERE id = ? AND user_id = ?", (asset_id, LOCAL_USER_ID))


def manual_assets_total_eur() -> float:
    rows = load_rows("SELECT COALESCE(SUM(value_eur),0) AS total FROM manual_assets WHERE user_id = ?", (LOCAL_USER_ID,))
    return float(rows[0]["total"]) if rows else 0.0


def classify_holding(ticker: str, asset_class: str) -> tuple[str, str]:
    if ticker in MUTUAL_FUND_TICKERS:
        return "Mutual fund", "Not applicable"
    if ticker in ETF_TICKERS or asset_class == "ETFs & Funds":
        return "ETF", "Not applicable"
    bucket = CAP_BUCKET_LOOKUP.get(ticker)
    if bucket:
        return "Stock", bucket
    return ("Stock", "Unclassified") if asset_class == "Equities" else ("Other", "Not applicable")


def classify_barbell(ticker: str, category: str, cap_bucket: str) -> tuple[str, str]:
    if ticker in BARBELL_CORE_TICKERS:
        return "Core", "Diversified, defensive, or liquid building block."
    if ticker in BARBELL_UPSIDE_TICKERS or (category == "Stock" and cap_bucket == "Small cap"):
        return "Upside", "Intentional higher-risk exposure with asymmetric upside potential."
    return "Review", "Does not clearly fit the core or upside side of the current heuristic."


def fx_rates() -> dict[str, float]:
    rows = load_rows("SELECT key, value FROM settings WHERE key LIKE 'fx_%'")
    return {**DEFAULT_FX_RATES, **{row["key"].replace("fx_", ""): float(row["value"]) for row in rows}}


LEGACY_USER_ID = "local-user"
LEGACY_DATA_TABLES = (
    "holdings", "imports", "snapshots", "refresh_runs",
    "budget_settings", "budget_expenses", "broker_imports",
    "broker_transactions", "manual_assets",
)


def has_legacy_data() -> bool:
    if db.is_postgres():
        # 'local-user' predates auth and isn't a uuid; legacy claiming is a
        # SQLite-era concept — migrated Postgres data is already owned.
        return False
    rows = load_rows("SELECT 1 FROM holdings WHERE user_id = ? LIMIT 1", (LEGACY_USER_ID,))
    return bool(rows)


def claim_legacy_data(new_user_id: str) -> None:
    """One-time transfer of pre-login data (tagged 'local-user') to a real account.

    Before claiming, the new account may already have placeholder rows (e.g. an
    empty snapshot auto-created on first page load) that collide on the same
    UNIQUE constraints the legacy rows use — clear those out first so the
    reassignment can't hit a UNIQUE constraint error.
    """
    with connect() as connection:
        for table in LEGACY_DATA_TABLES:
            connection.execute(f"DELETE FROM {table} WHERE user_id = ?", (new_user_id,))
            connection.execute(f"UPDATE {table} SET user_id = ? WHERE user_id = ?", (new_user_id, LEGACY_USER_ID))


def holdings() -> list[dict]:
    return load_rows("SELECT * FROM holdings WHERE user_id = ? ORDER BY value_eur DESC", (LOCAL_USER_ID,))


def latest_refresh() -> dict | None:
    rows = load_rows("SELECT * FROM refresh_runs WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (LOCAL_USER_ID,))
    return rows[0] if rows else None


def latest_updated_at() -> str | None:
    rows = load_rows(
        """
        SELECT MAX(updated_at) AS updated_at FROM (
            SELECT MAX(updated_at) AS updated_at FROM holdings WHERE user_id = ?
            UNION ALL
            SELECT MAX(created_at) AS updated_at FROM imports WHERE user_id = ?
        )
        """,
        (LOCAL_USER_ID, LOCAL_USER_ID),
    )
    return rows[0]["updated_at"] if rows and rows[0]["updated_at"] else None


def summarize(items: list[dict]) -> dict:
    total = 0.0
    invested = 0.0
    geography: dict[str, float] = {"India": 0.0, "Global": 0.0}
    asset_categories: dict[str, float] = {"Stock": 0.0, "Mutual fund": 0.0, "ETF": 0.0, "Other": 0.0}
    cap_buckets: dict[str, float] = {"Large cap": 0.0, "Mid cap": 0.0, "Small cap": 0.0, "Unclassified": 0.0}
    barbell_roles: dict[str, float] = {"Core": 0.0, "Upside": 0.0, "Review": 0.0}
    for item in items:
        v = float(item["value_eur"] or 0)
        total += v
        invested += float(item["invested_eur"] or 0)
        if item["market"] in geography:
            geography[item["market"]] += v
        if item["asset_category"] in asset_categories:
            asset_categories[item["asset_category"]] += v
        if item["asset_category"] == "Stock" and item["cap_bucket"] in cap_buckets:
            cap_buckets[item["cap_bucket"]] += v
        if item["barbell_role"] in barbell_roles:
            barbell_roles[item["barbell_role"]] += v
    stock_value = asset_categories["Stock"]

    def pct(val: float, base: float) -> float:
        return round(val / base * 100, 1) if base else 0.0

    return {
        "net_worth_eur": round(total, 2),
        "invested_eur": round(invested, 2),
        "total_returns_eur": round(total - invested, 2),
        "total_return_percent": round((total - invested) / invested * 100, 1) if invested else 0,
        "geography": {k: pct(v, total) for k, v in geography.items()},
        "asset_categories": {k: pct(v, total) for k, v in asset_categories.items()},
        "cap_buckets": {k: pct(v, stock_value) for k, v in cap_buckets.items()},
        "barbell_roles": {k: pct(v, total) for k, v in barbell_roles.items()},
    }


def record_snapshot(items: list[dict]) -> None:
    today = date.today().isoformat()
    with connect() as connection:
        existing = connection.execute(
            "SELECT 1 FROM snapshots WHERE snapshot_date = ? AND market = 'All' AND user_id = ?",
            (today, LOCAL_USER_ID),
        ).fetchone()
        if existing:
            return
        for market in ("All", "India", "Global"):
            scoped = items if market == "All" else [item for item in items if item["market"] == market]
            summary = summarize(scoped)
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
                (LOCAL_USER_ID, today, market, summary["net_worth_eur"], summary["invested_eur"], len(scoped), utc_now()),
            )


def money(value: float, currency: str, rates: dict[str, float]) -> str:
    symbols = {"EUR": "€", "USD": "$", "INR": "₹"}
    return f"{symbols[currency]}{value * rates[currency]:,.0f}"


def percent(value: float) -> str:
    return f"{'+' if value > 0 else ''}{value:.1f}%"


def projection_table_html(current_value: float, monthly_addition: float, annual_return_percent: float, currency: str, rates: dict[str, float]) -> str:
    rows = []
    for years in (1, 3, 5, 10):
        static_value = portfolio_projection(current_value, years * 12, 0, 0, annual_return_percent)
        added_value = portfolio_projection(current_value, years * 12, monthly_addition, 0, annual_return_percent)
        rows.append(
            f"<tr><td><strong>{years} year{'s' if years > 1 else ''}</strong></td>"
            f"<td>{money(static_value['value'], currency, rates)}</td>"
            f"<td>{money(added_value['contributed'], currency, rates)}</td>"
            f"<td><strong>{money(added_value['value'], currency, rates)}</strong></td>"
            f"<td class='positive'>{money(added_value['value'] - static_value['value'], currency, rates)}</td></tr>"
        )
    return "<table><thead><tr><th>Period</th><th>Without additions</th><th>New money added</th><th>With additions</th><th>Extra value</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def upsert_basic_holding(payload: dict) -> None:
    ticker = payload["ticker"].strip().upper()
    invested = payload.get("invested_eur") or round(payload["value_eur"] / (1 + payload["return_percent"] / 100), 2)
    category, cap_bucket = classify_holding(ticker, payload["asset_class"])
    role, reason = classify_barbell(ticker, category, cap_bucket)
    with connect() as connection:
        existing = connection.execute(
            "SELECT id FROM holdings WHERE ticker = ? AND market = ? AND user_id = ?",
            (ticker, payload["market"], LOCAL_USER_ID),
        ).fetchone()
        row = {
            **payload,
            "id": existing["id"] if existing else str(uuid4()),
            "user_id": LOCAL_USER_ID,
            "ticker": ticker,
            "invested_eur": invested,
            "asset_category": category,
            "cap_bucket": cap_bucket,
            "barbell_role": role,
            "barbell_reason": reason,
            "updated_at": utc_now(),
        }
        connection.execute(
            """
            INSERT INTO holdings (
                id, user_id, name, ticker, market, value_eur, return_percent, asset_class,
                quantity, average_cost, current_price, invested_eur, source_currency, updated_at,
                asset_category, cap_bucket, barbell_role, barbell_reason
            ) VALUES (
                :id, :user_id, :name, :ticker, :market, :value_eur, :return_percent, :asset_class,
                :quantity, :average_cost, :current_price, :invested_eur, :source_currency, :updated_at,
                :asset_category, :cap_bucket, :barbell_role, :barbell_reason
            )
            ON CONFLICT(user_id, ticker, market) DO UPDATE SET
                name = excluded.name,
                value_eur = excluded.value_eur,
                return_percent = excluded.return_percent,
                asset_class = excluded.asset_class,
                invested_eur = excluded.invested_eur,
                asset_category = excluded.asset_category,
                cap_bucket = excluded.cap_bucket,
                barbell_role = excluded.barbell_role,
                barbell_reason = excluded.barbell_reason,
                updated_at = excluded.updated_at
            """,
            row,
        )


def update_holding_position(holding_id: str, quantity: float, average_cost: float | None) -> None:
    """Recompute value_eur / invested_eur / return_percent after a manual share-count edit.

    India holdings store current_price/average_cost in INR; Global holdings store them in EUR.
    """
    row = load_rows("SELECT * FROM holdings WHERE id = ? AND user_id = ?", (holding_id, LOCAL_USER_ID))
    if not row:
        return
    holding = row[0]
    current_price = holding["current_price"]
    if current_price is None:
        return
    rates = fx_rates()
    if holding["market"] == "India":
        value_eur = quantity * current_price / rates["INR"]
        invested_eur = (quantity * average_cost / rates["INR"]) if average_cost else holding["invested_eur"]
    else:
        value_eur = quantity * current_price
        invested_eur = (quantity * average_cost) if average_cost else holding["invested_eur"]
    return_percent = (value_eur / invested_eur - 1) * 100 if invested_eur else 0
    with connect() as connection:
        connection.execute(
            """
            UPDATE holdings
            SET quantity = ?, average_cost = ?, value_eur = ?, invested_eur = ?, return_percent = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (quantity, average_cost, round(value_eur, 2), round(invested_eur, 2), round(return_percent, 2), utc_now(), holding_id, LOCAL_USER_ID),
        )


def import_csv(uploaded_file) -> tuple[int, int]:
    text = uploaded_file.getvalue().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not set(CSV_COLUMNS).issubset(set(reader.fieldnames or [])):
        raise ValueError(f'For Streamlit import, use columns: {", ".join(CSV_COLUMNS)}')
    imported = 0
    updated = 0
    before = {(row["ticker"], row["market"]) for row in holdings()}
    count = 0
    for row in reader:
        count += 1
        key = (row["ticker"].strip().upper(), row["market"].strip())
        upsert_basic_holding(
            {
                "name": row["name"].strip(),
                "ticker": row["ticker"].strip(),
                "market": row["market"].strip(),
                "value_eur": float(row["value_eur"]),
                "return_percent": float(row["return_percent"]),
                "asset_class": row["asset_class"].strip(),
                "quantity": None,
                "average_cost": None,
                "current_price": None,
                "source_currency": "EUR",
            }
        )
        imported += key not in before
        updated += key in before
    with connect() as connection:
        connection.execute(
            "INSERT INTO imports (id, user_id, filename, source, imported, updated, total_rows, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid4()), LOCAL_USER_ID, uploaded_file.name, "portfolio", imported, updated, count, utc_now()),
        )
    return imported, updated


def render_bars(title: str, data: dict[str, float]) -> None:
    st.markdown(f"#### {title}")
    for index, (label, value) in enumerate(data.items()):
        st.markdown(f"<div class='bar-row'><span>{label}</span><strong>{value}%</strong></div><div class='bar'><i class='bar-color-{index % 5}' style='width:{value}%'></i></div>", unsafe_allow_html=True)


def transaction_table(rows: list[dict]) -> str:
    body = []
    for row in rows:
        amount = float(row["amount"] or 0)
        fee = float(row["fee"] or 0)
        quantity = "" if row["quantity"] is None else f"{float(row['quantity']):,.4f}".rstrip("0").rstrip(".")
        body.append(
            f"<tr><td><strong>{row['trade_date']}</strong><br><small>{row['broker']}</small></td>"
            f"<td><span class='pill'>{row['transaction_type']}</span></td>"
            f"<td><strong>{row['name']}</strong><br><small>{row['ticker']}</small></td>"
            f"<td>{row['market']}</td><td>{quantity}</td><td>{row['currency']} {amount:,.2f}</td><td>{row['currency']} {fee:,.2f}</td></tr>"
        )
    return "<table><thead><tr><th>Date</th><th>Type</th><th>Asset</th><th>Market</th><th>Qty</th><th>Amount</th><th>Fees</th></tr></thead><tbody>" + "".join(body) + "</tbody></table>"


st.sidebar.markdown(
    f"""
    <div class="side-brand">
      <span class="side-brand-mark">V</span><span class="side-brand-name">Vermo</span>
      <span class="side-brand-sub">Portfolio, debt, and monthly money map</span>
    </div>
    <div style="font-size:12px;color:var(--atlas-sidebar-muted);margin:-6px 0 14px;padding:0 2px">
      Signed in as <strong style="color:var(--atlas-sidebar-text)">{_session_user["display_name"]}</strong>
    </div>
    """,
    unsafe_allow_html=True,
)
if st.sidebar.button("Log out", use_container_width=True):
    logout()
    st.rerun()
st.sidebar.caption("Display")
theme_mode = st.sidebar.selectbox("Theme", ["Linear Light", "Midnight Dark"], index=0)
currency = st.sidebar.selectbox("Base currency", ["EUR", "USD", "INR"])
st.sidebar.caption("Navigation")
page = st.sidebar.radio("View", ["Overview", "Holdings", "Other assets", "Broker imports", "Debt tracker", "Budget tracker", "Import & manage"], label_visibility="collapsed")

st.sidebar.markdown("---")
st.sidebar.caption("Live prices")
if st.sidebar.button("⟳ Refresh prices", use_container_width=True):
    with st.spinner("Fetching live quotes…"):
        try:
            import urllib.request as _ureq, json as _json
            # FastAPI derives the user from this session token (see main.py's
            # require_user_id) rather than trusting a client-supplied user_id —
            # a raw user_id in the URL used to be enough to act as anyone.
            _req = _ureq.Request(
                "http://127.0.0.1:8000/api/prices/refresh",
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {st.session_state.get('session_token', '')}",
                },
            )
            with _ureq.urlopen(_req, timeout=60) as _r:
                _run = _json.load(_r)
            st.sidebar.success(f"Updated {_run['refreshed']} · skipped {_run['skipped']} · failed {_run['failed']}")
            st.rerun()
        except Exception as _e:
            st.sidebar.error(f"Refresh failed: {_e}")

theme = {
    "bg": "#f6f8fb",
    "panel": "#ffffff",
    "ink": "#172033",
    "muted": "#667085",
    "line": "#dbe3ea",
    "sidebar_bg": "linear-gradient(180deg,#fbfdfd 0%,#eef7f4 58%,#eef3fb 100%)",
    "sidebar_text": "#182235",
    "sidebar_muted": "#667085",
    "nav_bg": "#ffffff",
    "nav_border": "#dfe8ec",
    "nav_hover": "#f3faf8",
    "table_header": "#eef3f8",
    "table_even": "#fbfcfe",
    "table_hover": "#f3f8f7",
    "metric_delta": "#16806a",
}
if theme_mode == "Midnight Dark":
    theme = {
        "bg": "#090a12",
        "panel": "#11111d",
        "ink": "#f4f0e9",
        "muted": "#9b96b5",
        "line": "#2c2e46",
        "sidebar_bg": "linear-gradient(180deg,#0b0c14 0%,#111827 58%,#151126 100%)",
        "sidebar_text": "#f4f0e9",
        "sidebar_muted": "#9b96b5",
        "nav_bg": "#11111d",
        "nav_border": "#2c2e46",
        "nav_hover": "#191b2d",
        "table_header": "#090a10",
        "table_even": "#0d0e18",
        "table_hover": "#17192a",
        "metric_delta": "#6ee48d",
    }


st.markdown(
    f"""
    <style>
    :root {{
      --atlas-bg:{theme["bg"]};--atlas-panel:{theme["panel"]};--atlas-ink:{theme["ink"]};--atlas-muted:{theme["muted"]};
      --atlas-line:{theme["line"]};--atlas-sidebar-bg:{theme["sidebar_bg"]};--atlas-sidebar-text:{theme["sidebar_text"]};--atlas-sidebar-muted:{theme["sidebar_muted"]};
      --atlas-nav-bg:{theme["nav_bg"]};--atlas-nav-border:{theme["nav_border"]};--atlas-nav-hover:{theme["nav_hover"]};
      --atlas-table-header:{theme["table_header"]};--atlas-table-even:{theme["table_even"]};--atlas-table-hover:{theme["table_hover"]};
      --atlas-metric-delta:{theme["metric_delta"]};
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <style>
    :root{
      --atlas-teal:#16806a;--atlas-blue:#2563eb;--atlas-amber:#ca8a04;
      --atlas-red:#dc2626;--atlas-violet:#7c3aed;--atlas-green:#16a34a;
      --atlas-mint:#e8f4f0;--atlas-blue-soft:#eff6ff;
    }
    /* ── Global ── */
    .stApp{background:var(--atlas-bg);color:var(--atlas-ink)}
    .block-container{padding-top:1.4rem;max-width:1380px}
    /* Streamlit's own toolbar (Deploy/Running/main menu) is for pushing this
       app to Streamlit Community Cloud — irrelevant for a locally-run personal
       app, and it always renders with Streamlit's native light theme regardless
       of the in-app theme switcher, so it looked like a stray white bar
       whenever "Midnight Dark" was selected. Hiding it removes both problems. */
    #MainMenu, header[data-testid="stHeader"], footer{visibility:hidden;height:0}
    /* The sidebar collapse/expand arrow lives outside our sidebar styling below
       (it's Streamlit's native chrome) and its icon color is hardcoded dark,
       so it disappears against a dark sidebar background in "Midnight Dark". */
    [data-testid="stSidebarCollapseButton"] svg,
    [data-testid="stSidebarCollapsedControl"] svg{fill:var(--atlas-sidebar-text) !important}
    /* ── Sidebar ── */
    section[data-testid="stSidebar"]{background:var(--atlas-sidebar-bg);border-right:1px solid var(--atlas-line)}
    section[data-testid="stSidebar"] > div{padding-top:1.25rem}
    section[data-testid="stSidebar"] h1,section[data-testid="stSidebar"] h2,section[data-testid="stSidebar"] h3,
    section[data-testid="stSidebar"] p,section[data-testid="stSidebar"] label{color:var(--atlas-sidebar-text)}
    section[data-testid="stSidebar"] .stCaption{color:var(--atlas-sidebar-muted);font-size:11px}
    section[data-testid="stSidebar"] [data-testid="stSelectbox"] label,
    section[data-testid="stSidebar"] [data-testid="stRadio"] label{color:var(--atlas-sidebar-muted);font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
    section[data-testid="stSidebar"] [data-baseweb="select"] > div{
      background:var(--atlas-nav-bg);border:1px solid var(--atlas-nav-border);border-radius:8px
    }
    section[data-testid="stSidebar"] [data-baseweb="select"] *{color:var(--atlas-ink)}
    section[data-testid="stSidebar"] [data-baseweb="select"] svg{color:var(--atlas-muted)}
    section[data-testid="stSidebar"] div[role="radiogroup"]{gap:4px}
    section[data-testid="stSidebar"] div[role="radiogroup"] label{
      background:var(--atlas-nav-bg);border:1px solid var(--atlas-nav-border);border-radius:8px;
      padding:6px 10px;margin-bottom:4px;min-height:36px;width:100%;box-sizing:border-box;display:flex;align-items:center
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover{background:var(--atlas-nav-hover);border-color:var(--atlas-teal)}
    section[data-testid="stSidebar"] div[role="radiogroup"] label p{font-size:13px;font-weight:700;line-height:1.15}
    /* ── Brand ── */
    .side-brand{background:var(--atlas-nav-bg);border:1px solid var(--atlas-nav-border);border-radius:10px;padding:10px 12px;margin:0 0 12px}
    .side-brand-mark{width:28px;height:28px;border-radius:7px;background:linear-gradient(135deg,#16806a,#1ab585);color:white;display:inline-flex;align-items:center;justify-content:center;font-weight:900;margin-right:8px;vertical-align:middle}
    .side-brand-name{font-size:15px;font-weight:800;color:var(--atlas-ink);vertical-align:middle;letter-spacing:-.3px}
    .side-brand-sub{display:block;color:var(--atlas-muted);font-size:11px;font-weight:600;margin-top:5px;line-height:1.3}
    /* ── Typography ── */
    h1,h2,h3,h4{color:var(--atlas-ink);letter-spacing:-.2px}
    h1{font-size:1.75rem;margin-bottom:.2rem;font-weight:800}
    h3{margin-top:1.2rem;font-weight:700}
    label,p,span{letter-spacing:0}
    /* ── Streamlit native widgets ── */
    div[data-testid="stMetric"]{background:var(--atlas-panel);border:1px solid var(--atlas-line);border-radius:10px;padding:14px 16px;box-shadow:0 2px 8px rgba(23,32,51,.04)}
    div[data-testid="stMetricLabel"] p{color:var(--atlas-muted);font-weight:700;font-size:.75rem;text-transform:uppercase;letter-spacing:.4px}
    div[data-testid="stMetricValue"]{color:var(--atlas-ink);font-weight:800}
    div[data-testid="stMetric"] *{color:var(--atlas-ink) !important}
    div[data-testid="stMetricLabel"] *,div[data-testid="stMetricLabel"] p{color:var(--atlas-muted) !important}
    div[data-testid="stMetricDelta"] *,div[data-testid="stMetricDelta"] svg{color:var(--atlas-metric-delta) !important;fill:var(--atlas-metric-delta) !important}
    div[data-testid="stButton"] button{background:var(--atlas-teal);border:1px solid var(--atlas-teal);color:white;border-radius:8px;font-weight:700;transition:opacity .15s}
    div[data-testid="stButton"] button:hover{opacity:.85}
    div[data-testid="stTextInput"] input,div[data-testid="stNumberInput"] input{border-color:var(--atlas-line) !important;border-radius:8px !important;background:var(--atlas-panel) !important;color:var(--atlas-ink) !important;caret-color:var(--atlas-ink) !important;opacity:1 !important}
    div[data-testid="stNumberInput"] label p,div[data-testid="stTextInput"] label p{color:var(--atlas-muted) !important;font-weight:700 !important}
    div[data-testid="stNumberInput"] button{background:var(--atlas-panel) !important;border-color:var(--atlas-line) !important;color:var(--atlas-ink) !important}
    div[data-testid="stForm"]{background:var(--atlas-panel);border:1px solid var(--atlas-line);border-radius:10px;padding:18px 20px 14px;box-shadow:0 2px 8px rgba(23,32,51,.04);margin-bottom:14px}
    div[data-testid="stForm"] div[data-testid="stFormSubmitButton"] button{width:100%;margin-top:4px}
    div[data-testid="stVerticalBlockBorderWrapper"]{border-color:var(--atlas-line) !important;background:var(--atlas-panel) !important;border-radius:10px !important}
    div[data-baseweb="select"] > div{background:var(--atlas-panel);border-color:var(--atlas-line);border-radius:8px}
    div[data-baseweb="select"] *{color:var(--atlas-ink)}
    div[data-testid="stSlider"]{background:var(--atlas-panel);border:1px solid var(--atlas-line);border-radius:10px;padding:12px 14px 8px;margin-bottom:10px}
    div[data-testid="stSlider"] label p{color:var(--atlas-muted);font-weight:700}
    div[data-testid="stSlider"] [data-baseweb="slider"] div[role="slider"]{background:var(--atlas-panel);border:2px solid var(--atlas-teal);box-shadow:0 2px 8px rgba(22,128,106,.3)}
    div[data-testid="stSlider"] [data-baseweb="slider"] > div{color:var(--atlas-teal)}
    /* ── Bar charts ── */
    .bar-row{display:flex;justify-content:space-between;margin-top:12px;font-size:13px;color:var(--atlas-muted);font-weight:600}
    .bar-row strong{color:var(--atlas-ink)}
    .bar{height:8px;background:var(--atlas-line);border-radius:999px;overflow:hidden;margin:4px 0 10px}
    .bar i{display:block;height:8px;border-radius:999px}
    .bar-color-0{background:var(--atlas-teal)}.bar-color-1{background:var(--atlas-blue)}.bar-color-2{background:var(--atlas-amber)}.bar-color-3{background:var(--atlas-violet)}.bar-color-4{background:#64748b}
    /* ── Tables ── */
    table{width:100%;border-collapse:separate;border-spacing:0;background:var(--atlas-panel);border:1px solid var(--atlas-line);border-radius:10px;overflow:hidden;font-size:13px;box-shadow:0 2px 8px rgba(23,32,51,.04)}
    th,td{border-bottom:1px solid var(--atlas-line);padding:10px 12px;text-align:left;color:var(--atlas-ink)}
    th{color:var(--atlas-muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px;background:var(--atlas-table-header);font-weight:700}
    tr:nth-child(even) td{background:var(--atlas-table-even)}
    tr:hover td{background:var(--atlas-table-hover)}
    td strong{color:var(--atlas-ink)}
    small{color:var(--atlas-muted)}
    .positive{color:var(--atlas-green);font-weight:700}.negative{color:var(--atlas-red);font-weight:700}
    /* ── Badges & pills ── */
    .pill{padding:3px 8px;border-radius:6px;background:var(--atlas-bg);border:1px solid var(--atlas-line);color:var(--atlas-muted);font-size:11px;font-weight:700}
    .badge{display:inline-block;padding:2px 8px;border-radius:5px;font-size:11px;font-weight:700}
    .badge-india{background:rgba(37,99,235,.12);color:#2563eb}
    .badge-global{background:rgba(22,163,74,.12);color:#16a34a}
    .wt-bar{display:inline-block;width:50px;height:5px;background:var(--atlas-line);border-radius:999px;overflow:hidden;vertical-align:middle;margin-right:5px}
    .wt-fill{height:5px;background:var(--atlas-blue);border-radius:999px}
    .wt-label{font-size:12px;font-weight:700;color:var(--atlas-ink);vertical-align:middle}
    /* ── Info / notice boxes ── */
    .debt-note{background:var(--atlas-panel);border:1px solid var(--atlas-line);border-left:3px solid var(--atlas-teal);color:var(--atlas-muted);border-radius:8px;padding:12px 14px;font-size:13px;margin:10px 0 14px;line-height:1.6}
    .budget-panel{background:var(--atlas-panel);border:1px solid var(--atlas-line);border-radius:10px;padding:14px 16px;box-shadow:0 2px 8px rgba(23,32,51,.04);margin-bottom:14px}
    /* ── Page header ── */
    .fd-header{display:flex;align-items:flex-start;justify-content:space-between;margin:0 0 20px;padding-bottom:16px;border-bottom:1px solid var(--atlas-line)}
    .fd-header-title{font-size:1.45rem;font-weight:800;color:var(--atlas-ink);letter-spacing:-.4px;line-height:1.2}
    .fd-header-sub{font-size:12px;color:var(--atlas-muted);margin-top:4px;font-weight:600}
    .fd-header-right{display:flex;gap:8px;align-items:center;padding-top:4px}
    .fd-tag{padding:4px 10px;border-radius:6px;font-size:11px;font-weight:700;background:var(--atlas-panel);border:1px solid var(--atlas-line);color:var(--atlas-muted)}
    .fd-tag.green{background:rgba(22,163,74,.1);border-color:rgba(22,163,74,.25);color:var(--atlas-green)}
    .fd-tag.amber{background:rgba(202,138,4,.1);border-color:rgba(202,138,4,.25);color:var(--atlas-amber)}
    .fd-tag.red{background:rgba(220,38,38,.1);border-color:rgba(220,38,38,.25);color:var(--atlas-red)}
    /* ── Metric cards ── */
    .fd-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
    .fd-metric{background:var(--atlas-panel);border:1px solid var(--atlas-line);border-radius:10px;padding:16px 18px;display:flex;flex-direction:column;gap:4px;box-shadow:0 2px 8px rgba(23,32,51,.04)}
    .fd-metric.primary{border-left:3px solid var(--atlas-blue)}
    .fd-metric.teal{border-left:3px solid var(--atlas-teal)}
    .fd-metric.amber{border-left:3px solid var(--atlas-amber)}
    .fd-metric.red{border-left:3px solid var(--atlas-red)}
    .fd-metric.green{border-left:3px solid var(--atlas-green)}
    .fd-metric-label{font-size:11px;font-weight:700;color:var(--atlas-muted);text-transform:uppercase;letter-spacing:.5px}
    .fd-metric-value{font-size:1.35rem;font-weight:800;color:var(--atlas-ink);line-height:1.1;margin-top:2px}
    .fd-metric-sub{font-size:12px;font-weight:600;color:var(--atlas-muted)}
    /* ── Allocation panels ── */
    .alloc-panel{background:var(--atlas-panel);border:1px solid var(--atlas-line);border-radius:10px;padding:16px 18px;box-shadow:0 2px 8px rgba(23,32,51,.04);height:100%}
    .ap-title{font-size:11px;font-weight:700;color:var(--atlas-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:14px}
    .ap-row{display:flex;align-items:center;gap:8px;margin-bottom:10px}
    .ap-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
    .ap-label{font-size:12px;font-weight:700;color:var(--atlas-ink);width:90px;flex-shrink:0}
    .ap-track{flex:1;height:6px;background:var(--atlas-line);border-radius:999px;overflow:hidden}
    .ap-fill{height:6px;border-radius:999px}
    .ap-pct{font-size:12px;font-weight:800;color:var(--atlas-ink);width:36px;text-align:right;flex-shrink:0}
    /* ── Detail panels ── */
    .fd-panel{background:var(--atlas-panel);border:1px solid var(--atlas-line);border-radius:10px;padding:18px 20px;box-shadow:0 2px 8px rgba(23,32,51,.04);height:100%}
    .fd-panel-title{font-size:11px;font-weight:700;color:var(--atlas-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:14px}
    .fd-kv{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--atlas-line);font-size:13px}
    .fd-kv span{color:var(--atlas-muted);font-weight:600}
    .fd-kv strong{color:var(--atlas-ink);font-weight:800}
    .fd-kv strong.green{color:var(--atlas-green)}.fd-kv strong.red{color:var(--atlas-red)}
    .fd-divider{height:1px;background:var(--atlas-line);margin:6px 0}
    .fd-progress-label{font-size:11px;color:var(--atlas-muted);font-weight:700;margin:10px 0 5px}
    .fd-progress-track{height:6px;background:var(--atlas-line);border-radius:999px;overflow:hidden}
    .fd-progress-fill{height:6px;background:var(--atlas-teal);border-radius:999px}
    /* ── Projection grid ── */
    .proj-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:4px}
    .proj-cell{background:var(--atlas-bg);border:1px solid var(--atlas-line);border-radius:8px;padding:12px 10px;text-align:center}
    .proj-horizon{display:block;font-size:11px;font-weight:700;color:var(--atlas-muted);text-transform:uppercase;letter-spacing:.5px}
    .proj-value{display:block;font-size:15px;font-weight:800;color:var(--atlas-ink);margin:6px 0 3px}
    .proj-gain{display:block;font-size:12px;font-weight:700}
    .proj-gain.green{color:var(--atlas-green)}.proj-gain.red{color:var(--atlas-red)}
    /* ── Section title ── */
    .fd-section-title{font-size:11px;font-weight:700;color:var(--atlas-muted);text-transform:uppercase;letter-spacing:.5px;margin:8px 0 14px;display:flex;align-items:center;gap:10px}
    .fd-section-title::after{content:'';flex:1;height:1px;background:var(--atlas-line)}
    /* ── Overview holdings table ── */
    .fd-table{width:100%;border-collapse:collapse;font-size:13px}
    .fd-table thead th{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--atlas-muted);padding:8px 12px;border-bottom:2px solid var(--atlas-line);background:var(--atlas-panel);text-align:left}
    .fd-table tbody td{padding:10px 12px;border-bottom:1px solid var(--atlas-line);color:var(--atlas-ink);vertical-align:middle}
    .fd-table tbody tr:last-child td{border-bottom:none}
    .fd-table tbody tr:hover td{background:var(--atlas-table-hover)}
    .fd-table .sub{color:var(--atlas-muted);font-size:11px;font-weight:600}
    .fd-table .green{color:var(--atlas-green);font-weight:700}
    .fd-table .red{color:var(--atlas-red);font-weight:700}
    /* ── Loan tracker — fully theme-aware ── */
    .loan-shell{background:var(--atlas-panel);border:1px solid var(--atlas-line);border-radius:12px;padding:24px;box-shadow:0 4px 20px rgba(23,32,51,.07)}
    .loan-kicker{color:var(--atlas-muted);letter-spacing:2px;text-transform:uppercase;font-weight:700;font-size:11px}
    .loan-title{font-size:28px;line-height:1.1;margin:8px 0;color:var(--atlas-ink);font-weight:800;letter-spacing:-.5px}
    .loan-subtitle{color:var(--atlas-muted);font-size:14px;margin-bottom:18px;font-weight:600}
    .loan-alert{background:var(--atlas-bg);border:1px solid var(--atlas-line);border-left:3px solid var(--atlas-red);border-radius:8px;padding:14px 16px;color:var(--atlas-ink);font-weight:700;margin:14px 0 18px}
    .loan-alert small{display:block;color:var(--atlas-muted);font-weight:600;margin-top:5px}
    .loan-control-card,.loan-chart-card,.loan-table-card{background:var(--atlas-bg);border:1px solid var(--atlas-line);border-radius:10px;padding:18px;margin-top:14px}
    .loan-input-strip{background:var(--atlas-bg);border:1px solid var(--atlas-line);border-radius:10px;padding:14px 16px;margin:8px 0 14px}
    .loan-input-strip .loan-kicker{margin-bottom:6px}
    .loan-input-caption{color:var(--atlas-muted);font-size:13px;font-weight:600;margin:0 0 10px}
    .loan-top-plan{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:14px 0}
    .loan-top-pill{background:var(--atlas-bg);border:1px solid var(--atlas-line);border-radius:10px;padding:14px 16px}
    .loan-top-pill span{display:block;color:var(--atlas-muted);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px}
    .loan-top-pill strong{display:block;color:var(--atlas-ink);font-size:22px;margin-top:2px;font-weight:800}
    .loan-top-pill strong.green{color:var(--atlas-green)}.loan-top-pill strong.gold{color:var(--atlas-amber)}
    .loan-stat-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:14px}
    .loan-stat,.loan-mini-stat{background:var(--atlas-bg);border-radius:10px;padding:14px 16px;border:1px solid var(--atlas-line)}
    .loan-stat span,.loan-mini-stat span{display:block;color:var(--atlas-muted);font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
    .loan-stat strong{display:block;color:var(--atlas-ink);font-size:22px;margin-top:6px;font-weight:800}
    .loan-stat strong.green,.loan-mini-stat strong.green{color:var(--atlas-green)}
    .loan-stat strong.red,.loan-mini-stat strong.red{color:var(--atlas-red)}
    .loan-mini-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:14px}
    .loan-mini-stat strong{display:block;font-size:22px;margin-top:6px;color:var(--atlas-ink);font-weight:800}
    .loan-chart{display:flex;align-items:flex-end;gap:10px;height:200px;padding:12px 0 2px;overflow-x:auto}
    .loan-bar-wrap{min-width:68px;text-align:center;color:var(--atlas-muted);font-weight:700}
    .loan-bar-label{font-size:11px;min-height:30px;color:var(--atlas-muted)}
    .loan-bar{width:50px;margin:0 auto 8px;border-radius:6px 6px 0 0;min-height:8px;background:#e34948}
    .loan-bar.mid{background:#eda100}.loan-bar.low{background:#f4c748}.loan-bar.done{background:var(--atlas-green)}
    .loan-table{background:var(--atlas-panel) !important;border-color:var(--atlas-line) !important}
    .loan-table th{background:var(--atlas-bg) !important;color:var(--atlas-muted) !important;border-bottom-color:var(--atlas-line) !important}
    .loan-table td{background:var(--atlas-panel) !important;border-bottom-color:var(--atlas-line) !important;color:var(--atlas-ink) !important}
    .loan-table tr:nth-child(even) td{background:var(--atlas-bg) !important}
    .loan-table td strong{color:var(--atlas-ink) !important}
    .loan-table .principal{color:var(--atlas-green) !important;font-weight:700}.loan-table .interest{color:var(--atlas-red) !important;font-weight:700}
    </style>
    """,
    unsafe_allow_html=True,
)

rates = fx_rates()
init_budget_tables()
ensure_recurring_expenses()
init_broker_tables()
init_manual_assets_table()

if LOCAL_USER_ID != LEGACY_USER_ID and has_legacy_data() and not holdings():
    st.info(
        "We found portfolio data from before login was enabled. "
        "Claim it to bring it into your account."
    )
    if st.button("Claim my existing portfolio data", type="primary"):
        claim_legacy_data(LOCAL_USER_ID)
        st.success("Done — your existing data is now linked to this account.")
        st.rerun()

items = holdings()
record_snapshot(items)
summary = summarize(items)
debt_config = debt_settings()
other_assets = manual_assets()
other_assets_total = sum(float(a["value_eur"]) for a in other_assets)
total_net_worth = summary["net_worth_eur"] + other_assets_total
refresh = latest_refresh()
st.sidebar.caption(
    f"Quotes: {refresh['refreshed']} refreshed, {refresh['skipped']} FX-only, {refresh['failed']} failed"
    if refresh else "Imported values are shown until prices are refreshed in the FastAPI app."
)

if page in {"Overview", "Holdings", "Import & manage"}:
    updated = latest_updated_at()
    health_score = 92 - (10 if summary["geography"]["India"] > 45 else 0) - (4 if summary["geography"]["Global"] < 20 else 0)
    health_label = "Healthy" if health_score >= 75 else "Needs attention"
    health_color = "#16a34a" if health_score >= 75 else "#dc2626"
    ret_color = "#16a34a" if summary["total_returns_eur"] >= 0 else "#dc2626"
    ret_sign  = "+" if summary["total_returns_eur"] >= 0 else ""
    updated_str = f"Updated {updated}" if updated else date.today().isoformat()
    other_pct = other_assets_total / total_net_worth * 100 if total_net_worth else 0

    health_tag_cls = "green" if health_score >= 75 else "amber"
    st.markdown(
        f"""
        <div class='fd-header'>
          <div>
            <div class='fd-header-title'>Vermo</div>
            <div class='fd-header-sub'>{updated_str}</div>
          </div>
          <div class='fd-header-right'>
            <span class='fd-tag {health_tag_cls}'>{health_label} · {max(health_score,0)}/100</span>
            <span class='fd-tag'>{len(items)} holdings</span>
            {f"<span class='fd-tag green'>{len(other_assets)} other assets</span>" if other_assets else ""}
          </div>
        </div>
        <div class='fd-metrics'>
          <div class='fd-metric primary'>
            <span class='fd-metric-label'>Total wealth</span>
            <span class='fd-metric-value'>{money(total_net_worth, currency, rates)}</span>
            <span class='fd-metric-sub'>portfolio + other assets</span>
          </div>
          <div class='fd-metric teal'>
            <span class='fd-metric-label'>Portfolio value</span>
            <span class='fd-metric-value'>{money(summary["net_worth_eur"], currency, rates)}</span>
            <span class='fd-metric-sub'>across {len(items)} positions</span>
          </div>
          <div class='fd-metric {"green" if summary["total_returns_eur"] >= 0 else "red"}'>
            <span class='fd-metric-label'>Profit / Loss</span>
            <span class='fd-metric-value' style='color:{ret_color}'>{ret_sign}{money(summary["total_returns_eur"], currency, rates)}</span>
            <span class='fd-metric-sub' style='color:{ret_color}'>{ret_sign}{summary["total_return_percent"]:.1f}% on invested</span>
          </div>
          <div class='fd-metric {"amber" if other_assets_total > 0 else ""}'>
            <span class='fd-metric-label'>Other assets</span>
            <span class='fd-metric-value'>{money(other_assets_total, currency, rates)}</span>
            <span class='fd-metric-sub'>{other_pct:.1f}% of total wealth{" · " + str(len(other_assets)) + " items" if other_assets else ""}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def holding_table(rows: list[dict]) -> str:
    total = sum(float(row["value_eur"] or 0) for row in rows)
    body = []
    for row in rows:
        pnl = float(row["value_eur"] or 0) - float(row["invested_eur"] or 0)
        body.append(
            f"<tr><td><strong>{row['name']}</strong><br><small>{row['ticker']}</small></td>"
            f"<td>{row['market']}</td><td>{row['asset_category']}</td><td>{row['cap_bucket']}</td><td><span class='pill'>{row['barbell_role']}</span></td>"
            f"<td>{money(float(row['invested_eur'] or 0), currency, rates)}</td><td><strong>{money(float(row['value_eur'] or 0), currency, rates)}</strong></td>"
            f"<td class='{'positive' if pnl >= 0 else 'negative'}'>{money(pnl, currency, rates)}</td>"
            f"<td class='{'positive' if row['return_percent'] >= 0 else 'negative'}'>{percent(float(row['return_percent'] or 0))}</td>"
            f"<td>{round(float(row['value_eur'] or 0) / total * 100) if total else 0}%</td></tr>"
        )
    return "<table><thead><tr><th>Asset</th><th>Market</th><th>Type</th><th>Cap</th><th>Barbell</th><th>Invested</th><th>Value</th><th>P/L</th><th>Return</th><th>Weight</th></tr></thead><tbody>" + "".join(body) + "</tbody></table>"


# Chart theme vars used across pages
plot_bg    = theme["panel"]
plot_paper = theme["bg"]
plot_ink   = theme["ink"]
plot_muted = theme["muted"]
plot_line  = theme["line"]

if page == "Overview":

    # ── Row 1: Portfolio performance chart + donut allocation ────────────
    chart_col, donut_col = st.columns([1.7, 1])

    with chart_col:
        snapshots = load_rows(
            "SELECT snapshot_date, net_worth_eur, invested_eur FROM snapshots "
            "WHERE market='All' AND user_id = ? ORDER BY snapshot_date",
            (LOCAL_USER_ID,),
        )
        if len(snapshots) >= 2:
            dates  = [s["snapshot_date"] for s in snapshots]
            # Add other_assets_total to every snapshot point (manual assets don't have history,
            # so we approximate by spreading today's total across all historical dates)
            values = [s["net_worth_eur"] + other_assets_total for s in snapshots]
            invested_vals = [s["invested_eur"] for s in snapshots]
            is_positive = values[-1] >= values[0]
            area_color  = "#16a34a" if is_positive else "#dc2626"
            fill_color  = "rgba(22,163,74,0.10)" if is_positive else "rgba(220,38,38,0.10)"
            fig_perf = go.Figure()
            fig_perf.add_trace(go.Scatter(
                x=dates, y=invested_vals, name="Invested",
                line=dict(color=plot_muted, width=1.5, dash="dot"),
                fill=None, hovertemplate="%{y:,.0f} EUR<extra>Invested</extra>",
            ))
            fig_perf.add_trace(go.Scatter(
                x=dates, y=values, name="Portfolio value",
                line=dict(color=area_color, width=2.5),
                fill="tonexty", fillcolor=fill_color,
                hovertemplate="%{y:,.0f} EUR<extra>Portfolio</extra>",
            ))
            fig_perf.update_layout(
                height=200, margin=dict(l=0, r=0, t=8, b=0),
                paper_bgcolor=plot_bg, plot_bgcolor=plot_bg,
                font=dict(color=plot_muted, size=11),
                xaxis=dict(showgrid=False, tickfont=dict(color=plot_muted, size=10), linecolor=plot_line, zeroline=False),
                yaxis=dict(showgrid=True, gridcolor=plot_line, tickfont=dict(color=plot_muted, size=10), zeroline=False, tickprefix="€", separatethousands=True),
                legend=dict(orientation="h", y=1.12, x=0, font=dict(color=plot_muted, size=10), bgcolor="rgba(0,0,0,0)"),
                hovermode="x unified",
            )
            st.markdown("<div class='fd-section-title'>Portfolio performance</div>", unsafe_allow_html=True)
            st.plotly_chart(fig_perf, use_container_width=True, config={"displayModeBar": False})
        else:
            st.markdown(
                "<div class='fd-panel' style='height:230px;display:flex;align-items:center;justify-content:center'>"
                "<span style='color:var(--atlas-muted);font-size:13px'>Refresh prices to build performance history</span></div>",
                unsafe_allow_html=True,
            )

    with donut_col:
        # Wealth breakdown: India portfolio / Global portfolio / Other assets
        tnw = total_net_worth or 1
        donut_labels = ["India", "Global"]
        donut_vals   = [
            summary["net_worth_eur"] * summary["geography"]["India"] / 100,
            summary["net_worth_eur"] * summary["geography"]["Global"] / 100,
        ]
        donut_colors = ["#2563eb", "#16a34a"]
        if other_assets_total > 0:
            donut_labels.append("Other assets")
            donut_vals.append(other_assets_total)
            donut_colors.append("#ca8a04")
        fig_donut = go.Figure(go.Pie(
            labels=donut_labels, values=donut_vals,
            hole=0.65,
            marker=dict(colors=donut_colors, line=dict(color=plot_bg, width=3)),
            textinfo="none",
            hovertemplate="%{label}: €%{value:,.0f} (%{percent})<extra></extra>",
        ))
        total_disp = money(total_net_worth, currency, rates)
        fig_donut.add_annotation(
            text=f"<b>{total_disp}</b><br><span style='font-size:10px'>total wealth</span>",
            x=0.5, y=0.5, showarrow=False, font=dict(color=plot_ink, size=13), align="center",
        )
        fig_donut.update_layout(
            height=200, margin=dict(l=0, r=0, t=8, b=0),
            paper_bgcolor=plot_bg, plot_bgcolor=plot_bg,
            showlegend=True,
            legend=dict(orientation="v", x=0.72, y=0.5, font=dict(color=plot_muted, size=11), bgcolor="rgba(0,0,0,0)"),
        )
        st.markdown("<div class='fd-section-title'>Wealth breakdown</div>", unsafe_allow_html=True)
        st.plotly_chart(fig_donut, use_container_width=True, config={"displayModeBar": False})

    # ── Row 2: Asset allocation bars + Liabilities + Projection ─────────
    def alloc_panel(title: str, data: dict[str, float], colors: list[str]) -> str:
        rows = ""
        for i, (label, value) in enumerate(data.items()):
            color = colors[i % len(colors)]
            rows += (
                f"<div class='ap-row'>"
                f"<span class='ap-dot' style='background:{color}'></span>"
                f"<span class='ap-label'>{label}</span>"
                f"<div class='ap-track'><div class='ap-fill' style='width:{min(value,100):.1f}%;background:{color}'></div></div>"
                f"<span class='ap-pct'>{value:.1f}%</span>"
                f"</div>"
            )
        return f"<div class='alloc-panel'><div class='ap-title'>{title}</div>{rows}</div>"

    cat_colors  = ["#7c3aed", "#0891b2", "#d97706", "#64748b"]
    barb_colors = ["#16a34a", "#dc2626", "#94a3b8"]

    al1, al2, al3 = st.columns(3)
    with al1:
        st.markdown(alloc_panel("Asset type", summary["asset_categories"], cat_colors), unsafe_allow_html=True)
    with al2:
        st.markdown(alloc_panel("Barbell strategy", summary["barbell_roles"], barb_colors), unsafe_allow_html=True)
    with al3:
        # Cap bucket allocation
        cap_data: dict[str, float] = {}
        total_val_all = summary["net_worth_eur"] or 1
        for row in items:
            bucket = row.get("cap_bucket") or "Unclassified"
            cap_data[bucket] = cap_data.get(bucket, 0) + float(row["value_eur"] or 0) / total_val_all * 100
        cap_data = dict(sorted(cap_data.items(), key=lambda x: -x[1]))
        cap_colors = ["#0891b2", "#7c3aed", "#f59e0b", "#64748b", "#10b981"]
        st.markdown(alloc_panel("Market cap", cap_data, cap_colors), unsafe_allow_html=True)

    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

    # ── Row 3: Liabilities + Growth projection ───────────────────────────
    debt_balance         = debt_config["balance"]
    debt_monthly_payment = debt_config["monthly_payment"]
    debt_remaining_months = int(debt_config["remaining_months"])
    net_after_liabilities = total_net_worth - debt_balance
    debt_to_assets = debt_balance / total_net_worth * 100 if total_net_worth else 0

    col_liab, col_proj = st.columns([1, 1.6])

    with col_liab:
        progress_pct = min((1 - debt_balance / debt_config["original_principal"]) * 100, 100) if debt_config["original_principal"] else 0
        st.markdown(
            f"""
            <div class='fd-panel'>
              <div class='fd-panel-title'>Liabilities</div>
              <div class='fd-kv'><span>Loan balance</span><strong class='red'>{money(debt_balance, currency, rates)}</strong></div>
              <div class='fd-kv'><span>Monthly payment</span><strong>{money(debt_monthly_payment, currency, rates)}</strong></div>
              <div class='fd-kv'><span>Remaining term</span><strong>{debt_remaining_months} mo</strong></div>
              <div class='fd-kv'><span>Debt / assets</span><strong>{debt_to_assets:.1f}%</strong></div>
              <div class='fd-divider'></div>
              <div class='fd-kv'><span>Net after liabilities</span><strong class='green'>{money(net_after_liabilities, currency, rates)}</strong></div>
              <div class='fd-progress-label'>Repaid {progress_pct:.0f}% of original loan</div>
              <div class='fd-progress-track'><div class='fd-progress-fill' style='width:{progress_pct:.1f}%'></div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_proj:
        inp1, inp2 = st.columns(2)
        monthly_addition = inp1.number_input("Monthly addition (EUR)", min_value=0.0, value=100.0, step=50.0)
        expected_return  = inp2.number_input("Annual return %", min_value=-50.0, max_value=50.0, value=8.0, step=0.5)

        horizons = [("1Y", 12), ("3Y", 36), ("5Y", 60), ("10Y", 120)]
        proj_cells = ""
        for label, months in horizons:
            p = portfolio_projection(total_net_worth, months, monthly_addition, 0, expected_return)
            gain_pct = (p["value"] / total_net_worth - 1) * 100 if total_net_worth else 0
            proj_cells += (
                f"<div class='proj-cell'>"
                f"<span class='proj-horizon'>{label}</span>"
                f"<strong class='proj-value'>{money(p['value'], currency, rates)}</strong>"
                f"<span class='proj-gain {'green' if gain_pct >= 0 else 'red'}'>{'+' if gain_pct >= 0 else ''}{gain_pct:.0f}%</span>"
                f"</div>"
            )
        st.markdown(
            f"<div class='fd-panel'><div class='fd-panel-title'>Growth projection</div>"
            f"<div class='proj-grid'>{proj_cells}</div></div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

    # ── Top 10 holdings + waterfall P/L chart ───────────────────────────
    st.markdown("<div class='fd-section-title'>Top holdings</div>", unsafe_allow_html=True)

    tbl_col, wfall_col = st.columns([1.6, 1])
    with tbl_col:
        total_val = summary["net_worth_eur"]
        top_rows = ""
        for row in items[:10]:
            val   = float(row["value_eur"] or 0)
            inv   = float(row["invested_eur"] or 0)
            pnl   = val - inv
            ret   = float(row["return_percent"] or 0)
            wt    = val / total_val * 100 if total_val else 0
            pnl_cls = "green" if pnl >= 0 else "red"
            ret_cls = "green" if ret >= 0 else "red"
            bar_w = min(wt * 3.5, 100)
            top_rows += (
                f"<tr>"
                f"<td><strong>{row['name']}</strong><br><span class='sub'>{row['ticker']}</span></td>"
                f"<td><span class='badge badge-{row['market'].lower()}'>{row['market']}</span></td>"
                f"<td><strong>{money(val, currency, rates)}</strong></td>"
                f"<td class='{ret_cls}'>{'+' if ret >= 0 else ''}{ret:.1f}%</td>"
                f"<td><div class='wt-bar'><div class='wt-fill' style='width:{bar_w:.0f}%'></div></div>"
                f"<span class='wt-label'>{wt:.1f}%</span></td>"
                f"</tr>"
            )
        st.markdown(
            f"<table class='fd-table'><thead><tr>"
            f"<th>Asset</th><th>Market</th><th>Value</th><th>Return</th><th>Weight</th>"
            f"</tr></thead><tbody>{top_rows}</tbody></table>",
            unsafe_allow_html=True,
        )

    with wfall_col:
        # Horizontal bar chart: top 8 by P/L value
        top8 = sorted(items[:15], key=lambda r: abs(float(r["value_eur"] or 0) - float(r["invested_eur"] or 0)), reverse=True)[:8]
        names_bar = [r["name"][:18] for r in top8]
        pnl_bar   = [float(r["value_eur"] or 0) - float(r["invested_eur"] or 0) for r in top8]
        bar_colors = ["#16a34a" if v >= 0 else "#dc2626" for v in pnl_bar]
        fig_bar = go.Figure(go.Bar(
            y=names_bar, x=pnl_bar, orientation="h",
            marker_color=bar_colors,
            text=[f"{'+'if v>=0 else ''}€{abs(v):,.0f}" for v in pnl_bar],
            textposition="outside", textfont=dict(color=plot_muted, size=10),
            hovertemplate="%{y}: %{x:+,.0f} EUR<extra></extra>",
        ))
        fig_bar.update_layout(
            height=280, margin=dict(l=0, r=50, t=8, b=0),
            paper_bgcolor=plot_bg, plot_bgcolor=plot_bg,
            xaxis=dict(showgrid=True, gridcolor=plot_line, zeroline=True, zerolinecolor=plot_line, tickprefix="€", tickfont=dict(color=plot_muted, size=10)),
            yaxis=dict(showgrid=False, tickfont=dict(color=plot_ink, size=10)),
            bargap=0.35,
        )
        st.markdown("<div class='fd-section-title' style='margin-top:0'>P/L by holding</div>", unsafe_allow_html=True)
        st.plotly_chart(fig_bar, use_container_width=True, config={"displayModeBar": False})

elif page == "Holdings":
    controls = st.columns([1.4, 1, 1, 1, 1])
    search = controls[0].text_input("Search", placeholder="Company or ticker").strip().lower()
    market = controls[1].selectbox("Market", ["All", "India", "Global"])
    category = controls[2].selectbox("Type", ["All", "Stock", "Mutual fund", "ETF", "Other"])
    role = controls[3].selectbox("Barbell", ["All", "Core", "Upside", "Review"])
    sort = controls[4].selectbox("Sort", ["Highest value", "Lowest value", "Highest return", "Lowest return", "Name"])
    filtered = [
        row for row in items
        if (market == "All" or row["market"] == market)
        and (category == "All" or row["asset_category"] == category)
        and (role == "All" or row["barbell_role"] == role)
        and (not search or search in row["name"].lower() or search in row["ticker"].lower())
    ]
    if sort == "Lowest value":
        filtered.sort(key=lambda row: float(row["value_eur"] or 0))
    elif sort == "Highest return":
        filtered.sort(key=lambda row: float(row["return_percent"] or 0), reverse=True)
    elif sort == "Lowest return":
        filtered.sort(key=lambda row: float(row["return_percent"] or 0))
    elif sort == "Name":
        filtered.sort(key=lambda row: row["name"].lower())
    else:
        filtered.sort(key=lambda row: float(row["value_eur"] or 0), reverse=True)
    filtered_summary = summarize(filtered)
    f_ret_sign = "+" if filtered_summary["total_returns_eur"] >= 0 else ""
    f_ret_color = "var(--atlas-green)" if filtered_summary["total_returns_eur"] >= 0 else "var(--atlas-red)"
    st.markdown(
        f"""
        <div class='fd-metrics'>
          <div class='fd-metric primary'>
            <span class='fd-metric-label'>Filtered value</span>
            <span class='fd-metric-value'>{money(filtered_summary["net_worth_eur"], currency, rates)}</span>
            <span class='fd-metric-sub'>{len(filtered)} of {len(items)} holdings</span>
          </div>
          <div class='fd-metric teal'>
            <span class='fd-metric-label'>Amount invested</span>
            <span class='fd-metric-value'>{money(filtered_summary["invested_eur"], currency, rates)}</span>
            <span class='fd-metric-sub'>capital deployed</span>
          </div>
          <div class='fd-metric {"green" if filtered_summary["total_returns_eur"] >= 0 else "red"}'>
            <span class='fd-metric-label'>Profit / Loss</span>
            <span class='fd-metric-value' style='color:{f_ret_color}'>{f_ret_sign}{money(filtered_summary["total_returns_eur"], currency, rates)}</span>
            <span class='fd-metric-sub' style='color:{f_ret_color}'>{f_ret_sign}{filtered_summary["total_return_percent"]:.1f}% return</span>
          </div>
          <div class='fd-metric'>
            <span class='fd-metric-label'>Holdings shown</span>
            <span class='fd-metric-value'>{len(filtered)}</span>
            <span class='fd-metric-sub'>matching filters</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    # ── Return distribution scatter ──────────────────────────────────────
    if filtered:
        ret_vals = [float(r["return_percent"] or 0) for r in filtered]
        val_vals = [float(r["value_eur"] or 0) for r in filtered]
        names_sc = [r["name"] for r in filtered]
        colors_sc = ["#16a34a" if v >= 0 else "#dc2626" for v in ret_vals]
        fig_scatter = go.Figure(go.Scatter(
            x=ret_vals, y=val_vals,
            mode="markers+text",
            text=[n[:14] for n in names_sc],
            textposition="top center",
            textfont=dict(size=9, color=plot_muted),
            marker=dict(color=colors_sc, size=[max(8, min(v / 200, 28)) for v in val_vals],
                        line=dict(color=plot_bg, width=1.5), opacity=0.85),
            hovertemplate="<b>%{text}</b><br>Return: %{x:.1f}%<br>Value: €%{y:,.0f}<extra></extra>",
        ))
        fig_scatter.add_vline(x=0, line=dict(color=plot_muted, width=1, dash="dot"))
        fig_scatter.update_layout(
            height=220, margin=dict(l=0, r=0, t=8, b=0),
            paper_bgcolor=plot_bg, plot_bgcolor=plot_bg,
            xaxis=dict(showgrid=False, zeroline=False, ticksuffix="%", tickfont=dict(color=plot_muted, size=10)),
            yaxis=dict(showgrid=True, gridcolor=plot_line, tickprefix="€", tickfont=dict(color=plot_muted, size=10)),
            showlegend=False,
        )
        st.markdown("<div class='fd-section-title'>Return vs value (bubble size = value)</div>", unsafe_allow_html=True)
        st.plotly_chart(fig_scatter, use_container_width=True, config={"displayModeBar": False})

    st.markdown(holding_table(filtered), unsafe_allow_html=True)

    # ── Update share quantities ──────────────────────────────────────────
    with st.expander("Update share quantities", icon="✏️"):
        st.caption(
            "Value = quantity × live price (fetched on refresh). Edit quantity (and optionally average cost) "
            "below, then save — value, P/L and weight all recompute automatically."
        )
        editable = [r for r in filtered if r["quantity"] is not None]
        if not editable:
            st.info("No editable holdings in the current filter (only holdings with a tracked quantity can be edited).")
        else:
            df = pd.DataFrame([
                {
                    "id": r["id"],
                    "Name": r["name"],
                    "Ticker": r["ticker"],
                    "Market": r["market"],
                    "Quantity": float(r["quantity"]),
                    "Avg cost": float(r["average_cost"]) if r["average_cost"] is not None else 0.0,
                    "Current price": float(r["current_price"]) if r["current_price"] is not None else 0.0,
                    "Value (EUR)": float(r["value_eur"]),
                }
                for r in editable
            ])
            edited = st.data_editor(
                df,
                column_config={
                    "id": None,
                    "Name": st.column_config.TextColumn(disabled=True),
                    "Ticker": st.column_config.TextColumn(disabled=True),
                    "Market": st.column_config.TextColumn(disabled=True),
                    "Quantity": st.column_config.NumberColumn(min_value=0.0, step=0.0001, format="%.4f"),
                    "Avg cost": st.column_config.NumberColumn(min_value=0.0, step=0.01, format="%.4f"),
                    "Current price": st.column_config.NumberColumn(disabled=True, format="%.4f"),
                    "Value (EUR)": st.column_config.NumberColumn(disabled=True, format="€%.2f"),
                },
                hide_index=True,
                use_container_width=True,
                key="qty_editor",
            )
            if st.button("Save share quantities", type="primary"):
                changed = 0
                for i in range(len(df)):
                    orig_row = df.iloc[i]
                    new_row = edited.iloc[i]
                    if orig_row["Quantity"] != new_row["Quantity"] or orig_row["Avg cost"] != new_row["Avg cost"]:
                        update_holding_position(
                            orig_row["id"],
                            float(new_row["Quantity"]),
                            float(new_row["Avg cost"]) if new_row["Avg cost"] > 0 else None,
                        )
                        changed += 1
                if changed:
                    st.success(f"Updated {changed} holding{'s' if changed != 1 else ''}.")
                    st.rerun()
                else:
                    st.info("No changes to save.")

elif page == "Other assets":
    # ── category totals ───────────────────────────────────────────────────
    cat_totals: dict[str, float] = {}
    for a in other_assets:
        cat_totals[a["category"]] = cat_totals.get(a["category"], 0) + float(a["value_eur"])
    cat_colors_map = {
        "Real estate": "#7c3aed", "Cash & savings": "#16a34a", "Private investment": "#2563eb",
        "Crypto": "#f59e0b", "Vehicles & collectibles": "#0891b2", "Other": "#64748b",
    }

    # ── page header ───────────────────────────────────────────────────────
    oa_tag = f"<span class='fd-tag green'>{money(other_assets_total, currency, rates)} total</span>" if other_assets else ""
    st.markdown(
        f"""
        <div class='fd-header'>
          <div>
            <div class='fd-header-title'>Other Assets</div>
            <div class='fd-header-sub'>Real estate, cash, private investments and more — added to your total wealth</div>
          </div>
          <div class='fd-header-right'>{oa_tag}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── category summary cards ─────────────────────────────────────────────
    if cat_totals:
        cards_html = ""
        for cat, total_val in sorted(cat_totals.items(), key=lambda x: -x[1]):
            color = cat_colors_map.get(cat, "#64748b")
            pct_of_other = total_val / other_assets_total * 100 if other_assets_total else 0
            pct_of_total = total_val / total_net_worth * 100 if total_net_worth else 0
            count = sum(1 for a in other_assets if a["category"] == cat)
            cards_html += (
                f"<div class='fd-metric' style='border-left:3px solid {color}'>"
                f"<span class='fd-metric-label'>{cat}</span>"
                f"<span class='fd-metric-value'>{money(total_val, currency, rates)}</span>"
                f"<span class='fd-metric-sub'>{pct_of_other:.1f}% of other · {count} item{'s' if count != 1 else ''}</span>"
                f"</div>"
            )
        # Pad to 4 cols
        while cards_html.count("fd-metric") % 4 != 0:
            cards_html += "<div class='fd-metric' style='visibility:hidden'></div>"
        st.markdown(f"<div class='fd-metrics'>{cards_html}</div>", unsafe_allow_html=True)

    # ── asset donut chart ──────────────────────────────────────────────────
    if cat_totals:
        fig_oa = go.Figure(go.Pie(
            labels=list(cat_totals.keys()),
            values=list(cat_totals.values()),
            hole=0.6,
            marker=dict(colors=[cat_colors_map.get(c, "#64748b") for c in cat_totals.keys()],
                        line=dict(color=plot_bg, width=2)),
            textinfo="none",
            hovertemplate="%{label}: €%{value:,.0f} (%{percent})<extra></extra>",
        ))
        fig_oa.add_annotation(
            text=f"<b>{money(other_assets_total, currency, rates)}</b><br><span style='font-size:10px'>{len(other_assets)} assets</span>",
            x=0.5, y=0.5, showarrow=False, font=dict(color=plot_ink, size=13), align="center",
        )
        fig_oa.update_layout(
            height=220, margin=dict(l=0, r=0, t=0, b=0),
            paper_bgcolor=plot_bg, showlegend=True,
            legend=dict(orientation="v", x=0.72, y=0.5, font=dict(color=plot_muted, size=11), bgcolor="rgba(0,0,0,0)"),
        )
        donut_c, _ = st.columns([1, 1.5])
        with donut_c:
            st.plotly_chart(fig_oa, use_container_width=True, config={"displayModeBar": False})

    # ── add / edit form ────────────────────────────────────────────────────
    st.markdown("<div class='fd-section-title'>Add or edit asset</div>", unsafe_allow_html=True)

    with st.form("oa_form", clear_on_submit=True):
        f1, f2, f3 = st.columns([2, 1, 1])
        oa_name     = f1.text_input("Asset name", placeholder="e.g. Berlin apartment, HDFC savings account")
        oa_category = f2.selectbox("Category", MANUAL_ASSET_CATEGORIES)
        oa_currency = f3.selectbox("Currency", ["EUR", "INR", "USD"])

        g1, g2, g3 = st.columns(3)
        oa_value_raw    = g1.number_input("Current value", min_value=0.0, step=1000.0,
                                          help="Enter the value in the currency selected above")
        oa_cost_raw     = g2.number_input("Purchase cost (optional)", min_value=0.0, step=1000.0,
                                          help="What you originally paid — used to calculate gain/loss")
        oa_notes        = g3.text_input("Notes (optional)", placeholder="Location, account number, etc.")

        submit = st.form_submit_button("Add asset", type="primary", use_container_width=True)
        if submit:
            if not oa_name.strip():
                st.error("Asset name is required.")
            elif oa_value_raw <= 0:
                st.error("Value must be greater than 0.")
            else:
                value_eur = oa_value_raw / rates.get(oa_currency, 1)
                cost_eur  = (oa_cost_raw / rates.get(oa_currency, 1)) if oa_cost_raw > 0 else None
                upsert_manual_asset(None, oa_name.strip(), oa_category, value_eur,
                                    cost_eur, oa_currency, oa_value_raw, oa_notes.strip())
                st.success(f"Added {oa_name}!")
                st.rerun()

    # ── existing assets table ──────────────────────────────────────────────
    if other_assets:
        st.markdown("<div class='fd-section-title'>Your other assets</div>", unsafe_allow_html=True)
        for asset in other_assets:
            val_eur  = float(asset["value_eur"])
            cost_eur = float(asset["cost_eur"]) if asset.get("cost_eur") else None
            gain_eur = val_eur - cost_eur if cost_eur else None
            gain_pct = (gain_eur / cost_eur * 100) if cost_eur else None
            color    = cat_colors_map.get(asset["category"], "#64748b")
            gain_html = ""
            if gain_eur is not None:
                g_cls = "green" if gain_eur >= 0 else "red"
                sign  = "+" if gain_eur >= 0 else ""
                gain_html = (
                    f"<span class='fd-tag {g_cls}' style='margin-left:8px'>"
                    f"{sign}{money(gain_eur, currency, rates)} ({sign}{gain_pct:.1f}%)</span>"
                )
            cost_html = (
                f"<span>Cost: <strong style='color:var(--atlas-ink)'>{money(cost_eur, currency, rates)}</strong></span>"
                if cost_eur else ""
            )
            notes_html = f"<span style='font-style:italic'>{asset['notes']}</span>" if asset.get("notes") else ""
            with st.container():
                row_l, row_r = st.columns([4, 1])
                with row_l:
                    # Built as one unbroken line, not a multi-line indented f-string: when the
                    # optional cost/notes spans are empty, the deep Python-source indentation
                    # they'd otherwise leave behind creates a blank line followed by 4+ spaces —
                    # which Markdown reads as the start of an indented code block, so the next
                    # span (the date) rendered as literal escaped text instead of HTML.
                    st.markdown(
                        f"<div style='background:var(--atlas-panel);border:1px solid var(--atlas-line);"
                        f"border-left:3px solid {color};border-radius:10px;padding:14px 18px;margin-bottom:8px'>"
                        f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:4px'>"
                        f"<strong style='color:var(--atlas-ink);font-size:14px'>{asset['name']}</strong>"
                        f"<span class='fd-tag'>{asset['category']}</span>"
                        f"{gain_html}"
                        f"</div>"
                        f"<div style='display:flex;gap:24px;font-size:13px;color:var(--atlas-muted)'>"
                        f"<span>Value: <strong style='color:var(--atlas-ink)'>{money(val_eur, currency, rates)}</strong></span>"
                        f"{cost_html}"
                        f"{notes_html}"
                        f"<span>Updated: {asset['updated_at'][:10]}</span>"
                        f"</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with row_r:
                    new_val = st.number_input(
                        "New value", min_value=0.0, step=1000.0,
                        key=f"upd_val_{asset['id']}", label_visibility="collapsed",
                    )
                    col_upd, col_del = st.columns(2)
                    with col_upd:
                        if st.button("Update", key=f"upd_{asset['id']}", use_container_width=True) and new_val > 0:
                            cur = asset.get("currency", "EUR")
                            new_eur = new_val / rates.get(cur, 1)
                            upsert_manual_asset(asset["id"], asset["name"], asset["category"],
                                                new_eur, float(asset["cost_eur"]) if asset.get("cost_eur") else None,
                                                cur, new_val, asset.get("notes", ""))
                            st.rerun()
                    with col_del:
                        if st.button("Delete", key=f"del_{asset['id']}", use_container_width=True):
                            delete_manual_asset(asset["id"])
                            st.rerun()
    else:
        st.markdown(
            "<div class='debt-note'>No other assets added yet. Use the form above to add your real estate, "
            "savings accounts, private investments, and anything else that makes up your wealth.</div>",
            unsafe_allow_html=True,
        )

elif page == "Broker imports":
    st.markdown(
        """
        <div class='fd-header'>
          <div>
            <div class='fd-header-title'>Broker Imports</div>
            <div class='fd-header-sub'>Import transaction exports to build a reconciled ledger — buys, sells, dividends, and fees</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([1, 1.25])
    with left:
        st.subheader("Import transactions")
        broker = st.selectbox("Broker", BROKERS)
        default_market = st.selectbox("Default market", ["Global", "India"])
        uploaded = st.file_uploader("Broker CSV export", type=["csv"])
        st.markdown(
            """
            <div class="debt-note">
            Flexible columns supported: date, type/action, name/instrument, ticker/symbol/ISIN, quantity/shares, price, amount, fee, currency.
            </div>
            """,
            unsafe_allow_html=True,
        )
        if uploaded and st.button("Import broker transactions", type="primary"):
            try:
                imported_count = import_broker_transactions(uploaded, broker, default_market)
                st.success(f"Imported {imported_count} transactions from {broker}.")
                st.rerun()
            except Exception as error:
                st.error(str(error))

    with right:
        st.subheader("Import history")
        imports = load_rows("SELECT * FROM broker_imports WHERE user_id = ? ORDER BY created_at DESC LIMIT 10", (LOCAL_USER_ID,))
        if imports:
            rows = []
            for item in imports:
                rows.append(
                    f"<tr><td><strong>{item['broker']}</strong><br><small>{item['filename']}</small></td>"
                    f"<td>{item['imported']}</td><td>{item['created_at']}</td></tr>"
                )
            st.markdown(
                "<table><thead><tr><th>Broker/file</th><th>Rows</th><th>Imported at</th></tr></thead><tbody>"
                + "".join(rows)
                + "</tbody></table>",
                unsafe_allow_html=True,
            )
        else:
            st.info("No broker imports yet.")

    st.subheader("Recent broker transactions")
    transactions = broker_transactions()
    if transactions:
        st.markdown(transaction_table(transactions), unsafe_allow_html=True)
    else:
        st.info("Import a broker CSV to start building the transaction ledger.")

elif page == "Debt tracker":
    original_principal = debt_config["original_principal"]
    debt_balance = debt_config["balance"]
    scheduled_payment = debt_config["monthly_payment"]
    original_term = int(debt_config["original_term"])
    paid_months = int(debt_config["paid_months"])
    remaining_months = int(debt_config["remaining_months"])
    stored_interest = debt_config["annual_interest"]
    inferred_interest = stored_interest if stored_interest > 0 else infer_annual_interest_rate(debt_balance, scheduled_payment, remaining_months)

    st.markdown(
        f"""
        <div class='fd-header'>
          <div>
            <div class='fd-header-title'>Debt Tracker</div>
            <div class='fd-header-sub'>Loan payoff modelling · {inferred_interest:.1f}% annual rate</div>
          </div>
          <div class='fd-header-right'>
            <span class='fd-tag red'>Balance: {money(debt_balance, currency, rates)}</span>
            <span class='fd-tag'>{remaining_months} months left</span>
          </div>
        </div>
        <div class='fd-section-title'>Loan details</div>
        """,
        unsafe_allow_html=True,
    )
    detail_columns = st.columns(5)
    edited_original_principal = detail_columns[0].number_input("Original loan", min_value=0.0, value=float(original_principal), step=500.0)
    edited_balance = detail_columns[1].number_input("Remaining balance", min_value=0.0, value=float(debt_balance), step=100.0)
    edited_payment = detail_columns[2].number_input("Monthly payment", min_value=0.0, value=float(scheduled_payment), step=25.0)
    edited_original_term = detail_columns[3].number_input(
        "Original term months", min_value=1, value=max(int(original_term), 1), step=1
    )
    edited_paid_months = detail_columns[4].number_input("Paid months", min_value=0, max_value=int(edited_original_term), value=min(int(paid_months), int(edited_original_term)), step=1)
    interest_col, save_col = st.columns([1, 3])
    edited_interest = interest_col.number_input("Annual interest %", min_value=0.0, max_value=30.0, value=float(debt_config["annual_interest"]) if debt_config["annual_interest"] > 0 else 0.0, step=0.1)
    if save_col.button("Save debt details", type="primary"):
        set_setting_value("debt_original_principal_eur", edited_original_principal)
        set_setting_value("debt_balance_eur", edited_balance)
        set_setting_value("debt_monthly_payment_eur", edited_payment)
        set_setting_value("debt_original_term_months", float(edited_original_term))
        set_setting_value("debt_paid_months", float(edited_paid_months))
        set_setting_value("debt_annual_interest_pct", edited_interest)
        st.success("Debt details saved.")
        st.rerun()

    original_principal = edited_original_principal
    debt_balance = edited_balance
    scheduled_payment = edited_payment
    original_term = int(edited_original_term)
    paid_months = int(edited_paid_months)
    remaining_months = max(original_term - paid_months, 0)
    stored_interest = debt_config["annual_interest"]
    inferred_interest = stored_interest if stored_interest > 0 else infer_annual_interest_rate(debt_balance, scheduled_payment, remaining_months)

    st.markdown(
        """
        <div class="loan-input-strip">
          <div class="loan-kicker">Additional payment controls</div>
          <p class="loan-input-caption">Use these bars to test how extra principal payments change the payoff month and interest.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    slider_columns = st.columns(2)
    extra_monthly = slider_columns[0].slider("Additional monthly principal payment", min_value=0, max_value=2500, value=0, step=25)
    one_time_extra = slider_columns[1].slider("One-time additional principal payment", min_value=0, max_value=15000, value=0, step=250)
    annual_interest = st.number_input("Estimated annual interest %", min_value=0.0, value=round(inferred_interest, 2), step=0.1)

    projection = debt_projection(debt_balance, scheduled_payment, extra_monthly, one_time_extra, annual_interest)
    baseline_with_rate = debt_projection(debt_balance, scheduled_payment, annual_interest=annual_interest)
    months_saved = baseline_with_rate["months"] - projection["months"] if math.isfinite(projection["months"]) and math.isfinite(baseline_with_rate["months"]) else 0
    interest_saved = baseline_with_rate["total_interest"] - projection["total_interest"] if math.isfinite(projection["total_interest"]) and math.isfinite(baseline_with_rate["total_interest"]) else 0
    target_date = add_months(date.today(), 24)
    months_late = projection["months"] - 24 if math.isfinite(projection["months"]) else None
    payoff_label = projection["payoff_date"].strftime("%b %Y") if projection["payoff_date"] else "Not reducing"
    status_title = f"{payoff_label} — {abs(int(months_late))} months {'late' if months_late and months_late > 0 else 'early'}" if months_late else f"{payoff_label} target"
    schedule_rows = projection["schedule"]
    chart_points = projection["schedule"][::max(1, len(projection["schedule"]) // 8)] if projection["schedule"] else []
    if projection["schedule"] and projection["schedule"][-1] not in chart_points:
        chart_points.append(projection["schedule"][-1])

    chart_html = []
    max_balance = max([debt_balance] + [point["Remaining"] for point in chart_points]) if chart_points else debt_balance
    for point in chart_points[:9]:
        height = max(point["Remaining"] / max_balance * 170, 8)
        css_class = "done" if point["Remaining"] < debt_balance * 0.2 else "low" if point["Remaining"] < debt_balance * 0.45 else "mid" if point["Remaining"] < debt_balance * 0.7 else ""
        chart_html.append(
            f"<div class='loan-bar-wrap'><div class='loan-bar-label'>{money(point['Remaining'], currency, rates)}</div>"
            f"<div class='loan-bar {css_class}' style='height:{height:.0f}px'></div>"
            f"<div>{point['Date'].strftime('%b')}<br>{point['Date'].strftime('%y')}</div></div>"
        )

    table_rows = []
    for row in schedule_rows:
        table_rows.append(
            f"<tr><td><strong>{row['Date'].strftime('%b %Y')}</strong></td>"
            f"<td>{money(row['Payment'], currency, rates)}</td>"
            f"<td class='principal'>{money(row['Principal'], currency, rates)}</td>"
            f"<td class='interest'>{money(row['Interest'], currency, rates)}</td>"
            f"<td><strong>{money(row['Remaining'], currency, rates)}</strong></td></tr>"
        )

    st.markdown(
        f"""
          <div class="loan-shell">
          <div class="loan-kicker">Debt elimination tracker</div>
          <div class="loan-title">{money(original_principal, currency, rates)} Loan Payoff</div>
          <div class="loan-subtitle">Paid {paid_months} months · {remaining_months} scheduled payments left · inferred annual rate {annual_interest:.2f}%</div>
          <div class="loan-top-plan">
            <div class="loan-top-pill"><span>Additional monthly principal payment</span><strong class="green">{money(extra_monthly, currency, rates)}</strong></div>
            <div class="loan-top-pill"><span>One-time additional principal payment</span><strong class="gold">{money(one_time_extra, currency, rates)}</strong></div>
            <div class="loan-top-pill"><span>Total monthly payment</span><strong>{money(scheduled_payment + extra_monthly, currency, rates)}</strong></div>
          </div>
          <div class="loan-alert">{status_title}<small>{projection['months']} monthly payments · saving {money(max(interest_saved, 0), currency, rates)} in interest vs scheduled payments · target {target_date.strftime('%b %Y')}</small></div>
          <div class="loan-control-card">
            <div class="loan-kicker">Adjust your plan</div>
            <div class="loan-stat-grid">
              <div class="loan-stat"><span>Base EMI</span><strong>{money(scheduled_payment, currency, rates)}</strong></div>
              <div class="loan-stat"><span>Additional Monthly Principal</span><strong>{money(extra_monthly, currency, rates)}</strong></div>
              <div class="loan-stat"><span>Total Monthly</span><strong class="green">{money(scheduled_payment + extra_monthly, currency, rates)}</strong></div>
              <div class="loan-stat"><span>Total Paid</span><strong>{money(projection['total_paid'], currency, rates) if math.isfinite(projection['total_paid']) else 'N/A'}</strong></div>
            </div>
          </div>
          <div class="loan-mini-grid">
            <div class="loan-mini-stat"><span>Remaining Balance</span><strong class="red">{money(max(debt_balance - one_time_extra, 0), currency, rates)}</strong><span>after one-time additional principal</span></div>
            <div class="loan-mini-stat"><span>Total Interest</span><strong>{money(projection['total_interest'], currency, rates) if math.isfinite(projection['total_interest']) else 'N/A'}</strong><span>saved {money(max(interest_saved, 0), currency, rates)}</span></div>
            <div class="loan-mini-stat"><span>Payoff Month</span><strong class="red">{payoff_label}</strong><span>{projection['months']} payments · {int(months_saved)} months saved</span></div>
          </div>
          <div class="loan-chart-card">
            <div class="loan-kicker">Balance over time</div>
            <div class="loan-chart">{''.join(chart_html)}</div>
          </div>
          <div class="loan-table-card">
            <div class="loan-kicker">All monthly payments</div>
            <table class="loan-table"><thead><tr><th>Month</th><th>Payment</th><th>Principal</th><th>Interest</th><th>Balance</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

elif page == "Budget tracker":
    salary = monthly_salary()
    all_expenses = budget_expenses()

    def _expense_month(item: dict) -> str:
        return (item["expense_date"] or item["created_at"][:10])[:7]

    months_with_data = sorted({_expense_month(item) for item in all_expenses}, reverse=True)
    month_labels = {m: datetime.strptime(m, "%Y-%m").strftime("%B %Y") for m in months_with_data}
    month_options = ["All time"] + months_with_data
    default_index = 0

    header_l, header_r = st.columns([3, 1])
    with header_r:
        selected_month = st.selectbox(
            "Month",
            month_options,
            index=default_index,
            format_func=lambda m: "All time" if m == "All time" else month_labels[m],
            label_visibility="collapsed",
        )

    if selected_month == "All time":
        expenses = all_expenses
        period_label = "All-time expenses"
        months_in_period = max(len(months_with_data), 1)
    else:
        expenses = [item for item in all_expenses if _expense_month(item) == selected_month]
        period_label = f"{month_labels[selected_month]} expenses"
        months_in_period = 1

    salary_for_period = salary * months_in_period
    total_expenses = sum(float(item["amount_eur"] or 0) for item in expenses)
    leftover = salary_for_period - total_expenses
    savings_rate = leftover / salary_for_period * 100 if salary_for_period else 0

    expense_ratio = total_expenses / salary_for_period * 100 if salary_for_period else 0
    leftover_color = "var(--atlas-green)" if leftover >= 0 else "var(--atlas-red)"
    savings_tag_cls = "green" if savings_rate >= 25 else "amber" if savings_rate >= 10 else "red"
    with header_l:
        st.markdown(
            f"""
            <div class='fd-header'>
              <div>
                <div class='fd-header-title'>Budget Tracker</div>
                <div class='fd-header-sub'>{period_label} · income vs. spending</div>
              </div>
              <div class='fd-header-right'>
                <span class='fd-tag {savings_tag_cls}'>{savings_rate:.0f}% savings rate</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.markdown(
        f"""
        <div class='fd-metrics'>
          <div class='fd-metric teal'>
            <span class='fd-metric-label'>💰 {"Salary" if months_in_period == 1 else f"Salary ({months_in_period} months)"}</span>
            <span class='fd-metric-value'>{money(salary_for_period, currency, rates)}</span>
            <span class='fd-metric-sub'>{"net take-home" if months_in_period == 1 else f"{money(salary, currency, rates)} × {months_in_period} months"}</span>
          </div>
          <div class='fd-metric red'>
            <span class='fd-metric-label'>💸 {"Monthly expenses" if months_in_period == 1 else "All-time expenses"}</span>
            <span class='fd-metric-value'>{money(total_expenses, currency, rates)}</span>
            <span class='fd-metric-sub'>{len(expenses)} tracked items</span>
          </div>
          <div class='fd-metric {"green" if leftover >= 0 else "red"}'>
            <span class='fd-metric-label'>🏷️ Left after expenses</span>
            <span class='fd-metric-value' style='color:{leftover_color}'>{money(leftover, currency, rates)}</span>
            <span class='fd-metric-sub' style='color:{leftover_color}'>{savings_rate:.1f}% savings rate</span>
          </div>
          <div class='fd-metric amber'>
            <span class='fd-metric-label'>📊 Expense ratio</span>
            <span class='fd-metric-value'>{expense_ratio:.1f}%</span>
            <span class='fd-metric-sub'>of income allocated</span>
          </div>
        </div>
        <div class='debt-note'>Track salary and expenses — rent, subscriptions, utilities, groceries, loan payments, investments. Use the month picker above to browse a specific period.</div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("📄 Import expenses from a PDF statement", icon=None):
        st.caption(
            "Upload a bank or credit card statement PDF. Each line that looks like "
            "`date · description · amount` is extracted and auto-categorized — review and "
            "edit before importing, since statement layouts vary and extraction isn't perfect."
        )
        pdf_file = st.file_uploader("Statement PDF", type=["pdf"], key="expense_pdf_uploader")
        if pdf_file is not None:
            cache_key = f"pdf_extract_{pdf_file.file_id}"
            if cache_key not in st.session_state:
                try:
                    st.session_state[cache_key] = extract_pdf_transactions(pdf_file)
                except Exception as error:
                    st.error(f"Couldn't read this PDF: {error}")
                    st.session_state[cache_key] = []
            extracted = st.session_state[cache_key]

            if not extracted:
                st.warning(
                    "No transaction-like lines were found. This parser expects each line to "
                    "start with a date and end with an amount — some statement layouts (e.g. "
                    "scanned/image-only PDFs, or tables split across columns) won't extract cleanly."
                )
            else:
                st.success(f"Found {len(extracted)} possible expenses. Review and edit below before importing.")
                preview_df = pd.DataFrame([
                    {
                        "Include": True,
                        "Date": row["date"],
                        "Description": row["description"],
                        "Category": row["category"],
                        "Amount (EUR)": row["amount"],
                    }
                    for row in extracted
                ])
                edited_df = st.data_editor(
                    preview_df,
                    column_config={
                        "Include": st.column_config.CheckboxColumn(),
                        "Date": st.column_config.DateColumn(),
                        "Category": st.column_config.SelectboxColumn(options=BUDGET_CATEGORIES),
                        "Amount (EUR)": st.column_config.NumberColumn(min_value=0.0, format="€%.2f"),
                    },
                    hide_index=True,
                    use_container_width=True,
                    key=f"pdf_editor_{pdf_file.file_id}",
                )
                included = edited_df[edited_df["Include"]]
                st.caption(f"{len(included)} of {len(edited_df)} selected · total {money(included['Amount (EUR)'].sum(), currency, rates)}")
                if st.button(f"Import {len(included)} expenses", type="primary", disabled=included.empty):
                    imported = skipped = 0
                    for _, row in included.iterrows():
                        raw_date = row["Date"]
                        expense_date = date.today() if pd.isna(raw_date) else pd.Timestamp(raw_date).date()
                        name = str(row["Description"])
                        amount = float(row["Amount (EUR)"])
                        # Skip rows already present so re-importing the same (or an
                        # overlapping) statement doesn't double-book — the root cause
                        # of the earlier duplicate-transaction cleanups.
                        if expense_exists(name, amount, expense_date.isoformat()):
                            skipped += 1
                            continue
                        add_budget_expense(name, row["Category"], amount, expense_date)
                        imported += 1
                    st.session_state.pop(cache_key, None)
                    if skipped:
                        st.success(f"Imported {imported} · skipped {skipped} duplicate{'s' if skipped != 1 else ''}.")
                    else:
                        st.success(f"Imported {imported} expenses.")
                    st.rerun()

    st.markdown("<div class='fd-section-title'>Add / update</div>", unsafe_allow_html=True)
    left, right = st.columns([1, 1.25])
    with left:
        with st.form("salary_form"):
            st.markdown(
                "<div style='font-size:13px;font-weight:800;color:var(--atlas-ink);"
                "text-transform:uppercase;letter-spacing:.4px;margin-bottom:10px'>💰 Monthly salary</div>",
                unsafe_allow_html=True,
            )
            salary_input = st.number_input("Net take-home (EUR)", min_value=0.0, value=float(salary), step=100.0)
            if st.form_submit_button("Save salary", type="primary"):
                set_monthly_salary(salary_input)
                st.success("Salary saved.")
                st.rerun()

        with st.form("expense_form", clear_on_submit=True):
            st.markdown(
                "<div style='font-size:13px;font-weight:800;color:var(--atlas-ink);"
                "text-transform:uppercase;letter-spacing:.4px;margin-bottom:10px'>➕ Add expense</div>",
                unsafe_allow_html=True,
            )
            expense_name = st.text_input("Name", placeholder="Rent, Netflix, electricity...")
            ec1, ec2 = st.columns(2)
            expense_category = ec1.selectbox(
                "Category", BUDGET_CATEGORIES,
                format_func=lambda c: f"{BUDGET_CATEGORY_ICONS.get(c, '🧾')} {c}",
            )
            expense_amount = ec2.number_input("Amount (EUR)", min_value=0.01, step=10.0)
            expense_date_input = st.date_input("Date", value=date.today())
            expense_recurring = st.checkbox("🔁 Repeats every month (e.g. rent, loan payment)")
            if st.form_submit_button("Add expense", type="primary"):
                if not expense_name.strip():
                    st.error("Add an expense name.")
                else:
                    add_budget_expense(
                        expense_name.strip(), expense_category, expense_amount, expense_date_input, expense_recurring
                    )
                    st.success("Expense added.")
                    st.rerun()

    with right:
        st.markdown(
            "<div style='font-size:13px;font-weight:800;color:var(--atlas-ink);"
            "text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px'>Category breakdown</div>",
            unsafe_allow_html=True,
        )
        category_totals = {}
        for item in expenses:
            category_totals[item["category"]] = category_totals.get(item["category"], 0.0) + float(item["amount_eur"] or 0)
        if category_totals:
            sorted_cats = dict(sorted(category_totals.items(), key=lambda item: item[1], reverse=True))
            cat_palette = ["#2563eb", "#16a34a", "#7c3aed", "#ca8a04", "#dc2626", "#0891b2",
                           "#f59e0b", "#64748b", "#ec4899", "#10b981", "#6366f1", "#94a3b8"]
            cat_labels_with_icons = [f"{BUDGET_CATEGORY_ICONS.get(c, '🧾')} {c}" for c in sorted_cats.keys()]
            fig_budget = go.Figure(go.Pie(
                labels=cat_labels_with_icons,
                values=list(sorted_cats.values()),
                hole=0.6,
                marker=dict(colors=cat_palette[: len(sorted_cats)], line=dict(color=plot_bg, width=2)),
                textinfo="percent",
                textfont=dict(color="#ffffff", size=11),
                hovertemplate="%{label}: " + ("€" if currency == "EUR" else currency + " ") + "%{value:,.0f} (%{percent})<extra></extra>",
            ))
            fig_budget.add_annotation(
                text=f"<b>{money(total_expenses, currency, rates)}</b><br><span style='font-size:10px'>per month</span>",
                x=0.5, y=0.5, showarrow=False, font=dict(color=plot_ink, size=13), align="center",
            )
            fig_budget.update_layout(
                height=260, margin=dict(l=0, r=0, t=8, b=0),
                paper_bgcolor=plot_bg, showlegend=True,
                legend=dict(orientation="v", x=1.0, y=0.5, font=dict(color=plot_muted, size=11), bgcolor="rgba(0,0,0,0)"),
            )
            st.plotly_chart(fig_budget, use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("Add your first monthly expense to see the breakdown.")

    st.markdown(f"<div class='fd-section-title'>{period_label} ({len(expenses)})</div>", unsafe_allow_html=True)
    if expenses:
        sorted_expenses = sorted(
            expenses,
            key=lambda item: item["expense_date"] or item["created_at"][:10],
            reverse=True,
        )
        table_df = pd.DataFrame([
            {
                "Date": item["expense_date"] or item["created_at"][:10],
                "Expense": item["name"],
                "Category": item["category"],
                "Amount": float(item["amount_eur"] or 0),
                "Share": (float(item["amount_eur"] or 0) / total_expenses * 100) if total_expenses else 0.0,
            }
            for item in sorted_expenses
        ])

        filter_col1, filter_col2 = st.columns([2, 3])
        cat_filter = filter_col1.multiselect(
            "Filter category",
            sorted({item["category"] for item in expenses}),
            format_func=lambda c: f"{BUDGET_CATEGORY_ICONS.get(c, '🧾')} {c}",
            placeholder="All categories",
            label_visibility="collapsed",
        )
        search_text = filter_col2.text_input(
            "Search", placeholder="Search by expense name…", label_visibility="collapsed"
        )

        view_df = table_df
        if cat_filter:
            view_df = view_df[view_df["Category"].isin(cat_filter)]
        if search_text.strip():
            view_df = view_df[view_df["Expense"].str.contains(search_text.strip(), case=False, na=False)]

        currency_symbol = "€" if currency == "EUR" else currency + " "
        event = st.dataframe(
            view_df,
            hide_index=True,
            use_container_width=True,
            height=420,
            column_config={
                "Category": st.column_config.TextColumn(),
                "Amount": st.column_config.NumberColumn(format=f"{currency_symbol}%.2f"),
                "Share": st.column_config.NumberColumn(format="%.1f%%"),
            },
            selection_mode="multi-row",
            on_select="rerun",
            key=f"budget_table_{selected_month}_{'-'.join(sorted(cat_filter))}_{search_text.strip()}",
        )

        selected_rows = event.selection.rows if event and event.selection else []
        del_col1, del_col2 = st.columns([3, 1])
        del_col1.caption(f"{len(selected_rows)} selected" if selected_rows else "Select rows in the table to delete them.")
        if del_col2.button("Delete selected", use_container_width=True, disabled=not selected_rows):
            ids_to_delete = [sorted_expenses[view_df.index[row]]["id"] for row in selected_rows]
            for expense_id in ids_to_delete:
                delete_budget_expense(expense_id)
            st.success(f"Removed {len(ids_to_delete)} expense(s).")
            st.rerun()
    else:
        st.info("No expenses recorded for this period." if selected_month != "All time" else "No expenses added yet.")

    # ── Spending trends: daily / weekly / monthly / yearly analytics ───────
    st.markdown("<div class='fd-section-title'>Spending trends</div>", unsafe_allow_html=True)
    if expenses:
        trend_col1, trend_col2 = st.columns([1, 3])
        granularity = trend_col1.selectbox("View by", ["Daily", "Weekly", "Monthly", "Yearly"], index=2)
        group_by_category = trend_col1.checkbox("Split by category", value=False)

        df = pd.DataFrame([
            {
                "date": pd.to_datetime(row["expense_date"] or row["created_at"][:10]),
                "category": row["category"],
                "amount": float(row["amount_eur"] or 0),
            }
            for row in expenses
        ])

        label_fmt = {
            "Daily": lambda d: d.strftime("%d %b"),
            "Weekly": lambda d: f"Week of {d.strftime('%d %b')}",
            "Monthly": lambda d: d.strftime("%b %Y"),
            "Yearly": lambda d: d.strftime("%Y"),
        }
        if granularity == "Daily":
            df["period"] = df["date"].dt.normalize()
        elif granularity == "Weekly":
            df["period"] = df["date"] - pd.to_timedelta(df["date"].dt.weekday, unit="D")
        elif granularity == "Monthly":
            df["period"] = df["date"].values.astype("datetime64[M]")
        else:  # Yearly
            df["period"] = df["date"].values.astype("datetime64[Y]")

        with trend_col2:
            if group_by_category:
                pivot = df.pivot_table(index="period", columns="category", values="amount", aggfunc="sum", fill_value=0)
                pivot = pivot.sort_index()
                fig_trend = go.Figure()
                cat_colors_trend = ["#2563eb", "#16a34a", "#7c3aed", "#ca8a04", "#dc2626", "#0891b2",
                                     "#f59e0b", "#64748b", "#ec4899", "#10b981", "#6366f1", "#94a3b8"]
                for i, cat in enumerate(pivot.columns):
                    fig_trend.add_trace(go.Bar(
                        x=[label_fmt[granularity](d) for d in pivot.index],
                        y=pivot[cat],
                        name=f"{BUDGET_CATEGORY_ICONS.get(cat, '🧾')} {cat}",
                        marker_color=cat_colors_trend[i % len(cat_colors_trend)],
                    ))
                fig_trend.update_layout(barmode="stack")
            else:
                totals = df.groupby("period")["amount"].sum().sort_index()
                fig_trend = go.Figure(go.Bar(
                    x=[label_fmt[granularity](d) for d in totals.index],
                    y=totals.values,
                    marker_color="#16806a",
                    text=[money(v, currency, rates) for v in totals.values],
                    textposition="outside",
                ))

            fig_trend.update_layout(
                height=300, margin=dict(l=0, r=0, t=8, b=0),
                paper_bgcolor=plot_bg, plot_bgcolor=plot_bg,
                xaxis=dict(showgrid=False, tickfont=dict(color=plot_muted, size=10)),
                yaxis=dict(showgrid=True, gridcolor=plot_line, tickfont=dict(color=plot_muted, size=10),
                           tickprefix="€" if currency == "EUR" else ""),
                legend=dict(orientation="h", y=1.15, font=dict(color=plot_muted, size=10), bgcolor="rgba(0,0,0,0)"),
                bargap=0.25,
            )
            st.plotly_chart(fig_trend, use_container_width=True, config={"displayModeBar": False})
    else:
        st.info("Add expenses with dates to see spending trends over time.")

else:
    st.markdown(
        """
        <div class='fd-header'>
          <div>
            <div class='fd-header-title'>Import & Manage</div>
            <div class='fd-header-sub'>Upload portfolio CSV or add holdings manually</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    left, right = st.columns(2)
    with left:
        st.subheader("Import CSV")
        uploaded = st.file_uploader("Portfolio CSV", type=["csv"])
        if uploaded and st.button("Import CSV", type="primary"):
            try:
                imported, updated = import_csv(uploaded)
                st.success(f"CSV imported: {imported} added, {updated} updated.")
                st.rerun()
            except Exception as error:
                st.error(str(error))
        st.subheader("Recent imports")
        imports = load_rows(
            "SELECT filename, source, imported, updated, total_rows, created_at FROM imports WHERE user_id = ? ORDER BY created_at DESC LIMIT 8",
            (LOCAL_USER_ID,),
        )
        st.markdown(holding_table([]).replace("<tbody></tbody>", "") if False else "", unsafe_allow_html=True)
        for item in imports:
            st.write(f"**{item['filename']}**")
            st.caption(f"{item['source']} · {item['imported']} added · {item['updated']} updated · {item['created_at']}")
    with right:
        st.subheader("Add holding")
        with st.form("add_holding"):
            name = st.text_input("Asset name")
            ticker = st.text_input("Ticker")
            market = st.selectbox("Market", ["India", "Global"])
            asset_class = st.selectbox("Asset class", ["Equities", "ETFs & Funds", "Fixed income", "Cash & others"])
            value_eur = st.number_input("Current value in EUR", min_value=0.01, step=100.0)
            return_percent = st.number_input("Return %", min_value=-100.0, max_value=1000.0, step=0.1)
            submitted = st.form_submit_button("Add holding", type="primary")
        if submitted:
            upsert_basic_holding(
                {
                    "name": name,
                    "ticker": ticker,
                    "market": market,
                    "value_eur": value_eur,
                    "return_percent": return_percent,
                    "asset_class": asset_class,
                    "quantity": None,
                    "average_cost": None,
                    "current_price": None,
                    "source_currency": "EUR",
                }
            )
            st.success("Holding added.")
            st.rerun()
