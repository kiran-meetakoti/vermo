# Vermo — Data Model

Two SQLite databases live under `data/` (gitignored — personal financial data,
never committed):

- **`data/portfolio.db`** — all financial data. Schema owned by two places:
  `main.py::init_database()` (holdings/imports/snapshots/settings/refresh_runs)
  and `streamlit_app.py::init_budget_tables() / init_manual_assets_table() /
  init_broker_tables()` (budget/manual/broker tables). `budget_db.ensure_schema()`
  has a canonical copy of `budget_expenses` for tests.
- **`data/auth.db`** — users and sessions. Schema owned by
  `auth/local_auth.py::init_auth_db()`.

Timestamped `data/portfolio.db.bak-*` files are manual backups taken before
risky operations — keep making them before schema experiments.

## Conventions

- **Every per-user table has `user_id TEXT NOT NULL DEFAULT 'local-user'`.**
  `'local-user'` is the pre-auth legacy owner; real rows carry a UUID from
  `auth.db::users.id`. Any new table holding user data MUST include `user_id`
  and include it in every uniqueness constraint and every query's WHERE clause.
- IDs are UUID4 strings (`TEXT PRIMARY KEY`). Roadmap Stage 2 maps these to
  Postgres `uuid`.
- Timestamps are ISO-8601 UTC strings from `utc_now()`.
- Dates (snapshot/expense/trade) are `YYYY-MM-DD` strings.
- All monetary amounts are stored in **EUR** (`*_eur` columns); source-currency
  originals are kept where relevant (`source_currency`, `original_value`).
- Migrations are idempotent and non-destructive: `CREATE TABLE IF NOT EXISTS`,
  column presence checked via `PRAGMA table_info` before `ALTER TABLE ADD
  COLUMN`, and full rename-copy-drop rebuilds only where a constraint had to
  change (`_rebuild_holdings_for_multiuser`, `_rebuild_snapshots_for_multiuser`,
  `_rebuild_budget_settings_for_multiuser`).

## portfolio.db tables

### holdings (main.py)
One row per user + ticker + market. `UNIQUE(user_id, ticker, market)` — imports
upsert against this key.

| Column | Notes |
|---|---|
| `id` | UUID PK |
| `user_id` | owner |
| `name`, `ticker` | display name, broker ticker |
| `market` | CHECK: `India` or `Global` |
| `value_eur`, `invested_eur`, `return_percent` | current value, cost basis, gain % |
| `quantity`, `average_cost`, `current_price` | position detail (nullable; only holdings with quantity are editable in the UI) |
| `source_currency` | default `EUR`; e.g. `INR` for India imports |
| `asset_category` | `Stock` / `Mutual fund` / `ETF` / other — from `classify_holding()` |
| `cap_bucket` | `Large cap` / `Mid cap` / `Small cap` / `Unclassified` — curated maps in `main.py` |
| `barbell_role`, `barbell_reason` | `Core` / `Upside` / `Review` + explanation — from `classify_barbell()` |
| `updated_at` | ISO UTC |

### imports (main.py)
CSV import audit log: `filename`, `source` (which parser), `imported`,
`updated`, `total_rows`, `created_at`. Lets refreshed broker exports update
positions without duplicating, and powers "recent imports" on Overview.

### snapshots (main.py)
Daily net-worth history. `PRIMARY KEY (user_id, snapshot_date, market)`,
`market` CHECK: `All` / `India` / `Global`. Columns: `net_worth_eur`,
`invested_eur`, `holdings_count`, `created_at`. Written by
`record_snapshots()` — at most one row per user/day/market; the performance
chart reads these.

### settings (main.py)
Global (NOT per-user) key/value store; currently FX rates as `fx_EUR`,
`fx_INR`, `fx_USD`, `fx_GBP`. Seeded from `DEFAULT_FX_RATES`, updated by price
refresh. ⚠️ If per-user settings are ever added here, the table needs a
`user_id` rebuild like `budget_settings` got.

### refresh_runs (main.py)
Price-refresh audit: `refreshed`/`skipped`/`failed` counts, `fx_rate_inr`,
JSON `details` (per-holding outcome), `created_at`.

### budget_settings (streamlit_app.py)
Per-user numeric settings, `PRIMARY KEY (user_id, key)`. Keys in use:
`monthly_salary` and the debt-tracker inputs (see `debt_settings()` /
`setting_value()` in `streamlit_app.py`).

### budget_expenses (streamlit_app.py / budget_db.py)
| Column | Notes |
|---|---|
| `id` | UUID PK |
| `user_id` | owner |
| `name`, `category` | description + category (auto-categorized on PDF import, user-editable) |
| `amount_eur` | amount |
| `expense_date` | `YYYY-MM-DD`; backfilled from `created_at` for pre-date-tracking rows |
| `is_recurring` | 1 = template for monthly materialization by `ensure_recurring_expenses()` |
| `created_at`, `updated_at` | ISO UTC |

**Duplicate guard:** `budget_db.expense_exists()` matches on user + name +
date + `ROUND(amount, 2)`. Category is deliberately excluded — auto-
categorization isn't stable across runs, and a category-sensitive check is how
duplicates previously slipped through.

### manual_assets (streamlit_app.py)
Manually tracked non-brokerage assets. Columns: `name`, `category` (one of
`MANUAL_ASSET_CATEGORIES`: Real estate, Cash & savings, Private investment,
Crypto, Vehicles & collectibles, Other), `value_eur`, `cost_eur` (nullable),
`currency`, `original_value`, `notes`, timestamps. Totals roll into net worth.

### broker_imports / broker_transactions (streamlit_app.py)
Broker CSV audit (`broker`, `filename`, `imported` count) and the raw
transaction rows (`import_id` FK-by-convention, `trade_date`,
`transaction_type` BUY/SELL/other normalized by
`normalize_transaction_type()`, `name`, `ticker`, `market`, `quantity`,
`price`, `amount`, `fee`, `currency`).

## auth.db tables (auth/local_auth.py)

### users
`id` (UUID PK), `email` (UNIQUE, lowercased), `password_hash` (PBKDF2-HMAC-
SHA256), `password_salt` (hex, 16 random bytes per user), `display_name`,
`created_at`.

### sessions
`token` (UUID4-hex PK), `user_id`, `created_at`, `last_active_at`. Sessions
past `INACTIVITY_TIMEOUT` (7 days) are deleted on lookup. `resolve_session()`
is the single validation path used by both Streamlit and FastAPI.

## Legacy data claim

Rows created before auth existed belong to `user_id = 'local-user'`. On first
login, `streamlit_app.py::has_legacy_data()` detects them and
`claim_legacy_data(new_user_id)` reassigns them across all per-user tables.
The JSON bootstrap in `init_database()` (from `data/portfolio.json`) only fires
when `holdings` is empty **across all users**, so a claim can't retrigger it.

## Postgres migration mapping (roadmap Stage 2)

The target schema is checked in at `migrations/postgres/001_initial_schema.sql`
(idempotent; apply via the Supabase SQL editor or MCP). Key mappings:

- `TEXT PRIMARY KEY` UUIDs → `uuid PRIMARY KEY DEFAULT gen_random_uuid()`
  (existing UUID4 ids migrate as-is, preserving `import_id` references)
- `user_id` → `uuid REFERENCES auth.users(id) ON DELETE CASCADE` (Supabase
  Auth), with an owner-only RLS policy `user_id = auth.uid()` on every
  per-user table; `settings` is read-only to users, written via service role
- Money `REAL` → `numeric(14,2)` (prices/costs `numeric(14,4)`); rates and
  percents stay `double precision`; `is_recurring` int → `boolean`;
  `refresh_runs.details` JSON text → `jsonb`
- ISO timestamp strings → `timestamptz`; `YYYY-MM-DD` strings → `date`
- `?` placeholders → `%s`; `INSERT ... ON CONFLICT` carries over unchanged
- `auth/local_auth.py` retires in favor of Supabase Auth

One-time data copy: `scripts/migrate_sqlite_to_postgres.py` (dry-run by
default flag choice; only rows whose `user_id` is explicitly mapped via
`--user-map old=new` are migrated; idempotent `ON CONFLICT DO NOTHING`).
Connection via `DATABASE_URL` in `.env` — see `.env.example`.
