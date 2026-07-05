from __future__ import annotations

import csv
import io
import json
import sqlite3
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Lock
from typing import Literal, Optional
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, ValidationError

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATABASE_FILE = DATA_DIR / "portfolio.db"
LEGACY_DATA_FILE = DATA_DIR / "portfolio.json"
data_lock = Lock()

CSV_COLUMNS = ("name", "ticker", "market", "value_eur", "return_percent", "asset_class")
TRANSACTION_COLUMNS = ("date", "category", "type", "asset_class", "name", "symbol", "shares", "price", "amount", "fee", "currency")
INDIA_SNAPSHOT_COLUMNS = ("Stock Name", "Company Name", "CMP", "Portfolio Holdings", "Invested Value", "Qty")
INR_PER_EUR = Decimal("97.3")
DEFAULT_FX_RATES = {"EUR": 1.0, "INR": 97.3, "USD": 1.14, "GBP": 0.86}

# Every holding/snapshot/import row belongs to a user. Until login is wired into the
# running app, every call defaults to this single account so existing behavior is
# unchanged; once auth/local_auth.py is wired in, pass the real session user_id instead.
LOCAL_USER_ID = "702204ea-0bba-4e4d-8349-3a6d31adab42"
INDIA_YAHOO_SYMBOLS = {
    "BAJFINEQ": "BAJFINANCE.NS", "ICIBANEQ": "ICICIBANK.NS", "HDFBANEQ": "HDFCBANK.NS",
    "KOTMAHEQ": "KOTAKBANK.NS", "AMIORGEQ": "ACUTAAS.NS", "TCSLTDEQ": "TCS.NS",
    "DIXONEQ": "DIXON.NS", "RELINDEQ": "RELIANCE.NS", "TRELTDEQ": "TRENT.NS",
    "NIITECEQ": "COFORGE.NS", "HDFCMFGETFEQ": "HDFCGOLD.NS", "EICMOTEQ": "EICHERMOT.NS",
    "JIOFINEQ": "JIOFIN.NS", "FINEORGEQ": "FINEORG.NS", "CLEANEQ": "CLEAN.NS",
    "BHELTDEQ": "BHEL.NS", "ALKAMIEQ": "ALKYLAMINE.NS", "ASIPAIEQ": "ASIANPAINT.NS",
    "ITCLTDEQ": "ITC.NS", "HLLLTDEQ": "HINDUNILVR.NS", "EXIINDEQ": "EXIDEIND.NS",
    "HDFCLIFEEQ": "HDFCLIFE.NS", "LIQBENEQ": "LIQUIDBEES.NS",
    # Extended mappings
    "RAINBOWEQ": "RAINBOW.NS", "SAGILITYEQ": "SAGILITY.NS", "GODIGITEQ": "GODIGIT.NS",
    "HDBFSEQ": "HDBFS.NS", "ROSSARIEQ": "ROSSARI.NS", "HOMEFIRSTEQ": "HOMEFIRST.NS",
    "RATEGAINIQ": "RATEGAIN.NS", "KWILEQ": "KWIL.NS", "LOGMICEQ": "IZMO.NS",
    "LUMINDEQ": "LUMAXIND.NS", "MASFINEQ": "MASFIN.NS", "SUBLTDEQ": "SUBROS.NS",
    "DCALEQ": "DCAL.NS", "TARSONSIQ": "TARSONS.NS", "RSYINTEQ": "RSYSTEMS.NS",
    "KNRCONEQ": "KNRCON.NS", "ITCHOTELSEQ": "ITCHOTELS.NS", "RELFOOEQ": "RELAXO.NS",
}
GLOBAL_YAHOO_SYMBOLS = {
    "US67066G1040": "NVDA", "US5949181045": "MSFT", "US02079K3059": "GOOGL",
    "US64110L1061": "NFLX", "US30303M1027": "META", "US0231351067": "AMZN",
    "US00217D1000": "ASTS", "US69608A1088": "PLTR", "NL0010273215": "ASML",
    "US8740391003": "TSM", "US81762P1021": "NOW", "US11135F1012": "AVGO",
    "US7811541090": "RBRK", "US26740W1099": "QBTS",
    # Irish-domiciled ETFs listed on Euronext Amsterdam / London
    "IE00B4ND3602": "IGLN.L",    # iShares Physical Gold ETC (USD)
    "IE00BK5BQT80": "VWCE.AS",   # Vanguard FTSE All-World acc (EUR)
    "IE00BFMXXD54": "VUAA.DE",   # Vanguard S&P 500 acc (EUR)
    "IE00BGV5VN51": "WTAI.L",    # WisdomTree AI & Big Data (USD)
}
MUTUAL_FUND_TICKERS = {
    "MF-QUANT-MIDCAP", "MF-PGIM-INDIA-MIDCAP", "MF-BANDHAN-NIFTY50", "MF-HELIOS-FLEXICAP",
    "MF-QUANT-SMALLCAP-1", "MF-MOTILAL-MIDCAP", "MF-QUANT-SMALLCAP-2", "MF-PPFAS-FLEXICAP",
    "MF-AXIS-SMALLCAP",
}
ETF_TICKERS = {"HDFCMFGETFEQ", "LIQBENEQ", "IE00BGV5VN51", "IE00BFMXXD54", "IE00B4ND3602", "IE00BK5BQT80"}
CAP_BUCKETS = {
    "Large cap": {
        "BAJFINEQ", "ICIBANEQ", "HDFBANEQ", "KOTMAHEQ", "TCSLTDEQ", "RELINDEQ", "TRELTDEQ",
        "EICMOTEQ", "JIOFINEQ", "ASIPAIEQ", "ITCLTDEQ", "HLLLTDEQ", "HDFCLIFEEQ",
        "US67066G1040", "US5949181045", "US02079K3059", "US64110L1061", "US30303M1027",
        "US0231351067", "NL0010273215", "US8740391003", "US81762P1021", "US11135F1012",
    },
    "Mid cap": {
        "DIXONEQ", "NIITECEQ", "FINEORGEQ", "CLEANEQ", "RAINBOWEQ", "SAGILITYEQ", "BHELTDEQ",
        "ALKAMIEQ", "HOMEFIRSTEQ", "GODIGITEQ", "HDBFSEQ", "ROSSARIEQ", "US69608A1088",
    },
    "Small cap": {
        "AMIORGEQ", "RATEGAINIQ", "LUMINDEQ", "RSYINTEQ", "UNIECOMEQ", "MASFINEQ", "LOGMICEQ",
        "SUBLTDEQ", "DCALEQ", "TARSONSIQ", "EXIINDEQ", "KNRCONEQ", "ITCHOTELSEQ", "RELFOOEQ",
        "KWILEQ", "US00217D1000", "US7811541090", "US26740W1099",
    },
}
BARBELL_CORE_TICKERS = {
    "LIQBENEQ", "HDFCMFGETFEQ", "IE00B4ND3602", "IE00BFMXXD54", "IE00BK5BQT80",
    "MF-BANDHAN-NIFTY50", "MF-PPFAS-FLEXICAP",
}
BARBELL_UPSIDE_TICKERS = {
    "MF-QUANT-MIDCAP", "MF-PGIM-INDIA-MIDCAP", "MF-QUANT-SMALLCAP-1", "MF-QUANT-SMALLCAP-2",
    "MF-AXIS-SMALLCAP", "MF-MOTILAL-MIDCAP", "IE00BGV5VN51", "US00217D1000", "US69608A1088",
    "US7811541090", "US26740W1099",
}
CAP_BUCKET_LOOKUP: dict[str, str] = {ticker: bucket for bucket, tickers in CAP_BUCKETS.items() for ticker in tickers}

app = FastAPI(
    title="Vermo API",
    description="Consolidated portfolio tracker for global investments.",
    version="0.2.0",
)


class HoldingCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    ticker: str = Field(min_length=1, max_length=20)
    market: Literal["India", "Global"]
    value_eur: float = Field(gt=0)
    return_percent: float = Field(ge=-100, le=1000)
    asset_class: Literal["Equities", "ETFs & Funds", "Fixed income", "Cash & others"] = "Equities"
    quantity: Optional[float] = None
    average_cost: Optional[float] = None
    current_price: Optional[float] = None
    invested_eur: Optional[float] = None
    source_currency: Literal["EUR", "INR"] = "EUR"
    asset_category: Literal["Stock", "Mutual fund", "ETF", "Other"] = "Stock"
    cap_bucket: Literal["Large cap", "Mid cap", "Small cap", "Unclassified", "Not applicable"] = "Unclassified"
    barbell_role: Literal["Core", "Upside", "Review"] = "Review"
    barbell_reason: str = "Review whether this holding has a clear role."


class Holding(HoldingCreate):
    id: str
    user_id: str = LOCAL_USER_ID
    updated_at: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    return connection


def inferred_invested(value_eur: float, return_percent: float) -> float:
    return round(value_eur / (1 + return_percent / 100), 2) if return_percent > -100 else value_eur


def load_fx_rates(connection: Optional[sqlite3.Connection] = None) -> dict[str, float]:
    owns_connection = connection is None
    connection = connection or connect()
    try:
        rows = connection.execute("SELECT key, value FROM settings WHERE key LIKE 'fx_%'").fetchall()
        rates = {row["key"].replace("fx_", ""): float(row["value"]) for row in rows}
        return {**DEFAULT_FX_RATES, **rates}
    finally:
        if owns_connection:
            connection.close()


def set_fx_rates(connection: sqlite3.Connection, rates: dict[str, float]) -> None:
    for currency, rate in rates.items():
        connection.execute(
            """
            INSERT INTO settings VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (f"fx_{currency}", str(rate), utc_now()),
        )


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Vermo/0.3"})
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


def fetch_reference_fx() -> dict[str, float]:
    payload = fetch_json("https://api.frankfurter.dev/v1/latest?base=EUR&symbols=INR,USD,GBP")
    return {
        "EUR": 1.0,
        "INR": float(payload["rates"]["INR"]),
        "USD": float(payload["rates"]["USD"]),
        "GBP": float(payload["rates"]["GBP"]),
    }


def fetch_yahoo_quote(symbol: str) -> tuple[float, str]:
    encoded_symbol = urllib.parse.quote(symbol)
    payload = fetch_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}?range=1d&interval=1d")
    meta = payload["chart"]["result"][0]["meta"]
    return float(meta["regularMarketPrice"]), meta["currency"]


def classify_holding(ticker: str, asset_class: str) -> tuple[str, str]:
    if ticker in MUTUAL_FUND_TICKERS:
        return "Mutual fund", "Not applicable"
    if ticker in ETF_TICKERS or asset_class == "ETFs & Funds":
        return "ETF", "Not applicable"
    bucket = CAP_BUCKET_LOOKUP.get(ticker)
    if bucket:
        return "Stock", bucket
    return ("Stock", "Unclassified") if asset_class == "Equities" else ("Other", "Not applicable")


def classify_barbell(ticker: str, asset_category: str, cap_bucket: str) -> tuple[str, str]:
    if ticker in BARBELL_CORE_TICKERS:
        return "Core", "Diversified, defensive, or liquid building block."
    if ticker in BARBELL_UPSIDE_TICKERS or (asset_category == "Stock" and cap_bucket == "Small cap"):
        return "Upside", "Intentional higher-risk exposure with asymmetric upside potential."
    return "Review", "Does not clearly fit the core or upside side of the current heuristic."


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _rebuild_holdings_for_multiuser(connection: sqlite3.Connection) -> None:
    """holdings.UNIQUE(ticker, market) predates multi-user support and would block two
    users from owning the same ticker. Rebuild it as UNIQUE(user_id, ticker, market)."""
    if not _table_exists(connection, "holdings"):
        return
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(holdings)").fetchall()}
    if "user_id" in columns:
        return
    connection.execute("ALTER TABLE holdings RENAME TO holdings_old")
    connection.execute(
        """
        CREATE TABLE holdings (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'local-user',
            name TEXT NOT NULL,
            ticker TEXT NOT NULL,
            market TEXT NOT NULL CHECK (market IN ('India', 'Global')),
            value_eur REAL NOT NULL,
            return_percent REAL NOT NULL,
            asset_class TEXT NOT NULL,
            quantity REAL,
            average_cost REAL,
            current_price REAL,
            invested_eur REAL NOT NULL,
            source_currency TEXT NOT NULL DEFAULT 'EUR',
            updated_at TEXT NOT NULL,
            asset_category TEXT NOT NULL DEFAULT 'Stock',
            cap_bucket TEXT NOT NULL DEFAULT 'Unclassified',
            barbell_role TEXT NOT NULL DEFAULT 'Review',
            barbell_reason TEXT NOT NULL DEFAULT 'Review whether this holding has a clear role.',
            UNIQUE(user_id, ticker, market)
        )
        """
    )
    old_columns = {row["name"] for row in connection.execute("PRAGMA table_info(holdings_old)").fetchall()}
    shared = [c for c in (
        "id", "name", "ticker", "market", "value_eur", "return_percent", "asset_class",
        "quantity", "average_cost", "current_price", "invested_eur", "source_currency", "updated_at",
        "asset_category", "cap_bucket", "barbell_role", "barbell_reason",
    ) if c in old_columns]
    cols_sql = ", ".join(shared)
    connection.execute(f"INSERT INTO holdings (user_id, {cols_sql}) SELECT '{LOCAL_USER_ID}', {cols_sql} FROM holdings_old")
    connection.execute("DROP TABLE holdings_old")


def _rebuild_snapshots_for_multiuser(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "snapshots"):
        return
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(snapshots)").fetchall()}
    if "user_id" in columns:
        return
    connection.execute("ALTER TABLE snapshots RENAME TO snapshots_old")
    connection.execute(
        """
        CREATE TABLE snapshots (
            user_id TEXT NOT NULL DEFAULT 'local-user',
            snapshot_date TEXT NOT NULL,
            market TEXT NOT NULL CHECK (market IN ('All', 'India', 'Global')),
            net_worth_eur REAL NOT NULL,
            invested_eur REAL NOT NULL,
            holdings_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, snapshot_date, market)
        )
        """
    )
    connection.execute(
        f"""
        INSERT INTO snapshots (user_id, snapshot_date, market, net_worth_eur, invested_eur, holdings_count, created_at)
        SELECT '{LOCAL_USER_ID}', snapshot_date, market, net_worth_eur, invested_eur, holdings_count, created_at
        FROM snapshots_old
        """
    )
    connection.execute("DROP TABLE snapshots_old")


def _add_user_id_column(connection: sqlite3.Connection, table: str) -> None:
    if not _table_exists(connection, table):
        return
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if "user_id" not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT NOT NULL DEFAULT '{LOCAL_USER_ID}'")


def init_database() -> None:
    with connect() as connection:
        # Multi-user migration: rebuild tables whose old constraints (UNIQUE/PRIMARY KEY)
        # didn't account for user_id, then additively add user_id to the rest.
        _rebuild_holdings_for_multiuser(connection)
        _rebuild_snapshots_for_multiuser(connection)

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS holdings (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'local-user',
                name TEXT NOT NULL,
                ticker TEXT NOT NULL,
                market TEXT NOT NULL CHECK (market IN ('India', 'Global')),
                value_eur REAL NOT NULL,
                return_percent REAL NOT NULL,
                asset_class TEXT NOT NULL,
                quantity REAL,
                average_cost REAL,
                current_price REAL,
                invested_eur REAL NOT NULL,
                source_currency TEXT NOT NULL DEFAULT 'EUR',
                updated_at TEXT NOT NULL,
                asset_category TEXT NOT NULL DEFAULT 'Stock',
                cap_bucket TEXT NOT NULL DEFAULT 'Unclassified',
                barbell_role TEXT NOT NULL DEFAULT 'Review',
                barbell_reason TEXT NOT NULL DEFAULT 'Review whether this holding has a clear role.',
                UNIQUE(user_id, ticker, market)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS imports (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'local-user',
                filename TEXT NOT NULL,
                source TEXT NOT NULL,
                imported INTEGER NOT NULL,
                updated INTEGER NOT NULL,
                total_rows INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        _add_user_id_column(connection, "imports")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                user_id TEXT NOT NULL DEFAULT 'local-user',
                snapshot_date TEXT NOT NULL,
                market TEXT NOT NULL CHECK (market IN ('All', 'India', 'Global')),
                net_worth_eur REAL NOT NULL,
                invested_eur REAL NOT NULL,
                holdings_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, snapshot_date, market)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_runs (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'local-user',
                refreshed INTEGER NOT NULL,
                skipped INTEGER NOT NULL,
                failed INTEGER NOT NULL,
                fx_rate_inr REAL NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        _add_user_id_column(connection, "refresh_runs")
        for currency, rate in DEFAULT_FX_RATES.items():
            connection.execute(
                "INSERT OR IGNORE INTO settings VALUES (?, ?, ?)",
                (f"fx_{currency}", str(rate), utc_now()),
            )
        # This JSON bootstrap only applies to a genuinely empty database (e.g. first
        # ever run). Check across ALL users, not just LOCAL_USER_ID — once a user
        # claims their pre-login data, local-user's row count drops to 0 even though
        # the database is full, and re-triggering this would re-insert rows whose ids
        # already exist (now owned by a real account) and crash on a UNIQUE violation.
        total_count = connection.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
        if total_count == 0 and LEGACY_DATA_FILE.exists():
            legacy_rows = json.loads(LEGACY_DATA_FILE.read_text(encoding="utf-8"))
            for row in legacy_rows:
                payload = HoldingCreate(**row)
                upsert_holding(connection, payload, holding_id=row.get("id"))
        for row in connection.execute("SELECT id, ticker, asset_class FROM holdings WHERE user_id = ?", (LOCAL_USER_ID,)).fetchall():
            asset_category, cap_bucket = classify_holding(row["ticker"], row["asset_class"])
            barbell_role, barbell_reason = classify_barbell(row["ticker"], asset_category, cap_bucket)
            connection.execute(
                "UPDATE holdings SET asset_category = ?, cap_bucket = ?, barbell_role = ?, barbell_reason = ? WHERE id = ?",
                (asset_category, cap_bucket, barbell_role, barbell_reason, row["id"]),
            )
        record_snapshots(connection)


def row_to_holding(row: sqlite3.Row) -> Holding:
    return Holding(**dict(row))


def load_holdings(user_id: str = LOCAL_USER_ID) -> list[Holding]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM holdings WHERE user_id = ? ORDER BY value_eur DESC", (user_id,)
        ).fetchall()
    return [row_to_holding(row) for row in rows]


def record_snapshots(connection: sqlite3.Connection, snapshot_date: Optional[str] = None, user_id: str = LOCAL_USER_ID) -> None:
    snapshot_date = snapshot_date or date.today().isoformat()
    rows = [row_to_holding(row) for row in connection.execute("SELECT * FROM holdings WHERE user_id = ?", (user_id,)).fetchall()]
    for market in ("All", "India", "Global"):
        holdings = rows if market == "All" else [item for item in rows if item.market == market]
        summary = build_summary(holdings)
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
            (user_id, snapshot_date, market, summary["net_worth_eur"], summary["invested_eur"], len(holdings), utc_now()),
        )


def list_snapshots(user_id: str = LOCAL_USER_ID) -> list[dict]:
    with connect() as connection:
        return [
            dict(row) for row in connection.execute(
                "SELECT * FROM snapshots WHERE user_id = ? ORDER BY snapshot_date, market", (user_id,)
            ).fetchall()
        ]


def upsert_holding(connection: sqlite3.Connection, payload: HoldingCreate, holding_id: Optional[str] = None, user_id: str = LOCAL_USER_ID) -> bool:
    existing = connection.execute(
        "SELECT id FROM holdings WHERE ticker = ? AND market = ? AND user_id = ?",
        (payload.ticker.upper(), payload.market, user_id),
    ).fetchone()
    values = payload.model_dump()
    values["ticker"] = payload.ticker.upper()
    values["user_id"] = user_id
    values["asset_category"], values["cap_bucket"] = classify_holding(values["ticker"], payload.asset_class)
    values["barbell_role"], values["barbell_reason"] = classify_barbell(values["ticker"], values["asset_category"], values["cap_bucket"])
    values["invested_eur"] = payload.invested_eur or inferred_invested(payload.value_eur, payload.return_percent)
    values["updated_at"] = utc_now()
    values["id"] = existing["id"] if existing else (holding_id or str(uuid4()))
    connection.execute(
        """
        INSERT INTO holdings (
            id, user_id, name, ticker, market, value_eur, return_percent, asset_class,
            quantity, average_cost, current_price, invested_eur, source_currency, updated_at
            , asset_category, cap_bucket
            , barbell_role, barbell_reason
        ) VALUES (
            :id, :user_id, :name, :ticker, :market, :value_eur, :return_percent, :asset_class,
            :quantity, :average_cost, :current_price, :invested_eur, :source_currency, :updated_at
            , :asset_category, :cap_bucket
            , :barbell_role, :barbell_reason
        )
        ON CONFLICT(user_id, ticker, market) DO UPDATE SET
            name = excluded.name,
            value_eur = excluded.value_eur,
            return_percent = excluded.return_percent,
            asset_class = excluded.asset_class,
            quantity = excluded.quantity,
            average_cost = excluded.average_cost,
            current_price = excluded.current_price,
            invested_eur = excluded.invested_eur,
            source_currency = excluded.source_currency,
            asset_category = excluded.asset_category,
            cap_bucket = excluded.cap_bucket,
            barbell_role = excluded.barbell_role,
            barbell_reason = excluded.barbell_reason,
            updated_at = excluded.updated_at
        """,
        values,
    )
    return existing is None


def parse_decimal(value: str, row_number: int, column: str) -> Decimal:
    try:
        return Decimal(value or "0")
    except InvalidOperation as error:
        raise ValueError(f'Row {row_number}: "{column}" must be a number.') from error


def parse_portfolio_rows(reader: csv.DictReader) -> list[HoldingCreate]:
    parsed_rows = []
    for row_number, row in enumerate(reader, start=2):
        try:
            parsed_rows.append(
                HoldingCreate(
                    name=row["name"].strip(),
                    ticker=row["ticker"].strip().upper(),
                    market=row["market"].strip(),
                    value_eur=row["value_eur"].strip(),
                    return_percent=row["return_percent"].strip(),
                    asset_class=row["asset_class"].strip(),
                )
            )
        except (ValidationError, AttributeError) as error:
            raise ValueError(f"Row {row_number}: {error}") from error
    return parsed_rows


def parse_transaction_rows(reader: csv.DictReader) -> list[HoldingCreate]:
    positions = defaultdict(lambda: {"name": "", "asset_class": "", "shares": Decimal("0"), "cost": Decimal("0"), "price": Decimal("0")})
    for row_number, row in enumerate(reader, start=2):
        if row["category"] != "TRADING" or row["type"] not in {"BUY", "SELL"} or not row["symbol"]:
            continue
        if row["currency"] != "EUR":
            raise ValueError(f"Row {row_number}: only EUR transactions are currently supported.")
        position = positions[row["symbol"].strip().upper()]
        shares = abs(parse_decimal(row["shares"], row_number, "shares"))
        price = parse_decimal(row["price"], row_number, "price")
        fee = abs(parse_decimal(row["fee"], row_number, "fee"))
        amount = abs(parse_decimal(row["amount"], row_number, "amount"))
        position["name"] = row["name"].strip()
        position["asset_class"] = "ETFs & Funds" if row["asset_class"] == "FUND" else "Equities"
        position["price"] = price
        if row["type"] == "BUY":
            position["shares"] += shares
            position["cost"] += amount + fee
        elif shares > position["shares"]:
            raise ValueError(f"Row {row_number}: sell quantity exceeds available shares for {row['symbol']}.")
        elif position["shares"]:
            average_cost = position["cost"] / position["shares"]
            position["shares"] -= shares
            position["cost"] -= average_cost * shares

    holdings = []
    for symbol, position in positions.items():
        if position["shares"] <= 0:
            continue
        value_eur = position["shares"] * position["price"]
        return_percent = (value_eur / position["cost"] - 1) * 100 if position["cost"] else Decimal("0")
        holdings.append(
            HoldingCreate(
                name=position["name"],
                ticker=symbol,
                market="Global",
                value_eur=round(float(value_eur), 2),
                return_percent=round(float(return_percent), 2),
                asset_class=position["asset_class"],
                quantity=float(position["shares"]),
                average_cost=round(float(position["cost"] / position["shares"]), 4),
                current_price=float(position["price"]),
                invested_eur=round(float(position["cost"]), 2),
            )
        )
    return holdings


def parse_india_snapshot_rows(reader: csv.DictReader) -> list[HoldingCreate]:
    holdings = []
    inr_per_eur = Decimal(str(load_fx_rates()["INR"]))
    for row_number, row in enumerate(reader, start=2):
        quantity = parse_decimal(row["Qty"], row_number, "Qty")
        current_price = parse_decimal(row["CMP"], row_number, "CMP")
        invested_inr = parse_decimal(row["Invested Value"], row_number, "Invested Value")
        if quantity <= 0:
            continue
        current_value_inr = quantity * current_price
        value_eur = current_value_inr / inr_per_eur
        return_percent = (current_value_inr / invested_inr - 1) * 100 if invested_inr else Decimal("0")
        holdings.append(
            HoldingCreate(
                name=row["Company Name"].strip(),
                ticker=row["Stock Name"].strip().upper(),
                market="India",
                value_eur=round(float(value_eur), 2),
                return_percent=round(float(return_percent), 2),
                asset_class="Equities",
                quantity=float(quantity),
                average_cost=round(float(invested_inr / quantity), 4) if quantity else None,
                current_price=float(current_price),
                invested_eur=round(float(invested_inr / inr_per_eur), 2),
                source_currency="INR",
            )
        )
    return holdings


def build_summary(holdings: list[Holding]) -> dict:
    total_value = 0.0
    invested = 0.0
    geography: dict[str, float] = {"India": 0.0, "Global": 0.0}
    asset_classes: dict[str, float] = {"Equities": 0.0, "ETFs & Funds": 0.0, "Fixed income": 0.0, "Cash & others": 0.0}
    asset_categories: dict[str, float] = {"Stock": 0.0, "Mutual fund": 0.0, "ETF": 0.0, "Other": 0.0}
    cap_buckets: dict[str, float] = {"Large cap": 0.0, "Mid cap": 0.0, "Small cap": 0.0, "Unclassified": 0.0}
    barbell_roles: dict[str, float] = {"Core": 0.0, "Upside": 0.0, "Review": 0.0}
    for h in holdings:
        v = h.value_eur
        total_value += v
        invested += h.invested_eur or 0
        if h.market in geography:
            geography[h.market] += v
        if h.asset_class in asset_classes:
            asset_classes[h.asset_class] += v
        if h.asset_category in asset_categories:
            asset_categories[h.asset_category] += v
        if h.asset_category == "Stock" and h.cap_bucket in cap_buckets:
            cap_buckets[h.cap_bucket] += v
        if h.barbell_role in barbell_roles:
            barbell_roles[h.barbell_role] += v
    stock_value = asset_categories["Stock"]

    def pct(val: float, base: float) -> float:
        return round(val / base * 100, 1) if base else 0.0

    return {
        "net_worth_eur": round(total_value, 2),
        "invested_eur": round(invested, 2),
        "total_returns_eur": round(total_value - invested, 2),
        "total_return_percent": round((total_value - invested) / invested * 100, 1) if invested else 0,
        "day_change_eur": round(total_value * 0.0048, 2),
        "geography": {k: pct(v, total_value) for k, v in geography.items()},
        "asset_classes": {k: pct(v, total_value) for k, v in asset_classes.items()},
        "asset_categories": {k: pct(v, total_value) for k, v in asset_categories.items()},
        "cap_buckets": {k: pct(v, stock_value) for k, v in cap_buckets.items()},
        "barbell_roles": {k: pct(v, total_value) for k, v in barbell_roles.items()},
    }


def build_health(summary: dict) -> dict:
    score = 92
    signals = []
    if summary["geography"]["India"] > 45:
        score -= 10
        signals.append("India exposure is above the 45% diversification guideline.")
    if summary["geography"]["Global"] < 20:
        score -= 4
        signals.append("Global exposure is below 20%.")
    return {"score": max(score, 0), "label": "Looking healthy" if score >= 75 else "Needs attention", "signals": signals}


def build_suggestions(summary: dict) -> list[dict]:
    geography = summary["geography"]
    largest_market = max(geography, key=geography.get)
    next_market = min(geography, key=geography.get)
    return [
        {"level": "warning", "title": f"Review {largest_market} exposure", "description": f"{largest_market} is {geography[largest_market]}% of your portfolio.", "action": "Explore diversification"},
        {"level": "good", "title": f"Review {next_market} allocation", "description": f"{next_market} is currently {geography[next_market]}% of your portfolio.", "action": "View opportunities"},
        {"level": "info", "title": "Currency balance", "description": "Your EUR exposure helps offset INR volatility.", "action": "See currency view"},
    ]


@app.on_event("startup")
def startup() -> None:
    init_database()


@app.get("/api/dashboard")
def dashboard(user_id: str = LOCAL_USER_ID) -> dict:
    with connect() as connection:
        record_snapshots(connection, user_id=user_id)
        holdings = [
            row_to_holding(row) for row in connection.execute(
                "SELECT * FROM holdings WHERE user_id = ? ORDER BY value_eur DESC", (user_id,)
            ).fetchall()
        ]
        summary = build_summary(holdings)
        snapshots = [
            dict(row) for row in connection.execute(
                "SELECT * FROM snapshots WHERE user_id = ? ORDER BY snapshot_date, market", (user_id,)
            ).fetchall()
        ]
        imports = [
            dict(row) for row in connection.execute(
                "SELECT * FROM imports WHERE user_id = ? ORDER BY created_at DESC LIMIT 5", (user_id,)
            ).fetchall()
        ]
        updated_row = connection.execute(
            """
            SELECT MAX(updated_at) AS updated_at FROM (
                SELECT MAX(updated_at) AS updated_at FROM holdings WHERE user_id = ?
                UNION ALL
                SELECT MAX(created_at) AS updated_at FROM imports WHERE user_id = ?
            )
            """,
            (user_id, user_id),
        ).fetchone()
        refresh_row = connection.execute(
            "SELECT * FROM refresh_runs WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,)
        ).fetchone()
        fx = load_fx_rates(connection)
    return {
        "summary": summary,
        "health": build_health(summary),
        "suggestions": build_suggestions(summary),
        "holdings": holdings,
        "snapshots": snapshots,
        "imports": imports,
        "last_updated_at": updated_row["updated_at"] if updated_row else None,
        "fx_rates": fx,
        "latest_refresh": dict(refresh_row) if refresh_row else None,
    }


@app.get("/api/holdings", response_model=list[Holding])
def list_holdings(user_id: str = LOCAL_USER_ID) -> list[Holding]:
    return load_holdings(user_id)


@app.get("/api/imports")
def list_imports(user_id: str = LOCAL_USER_ID) -> list[dict]:
    with connect() as connection:
        return [
            dict(row) for row in connection.execute(
                "SELECT * FROM imports WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
            ).fetchall()
        ]


@app.get("/api/snapshots")
def snapshots(user_id: str = LOCAL_USER_ID) -> list[dict]:
    return list_snapshots(user_id)


def latest_updated_at(user_id: str = LOCAL_USER_ID) -> Optional[str]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT MAX(updated_at) AS updated_at FROM (
                SELECT MAX(updated_at) AS updated_at FROM holdings WHERE user_id = ?
                UNION ALL
                SELECT MAX(created_at) AS updated_at FROM imports WHERE user_id = ?
            )
            """,
            (user_id, user_id),
        ).fetchone()
    return row["updated_at"]


def latest_refresh(user_id: str = LOCAL_USER_ID) -> Optional[dict]:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM refresh_runs WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def yahoo_symbol(holding: Holding) -> Optional[str]:
    if holding.market == "India":
        return INDIA_YAHOO_SYMBOLS.get(holding.ticker)
    return GLOBAL_YAHOO_SYMBOLS.get(holding.ticker)


@app.post("/api/prices/refresh")
def refresh_prices(user_id: str = LOCAL_USER_ID) -> dict:
    details = []
    refreshed = 0
    skipped = 0
    failed = 0
    with data_lock, connect() as connection:
        fx_rates = load_fx_rates(connection)
        previous_fx_rates = dict(fx_rates)
        try:
            fx_rates = fetch_reference_fx()
            set_fx_rates(connection, fx_rates)
            details.append({"type": "fx", "status": "refreshed", "message": f'EUR/INR reference rate: {fx_rates["INR"]:.4f}'})
        except Exception as error:
            details.append({"type": "fx", "status": "failed", "message": f"FX refresh failed; retained previous rate. {error}"})

        holdings = [
            row_to_holding(row) for row in connection.execute(
                "SELECT * FROM holdings WHERE user_id = ?", (user_id,)
            ).fetchall()
        ]
        symbols = {symbol for holding in holdings if (symbol := yahoo_symbol(holding)) and holding.quantity is not None}
        quotes = {}
        quote_errors = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch_yahoo_quote, symbol): symbol for symbol in symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    quotes[symbol] = future.result()
                except Exception as error:
                    quote_errors[symbol] = str(error)
        for holding in holdings:
            invested_eur = holding.invested_eur or inferred_invested(holding.value_eur, holding.return_percent)
            if holding.source_currency == "INR":
                if holding.quantity is not None and holding.average_cost is not None:
                    invested_eur = holding.quantity * holding.average_cost / fx_rates["INR"]
                else:
                    invested_eur *= previous_fx_rates["INR"] / fx_rates["INR"]
                if holding.quantity is not None and holding.current_price is not None:
                    fx_value_eur = holding.quantity * holding.current_price / fx_rates["INR"]
                else:
                    fx_value_eur = holding.value_eur * previous_fx_rates["INR"] / fx_rates["INR"]
                fx_return = (fx_value_eur / invested_eur - 1) * 100 if invested_eur else 0
                connection.execute(
                    "UPDATE holdings SET value_eur = ?, invested_eur = ?, return_percent = ?, updated_at = ? WHERE id = ?",
                    (round(fx_value_eur, 2), round(invested_eur, 2), round(fx_return, 2), utc_now(), holding.id),
                )
            symbol = yahoo_symbol(holding)
            if not symbol or holding.quantity is None:
                skipped += 1
                details.append({"ticker": holding.ticker, "status": "skipped", "message": "FX updated; no verified live-quote mapping."})
                continue
            try:
                if symbol in quote_errors:
                    raise ValueError(quote_errors[symbol])
                quote, quote_currency = quotes[symbol]
                if quote_currency not in fx_rates:
                    raise ValueError(f"Unsupported quote currency: {quote_currency}")
                price_eur = quote / fx_rates[quote_currency]
                value_eur = holding.quantity * price_eur
                return_percent = (value_eur / invested_eur - 1) * 100 if invested_eur else 0
                displayed_price = quote if holding.market == "India" else price_eur
                connection.execute(
                    """
                    UPDATE holdings
                    SET current_price = ?, value_eur = ?, return_percent = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (round(displayed_price, 4), round(value_eur, 2), round(return_percent, 2), utc_now(), holding.id),
                )
                refreshed += 1
            except Exception as error:
                failed += 1
                details.append({"ticker": holding.ticker, "symbol": symbol, "status": "failed", "message": str(error)})

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


@app.post("/api/holdings", response_model=Holding, status_code=201)
def add_holding(payload: HoldingCreate, user_id: str = LOCAL_USER_ID) -> Holding:
    with data_lock, connect() as connection:
        upsert_holding(connection, payload, user_id=user_id)
        record_snapshots(connection, user_id=user_id)
        row = connection.execute(
            "SELECT * FROM holdings WHERE ticker = ? AND market = ? AND user_id = ?",
            (payload.ticker.upper(), payload.market, user_id),
        ).fetchone()
    return row_to_holding(row)


@app.post("/api/holdings/import")
async def import_holdings(file: UploadFile = File(...), user_id: str = LOCAL_USER_ID) -> dict:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a CSV file.")
    try:
        contents = (await file.read()).decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=400, detail="CSV file must use UTF-8 encoding.") from error
    reader = csv.DictReader(io.StringIO(contents))
    columns = set(reader.fieldnames or [])
    try:
        if set(CSV_COLUMNS).issubset(columns):
            parsed_rows, source = parse_portfolio_rows(reader), "portfolio"
        elif set(TRANSACTION_COLUMNS).issubset(columns):
            parsed_rows, source = parse_transaction_rows(reader), "transactions"
        elif set(INDIA_SNAPSHOT_COLUMNS).issubset(columns):
            parsed_rows, source = parse_india_snapshot_rows(reader), "india_snapshot"
        else:
            raise ValueError(f'CSV format not recognized. Use columns: {", ".join(CSV_COLUMNS)}')
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not parsed_rows:
        raise HTTPException(status_code=400, detail="CSV file does not contain any holdings.")

    with data_lock, connect() as connection:
        imported = sum(upsert_holding(connection, payload, user_id=user_id) for payload in parsed_rows)
        updated = len(parsed_rows) - imported
        connection.execute(
            "INSERT INTO imports (id, user_id, filename, source, imported, updated, total_rows, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid4()), user_id, file.filename, source, imported, updated, len(parsed_rows), utc_now()),
        )
        record_snapshots(connection, user_id=user_id)
    return {"source": source, "imported": imported, "updated": updated, "total_rows": len(parsed_rows)}


@app.delete("/api/holdings/{holding_id}", status_code=204, response_class=Response)
def delete_holding(holding_id: str, user_id: str = LOCAL_USER_ID) -> Response:
    with data_lock, connect() as connection:
        result = connection.execute(
            "DELETE FROM holdings WHERE id = ? AND user_id = ?", (holding_id, user_id)
        )
        if not result.rowcount:
            raise HTTPException(status_code=404, detail="Holding not found")
        record_snapshots(connection, user_id=user_id)
    return Response(status_code=204)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.get("/styles.css")
def styles() -> FileResponse:
    return FileResponse(BASE_DIR / "styles.css", media_type="text/css")


@app.get("/app.js")
def javascript() -> FileResponse:
    return FileResponse(BASE_DIR / "app.js", media_type="text/javascript")


@app.get("/portfolio-template.csv")
def portfolio_template() -> FileResponse:
    return FileResponse(BASE_DIR / "templates" / "portfolio-template.csv", filename="vermo-template.csv", media_type="text/csv")
