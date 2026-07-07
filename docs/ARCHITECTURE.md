# Vermo — Architecture

> **Naming note:** the product is called **Vermo** (`pyproject.toml`, README).
> You will also see **Atlas** in older material: `MULTI_USER_ROADMAP.md`'s title
> and the `--atlas-*` CSS variables inside `streamlit_app.py`. They refer to the
> same app; prefer "Vermo" for anything new.

## What the app is

A personal-finance tracker with five capability areas:

1. **Portfolio tracking** — consolidated holdings across India and Global
   markets, valued in EUR with USD/INR display options, with daily snapshots
   and a performance-history chart.
2. **Price refresh** — reference FX (Frankfurter) plus delayed quotes (Yahoo
   Finance) for tickers with curated symbol mappings.
3. **Budget tracking** — salary vs. expenses, PDF bank-statement import with
   auto-categorization, recurring expenses, spending-trend analytics.
4. **Debt tracking** — balance/payment projections with interest-rate
   inference.
5. **Other assets** — manually tracked assets (real estate, cash, crypto, …)
   that roll into net worth.

It is currently a **local, single-machine, multi-account** app: the schema and
all queries are multi-user, login is enforced, but everything runs on SQLite on
one machine. `MULTI_USER_ROADMAP.md` is the staged plan to a hosted product.

## Process topology

There are **two processes** and **two SQLite databases**:

```
┌─────────────────────────┐        ┌──────────────────────────┐
│ Streamlit (port 8501)   │        │ FastAPI (port 8000)      │
│ streamlit_app.py        │        │ main.py                  │
│ — the primary UI —      │        │ — API + legacy dashboard │
│                         │        │   (index.html + app.js)  │
│ reads/writes SQLite     │  POST  │                          │
│ DIRECTLY for almost     │ ─────► │ /api/prices/refresh      │
│ everything              │ Bearer │ (the one cross-process   │
│                         │ token  │  call Streamlit makes)   │
└───────────┬─────────────┘        └────────────┬─────────────┘
            │                                   │
            ▼                                   ▼
   data/portfolio.db  ◄────────────────────────┘
   (holdings, snapshots, budget, debt, broker, manual assets)

   data/auth.db
   (users, sessions — written by auth/local_auth.py,
    read by BOTH processes to validate session tokens)
```

Key consequence: **Streamlit does not go through the API for data access.**
It opens `data/portfolio.db` directly. The FastAPI service exists for (a) the
legacy JS dashboard, (b) programmatic API access, and (c) the price-refresh
job that Streamlit triggers over HTTP. The roadmap's Stage 5 (React frontend)
would flip this: the API becomes the only data path.

## Modules

| File | Lines | Role |
|---|---|---|
| `streamlit_app.py` | ~2,700 | Primary UI. Login gate, DB init/migrations for budget/broker/manual-asset tables, PDF statement parsing, auto-categorization, recurring expenses, FX/debt/projection math, and all seven pages. |
| `main.py` | ~950 | FastAPI service. Portfolio schema init/migrations, CSV import parsers (template, transaction-history, India broker snapshot), price refresh, classification (asset category / cap bucket / barbell role), REST endpoints, serves the legacy dashboard. |
| `auth/local_auth.py` | ~480 | Local email+password auth. PBKDF2-SHA256 hashing, session tokens in `auth.db`, 7-day inactivity timeout. `resolve_session()` is Streamlit-free so FastAPI can import it. Streamlit-facing helpers: `require_login()`, `current_user()`, `logout()`. |
| `budget_db.py` | ~100 | Extracted budget-expense data layer (`add_expense`, `expense_exists` duplicate guard). Exists so this logic is importable and unit-testable — `streamlit_app.py` executes login/rendering at import time and cannot be imported by tests. **This is the pattern to follow when extracting more logic** (see DEVELOPMENT.md). |
| `statement_parser.py` | ~220 | Extracted bank/credit-card statement PDF parser (N26-style multi-line blocks + single-line fallback, date/amount parsing, keyword auto-categorization). Streamlit-free for the same testability reason. |
| `index.html`, `app.js`, `styles.css` | ~600 | Legacy vanilla-JS dashboard served by FastAPI at `/`. Portfolio-only; predates the Streamlit UI. |
| `run_streamlit.py` | 46 | Streamlit launcher with a Python 3.9 Protocol-dataclass workaround. |
| `tests/` | — | Pytest suite: auth (`test_auth.py`), budget dedup (`test_budget.py`), API auth (`test_api.py`). |

## Authentication flow

1. `streamlit_app.py` calls `require_login()` before any DB read. Unauthenticated
   users see a login/signup form and the script stops there.
2. On login, `local_auth` stores the session token in `st.session_state` and
   mirrors it into `st.query_params["session"]` (so browser refreshes survive).
3. After login, the module global `LOCAL_USER_ID` in `streamlit_app.py` is
   reassigned to the real session user's id — every helper that reads it as a
   global picks up the correct user for the rest of the script run.
4. **FastAPI never trusts a client-supplied user id.** Every data route depends
   on `require_user_id` (`main.py`), which requires an `Authorization: Bearer
   <token>` header, resolves it via `auth.local_auth.resolve_session()` against
   `auth.db`, and derives `user_id` from the session. Missing/invalid tokens
   get 401.
5. Sessions expire after 7 days of inactivity (`INACTIVITY_TIMEOUT`); each
   authenticated request touches `last_active_at`.
6. Password reset (`reset_password`) is email + new password with **no email
   round-trip** — acceptable for a local trust circle, must be replaced with a
   real reset flow (e.g. Supabase Auth) before hosting for strangers.

`main.py` also defines its own `LOCAL_USER_ID` constant — it is only the
default for internal helpers called directly from scripts/tests, never used to
authorize a route.

## Data flow highlights

- **CSV import (portfolio):** `main.py` sniffs the header row and dispatches to
  one of three parsers — the app's own template format, transaction-history
  exports (BUY/SELL rows consolidated by average cost), or India broker
  snapshots (`Stock Name`/`CMP`/`Qty`, INR converted at the prototype rate).
  Re-importing the same ticker+market updates in place (`UNIQUE(user_id,
  ticker, market)`).
- **PDF statement import (budget):** `streamlit_app.py` extracts text with
  `pypdf`, strips page boilerplate, splits into transaction blocks, parses
  dates/amounts, auto-categorizes by keyword, then shows an editable preview
  table. On import, `budget_db.expense_exists` (name+amount+date, category
  deliberately ignored) skips rows already present — re-importing an
  overlapping statement cannot double-book. This guard exists because silent
  duplicates previously required two rounds of manual data cleanup.
- **Recurring expenses:** `ensure_recurring_expenses()` materializes one
  concrete row per month from a recurring expense's earliest occurrence to the
  current month, using the most recent amount as the template. Idempotent per
  (name, category, month).
- **Snapshots:** one row per user/day/market (`All`, `India`, `Global`),
  recorded on dashboard access. The performance chart uses only these real
  snapshots — history starts the first day the app recorded one.
- **Price refresh:** FX first (Frankfurter, falls back to stored rates), then
  per-holding Yahoo quotes for tickers in the curated symbol maps. Unmapped
  holdings get FX-only revaluation. Results logged to `refresh_runs` with
  per-holding detail.
- **Classification:** `classify_holding` (Stock/Mutual fund/ETF/Other +
  large/mid/small-cap bucket) and `classify_barbell` (Core/Upside/Review) use
  curated ticker maps at the top of `main.py` (duplicated in
  `streamlit_app.py`). These are hand-maintained heuristics, not market data —
  review periodically.

## Deliberate design decisions

- **SQLite + direct access now, Postgres later.** All tables carry `user_id`
  with composite uniqueness so the Postgres/RLS migration (roadmap Stage 2) is
  mechanical. Migrations are non-destructive, additive, idempotent
  (`CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` checks + table rebuilds
  where constraints had to change).
- **`data/` is gitignored.** It holds personal financial data: the DBs, timestamped
  `.bak` DB backups, uploaded statements (`data/test_uploads/`), and manual
  `manual-*.csv` transcriptions. Nothing under `data/` may ever be committed.
- **Legacy-data claim:** pre-auth data was stored under the `local-user` id.
  `has_legacy_data()` / `claim_legacy_data()` in `streamlit_app.py` let the
  first real account adopt it. The JSON bootstrap in `init_database()` guards
  against re-running after a claim (checks row count across ALL users).
- **Barbell view is a review aid, not advice.** Core/Upside/Review mappings are
  user-editable opinions in code.

## Known limitations (tracked in MULTI_USER_ROADMAP.md)

- Streamlit reruns the whole script per interaction — fine locally, sluggish
  as a hosted product (Stage 5 addresses this).
- Most of `streamlit_app.py`'s logic (PDF parsing, recurring expenses,
  projections) is untested because the module can't be imported without a
  Streamlit session. Extraction into testable modules (the `budget_db.py`
  pattern) is the standing remediation.
- INR conversion for India snapshot imports uses a hardcoded prototype rate
  (`INR_PER_EUR` in `main.py`) at import time; live FX applies only on refresh.
- Mutual-fund rows from screenshots lack AMFI scheme codes, so they only get
  FX-only updates on refresh.
- No rate limiting, error monitoring, or email service yet (Stage 4).
