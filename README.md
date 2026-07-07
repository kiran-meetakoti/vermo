# Vermo

A personal-finance tracker for consolidating investments across India,
Germany, and global markets — portfolio, budget, debt, and net worth in one
place. Streamlit UI + FastAPI backend, SQLite storage, local multi-account
auth, on a staged path to a hosted multi-tenant product.

## Features

- **Portfolio** — consolidated EUR portfolio (EUR/USD/INR display), CSV import
  (own template, transaction-history exports, India broker snapshots), daily
  snapshots with a real performance-history chart, market-cap and
  barbell-strategy classification, health & diversification suggestions.
- **Prices** — reference FX (Frankfurter) + delayed Yahoo Finance quotes for
  mapped instruments; explicit refreshed / FX-only / failed reporting.
- **Budget** — salary vs. expenses, PDF bank-statement import with
  auto-categorization and a duplicate-import guard, recurring expenses,
  daily/weekly/monthly/yearly spending trends.
- **Debt tracker** — payoff projections with interest-rate inference.
- **Other assets** — manually tracked assets (real estate, cash, crypto, …)
  rolled into net worth.
- **Accounts** — email+password login, per-user data isolation, session
  tokens shared between the UI and the API.

## Quick start

Requires Python ≥ 3.9 and [uv](https://docs.astral.sh/uv/).

```bash
cd ~/Documents/FinApp
uv sync

# Terminal 1 — API + legacy dashboard (http://localhost:8000, docs at /docs)
uv run uvicorn main:app --reload --port 8000

# Terminal 2 — primary UI (http://localhost:8501)
uv run streamlit run streamlit_app.py
```

First run creates the databases under `data/` and shows a signup form.
`data/` is gitignored — it contains personal financial data.

```bash
uv run pytest   # run the test suite
```

## Documentation

| Doc | What's in it |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System overview, process topology, auth flow, module map, design decisions |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Every table, schema conventions, migration rules |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Setup, testing, conventions, how-to recipes for extending the app |
| [MULTI_USER_ROADMAP.md](MULTI_USER_ROADMAP.md) | Staged plan from local app to hosted product (current status inside) |
| [CLAUDE.md](CLAUDE.md) | Working agreements for AI-assisted development |

## Importing data

- **Portfolio CSV**: download the template at
  `http://localhost:8000/portfolio-template.csv`, fill it in, and use
  **Import CSV** in the UI. Markets: `India`, `Global`. Asset classes:
  `Equities`, `ETFs & Funds`, `Fixed income`, `Cash & others`. Re-importing a
  ticker+market updates the existing holding. Transaction-history exports
  (BUY/SELL rows) and India broker snapshots are auto-detected.
- **Bank/credit-card statements (PDF)**: Budget tracker → upload → review the
  editable preview → import. Already-imported rows are skipped automatically.

## Status & disclaimers

Runs locally today; multi-user schema and auth are in place, hosting is
planned (see the roadmap). The barbell view and all suggestions are
portfolio-review aids, **not** personalized financial advice. Market-cap and
barbell mappings are curated in `main.py` — review them periodically.
