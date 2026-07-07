# Vermo — Development Guide

How to set up, run, test, and safely extend the app. Read
[ARCHITECTURE.md](ARCHITECTURE.md) first for the big picture and
[DATA_MODEL.md](DATA_MODEL.md) before touching any table.

## Setup

Requires Python ≥ 3.9 and [uv](https://docs.astral.sh/uv/).

```bash
cd ~/Documents/FinApp
uv sync          # installs runtime + dev dependency groups
```

## Running

Two processes; run both for full functionality (price refresh calls FastAPI):

```bash
# API + legacy dashboard → http://localhost:8000 (API docs at /docs)
uv run uvicorn main:app --reload --port 8000

# Primary Streamlit UI → http://localhost:8501
uv run streamlit run streamlit_app.py
# (or: uv run python run_streamlit.py — applies a Python 3.9
#  Protocol-dataclass workaround if you're on 3.9)
```

VS Code: **Run and Debug → Run Vermo FastAPI**. Claude Code preview: the
`vermo-streamlit-preview` config in `.claude/launch.json` runs Streamlit
headless on port 8510.

On first run the app creates/migrates `data/portfolio.db` and `data/auth.db`
automatically, then shows the signup/login form.

## Testing

```bash
uv run pytest            # whole suite
uv run pytest tests/test_budget.py -q
```

- `tests/test_auth.py` — register/login/reset/session-expiry against a temp
  auth DB.
- `tests/test_budget.py` — expense insert + duplicate-import guard, including
  the regression test for the duplicate-transaction incident.
- `tests/test_api.py` — FastAPI auth: 401 without/with bad token, correct user
  derived from token, cross-user isolation.

Write tests for any logic that can silently corrupt data (parsers, dedup,
recurring generation, migrations). The two production incidents so far were
both silent-parsing/dedup bugs.

### The testability rule (important)

**`streamlit_app.py` cannot be imported by tests** — it runs login, network
calls, and page rendering at import time. Logic that needs tests must live in
its own module. `budget_db.py` is the template:

1. Create a plain module (no `streamlit` import) with the logic; give DB
   functions a `db_file` parameter defaulting to the real path.
2. Have `streamlit_app.py` delegate to it (thin wrappers are fine).
3. Test the module against a temp DB (see `tests/test_budget.py`).

Done so far: `budget_db.py` (expense insert + dedup guard) and
`statement_parser.py` (PDF statement parsing — see
`tests/test_statement_parser.py`). Highest-value extraction candidates still
inside `streamlit_app.py` (roadmap Stage 1.5):
`ensure_recurring_expenses` and the projection/debt math.

## Conventions

- **Every query on a per-user table filters by `user_id`. No exceptions.**
  This is application-level tenant isolation until Postgres RLS (Stage 2)
  makes it a database guarantee.
- **FastAPI routes never accept a client-supplied user id.** New data routes
  must take `user_id: str = Depends(require_user_id)`; the id comes from the
  Bearer session token. Internal helpers may default to `LOCAL_USER_ID` for
  script/test use only.
- Money is stored in EUR; convert for display via `money()` /
  `fx_rates()`. Timestamps: `utc_now()` ISO strings. Dates: `YYYY-MM-DD`.
- Schema changes must be idempotent and non-destructive (see DATA_MODEL.md
  "Conventions"). Back up first: `cp data/portfolio.db
  data/portfolio.db.bak-$(date +%Y%m%dT%H%M%S)`.
- Dependencies: runtime deps are pinned in `pyproject.toml`; run `uv sync`
  after changing them and commit `uv.lock`.
- **Never commit anything under `data/`** — it contains real personal
  financial data (already gitignored; don't work around it).
- Streamlit HTML blocks passed to `st.markdown(unsafe_allow_html=True)` must
  be built as single-line f-strings — multi-line indented f-strings with empty
  optional spans leave blank-line + indentation sequences that Markdown
  renders as code blocks (this bug shipped once; see the comment in the
  "Other assets" page).

## How-to recipes

### Add a FastAPI endpoint
1. Define the route in `main.py` with `user_id: str = Depends(require_user_id)`.
2. Scope every query with `user_id = ?`.
3. Add a `TestClient` test in `tests/test_api.py` covering the happy path and
   a wrong-token/cross-user case.

### Add a Streamlit page
1. Add the page name to the `st.sidebar.radio("View", [...])` list
   (`streamlit_app.py`, around line 1192).
2. Add an `elif page == "Your page":` block with the other page blocks (they
   start around line 1571).
3. Keep new business logic in an importable module (testability rule); the
   page block should only render.

### Add a table
1. Pick an owner: `main.py::init_database()` for portfolio-domain tables,
   a `@st.cache_resource` init function in `streamlit_app.py` for UI-domain
   tables (or better: a new plain module).
2. Include `user_id TEXT NOT NULL DEFAULT 'local-user'` and put `user_id` in
   any uniqueness constraint.
3. Use `CREATE TABLE IF NOT EXISTS`; for later column additions, check
   `PRAGMA table_info` first (see `_add_user_id_column` for the pattern).
4. Add the table to `claim_legacy_data()` if legacy pre-auth rows could exist,
   and document it in DATA_MODEL.md.

### Add a price-refresh mapping
Curated ticker → Yahoo symbol maps live at the top of `main.py`
(`INDIA_YAHOO_SYMBOLS` etc.). Mutual funds need exact AMFI scheme codes before
their NAVs count as refreshed. Cap-bucket and barbell maps are nearby and
duplicated in `streamlit_app.py` — update both.

## Documentation upkeep

Docs are part of the product (the goal is a sellable, maintainable codebase):

- Architecture change (new module/process/flow) → update `ARCHITECTURE.md`.
- Any schema change → update `DATA_MODEL.md`.
- New setup step, convention, or recipe → update this file.
- Product-plan changes → `MULTI_USER_ROADMAP.md`.
- Keep `README.md` a short front door that links here.
