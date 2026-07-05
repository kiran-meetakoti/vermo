# Vermo

A FastAPI + Streamlit portfolio tracker for consolidating investments across India, Germany, and global markets.

## Run in VS Code

1. Open this folder in VS Code.
2. Create the environment and install dependencies with `uv`:

```bash
cd ~/Documents/FinApp
uv sync
```

3. Open **Run and Debug** and select **Run Vermo FastAPI**.
4. Visit `http://localhost:8000`.

You can also run it directly:

```bash
cd ~/Documents/FinApp
uv run uvicorn main:app --reload --port 8000
```

If you are running the command from another directory, specify the project explicitly:

```bash
python3 -m uv run --project ~/Documents/FinApp uvicorn main:app --reload --port 8000
```

FastAPI API documentation is available at `http://localhost:8000/docs`.

## Import a CSV portfolio

Download the template from `http://localhost:8000/portfolio-template.csv`, update the rows, and click **Import CSV** in the dashboard.

Required columns:

```csv
name,ticker,market,value_eur,return_percent,asset_class
Reliance Industries,RELIANCE,India,23840,14.8,Equities
Vanguard FTSE All-World,VWCE,Global,19760,9.2,ETFs & Funds
```

Supported markets are `India` and `Global`. Supported asset classes are `Equities`, `ETFs & Funds`, `Fixed income`, and `Cash & others`. Values are currently imported in EUR. Importing the same ticker and market again updates the existing holding.

The importer also accepts transaction-history CSV exports with `BUY` and `SELL` rows. Open positions are consolidated as `Global` holdings using average-cost accounting. The latest recorded transaction price is used as a provisional valuation until live market prices are connected.

India broker portfolio snapshots with `Stock Name`, `Company Name`, `CMP`, `Invested Value`, and `Qty` columns are supported too. INR values currently use the prototype rate in `main.py`; live FX conversion is the next planned improvement.

Portfolio data is stored locally in `data/portfolio.db`. The database also keeps an import history so refreshed broker exports can update existing positions without creating duplicates.

Manual holdings transcribed from screenshots can be stored as ignored `data/manual-*.csv` files and imported into SQLite. Keep these files local because they contain personal financial information.

The dashboard records one SQLite portfolio snapshot per day for `All`, `India`, and `Global`. The performance chart uses these real snapshots and starts building history from the first recorded day. Recent import timestamps are shown on the Overview page.

## Refresh prices

Click **Refresh prices** to fetch reference FX and delayed informational market quotes. EUR/INR and EUR/USD rates use Frankfurter reference data. Mapped stocks and ETFs use Yahoo Finance delayed quotes. Instruments without a verified provider mapping keep their imported price while their EUR value is adjusted for the latest FX rate. The dashboard reports refreshed, FX-only, and failed counts explicitly.

Mutual-fund screenshot rows do not have exact AMFI scheme codes yet, so they currently receive FX-only updates. Add exact scheme mappings before treating their NAV values as refreshed.

## Asset classification

Holdings are separated into stocks, mutual funds, ETFs, and other assets. Stock holdings are grouped into large-cap, mid-cap, small-cap, and unclassified buckets using the curated mappings in `main.py`. Review these mappings periodically because market-cap classifications can change over time.

## Barbell strategy view

The dashboard includes a configurable heuristic view with three roles:

- `Core`: diversified, defensive, or liquid building blocks
- `Upside`: intentional higher-risk exposure with asymmetric upside potential
- `Review`: holdings that do not clearly fit either side yet

This is a portfolio-review aid, not personalized financial advice. The mappings live in `main.py` and should be adjusted to your goals, risk tolerance, time horizon, and definition of a barbell strategy.

## Included

- Consolidated EUR portfolio with EUR, USD, and INR display options
- FastAPI dashboard and holdings endpoints
- CSV portfolio import with a downloadable template
- SQLite persistence with import history
- Daily portfolio snapshots and a real performance-history chart
- Stock, mutual-fund, and ETF separation with curated equity market-cap buckets
- Configurable barbell-strategy roles with a review bucket
- Holdings search, market filters, sorting, cost basis, quantity, and profit/loss
- Portfolio health and diversification suggestions
- VS Code debug configuration
