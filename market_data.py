"""Market data: curated symbol/classification maps and quote/FX fetchers.

Single source of truth — these maps were previously duplicated between
main.py and streamlit_app.py and had to be edited twice. Both import from
here now, as does portfolio_core.refresh_prices.

The maps are hand-maintained heuristics, not market data feeds:
  * Yahoo symbol maps cover instruments with a verified provider mapping;
    anything unmapped gets FX-only revaluation on refresh.
  * Cap buckets and barbell roles are portfolio-review opinions — review
    them periodically (market caps drift; roles are personal strategy).
  * Mutual-fund tickers have no AMFI scheme codes yet, so their NAVs are
    never treated as live quotes.

No Streamlit/FastAPI/DB imports — safe for tests and any future frontend.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

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


def yahoo_symbol(ticker: str, market: str) -> str | None:
    """Verified Yahoo Finance symbol for a holding, or None if unmapped."""
    if market == "India":
        return INDIA_YAHOO_SYMBOLS.get(ticker)
    return GLOBAL_YAHOO_SYMBOLS.get(ticker)


def classify_holding(ticker: str, asset_class: str) -> tuple[str, str]:
    """(asset_category, cap_bucket) from the curated maps."""
    if ticker in MUTUAL_FUND_TICKERS:
        return "Mutual fund", "Not applicable"
    if ticker in ETF_TICKERS or asset_class == "ETFs & Funds":
        return "ETF", "Not applicable"
    bucket = CAP_BUCKET_LOOKUP.get(ticker)
    if bucket:
        return "Stock", bucket
    return ("Stock", "Unclassified") if asset_class == "Equities" else ("Other", "Not applicable")


def classify_barbell(ticker: str, asset_category: str, cap_bucket: str) -> tuple[str, str]:
    """(barbell_role, reason) — Core / Upside / Review heuristic."""
    if ticker in BARBELL_CORE_TICKERS:
        return "Core", "Diversified, defensive, or liquid building block."
    if ticker in BARBELL_UPSIDE_TICKERS or (asset_category == "Stock" and cap_bucket == "Small cap"):
        return "Upside", "Intentional higher-risk exposure with asymmetric upside potential."
    return "Review", "Does not clearly fit the core or upside side of the current heuristic."


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Vermo/0.3"})
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


def fetch_reference_fx() -> dict[str, float]:
    """EUR-base reference rates from Frankfurter (ECB data)."""
    payload = fetch_json("https://api.frankfurter.dev/v1/latest?base=EUR&symbols=INR,USD,GBP")
    return {
        "EUR": 1.0,
        "INR": float(payload["rates"]["INR"]),
        "USD": float(payload["rates"]["USD"]),
        "GBP": float(payload["rates"]["GBP"]),
    }


def fetch_yahoo_quote(symbol: str) -> tuple[float, str]:
    """Delayed informational quote: (price, currency)."""
    encoded_symbol = urllib.parse.quote(symbol)
    payload = fetch_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}?range=1d&interval=1d")
    meta = payload["chart"]["result"][0]["meta"]
    return float(meta["regularMarketPrice"]), meta["currency"]
