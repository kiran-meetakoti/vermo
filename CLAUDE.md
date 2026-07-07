# CLAUDE.md — Vermo (FinApp)

Personal-finance tracker (portfolio, budget, debt, net worth) being developed
into a **sellable multi-tenant product**. Hold changes to that bar: tenant
isolation, tests for data-corrupting logic, docs kept in sync.

## Docs to read first
- `docs/ARCHITECTURE.md` — two-process topology (Streamlit UI + FastAPI),
  auth flow, module map, known limitations.
- `docs/DATA_MODEL.md` — every table in `portfolio.db` and `auth.db`,
  schema conventions, migration rules.
- `docs/DEVELOPMENT.md` — setup, conventions, how-to recipes.
- `MULTI_USER_ROADMAP.md` — staged plan to hosted product; check "Where we
  are" before starting roadmap work.

## Commands
```bash
uv sync                                        # install deps
uv run pytest                                  # test suite (must stay green)
uv run uvicorn main:app --reload --port 8000   # FastAPI (API + legacy dashboard)
uv run streamlit run streamlit_app.py          # primary UI (port 8501)
```
Preview config: `vermo-streamlit-preview` (port 8510) in `.claude/launch.json`.

## Hard rules
- **Never commit anything under `data/`** — real personal financial data
  (DBs, bank statements, backups). It's gitignored; don't bypass.
- **Every query on a per-user table filters by `user_id`.** New tables get a
  `user_id` column and composite uniqueness including it.
- **FastAPI routes never trust a client-supplied user id** — use
  `Depends(require_user_id)` (Bearer session token → `auth.db` lookup).
- **Don't import `streamlit_app.py` in tests** — it executes login/rendering
  at import time. Extract logic into plain modules (pattern: `budget_db.py`)
  and test those.
- Schema changes: idempotent, non-destructive, additive (`CREATE TABLE IF NOT
  EXISTS`, `PRAGMA table_info` checks). Back up `data/portfolio.db` before
  schema experiments. Update `docs/DATA_MODEL.md` with any schema change.
- Money stored in EUR; ISO-UTC timestamps via `utc_now()`; UUID4 string ids.

## Gotchas
- Product name is **Vermo**; "Atlas" appears in older material (roadmap
  title, `--atlas-*` CSS vars) — same app.
- `LOCAL_USER_ID` exists in BOTH `main.py` and `streamlit_app.py`; in
  Streamlit it's reassigned to the logged-in user at startup. It is only a
  default for direct script/test calls, never route authorization.
- Ticker classification maps (Yahoo symbols, cap buckets, barbell roles) are
  duplicated between `main.py` and `streamlit_app.py` — change both.
- HTML for `st.markdown(unsafe_allow_html=True)` must be single-line
  f-strings (multi-line + empty optional spans → Markdown code-block bug).
- Requires Python ≥ 3.9; `run_streamlit.py` patches a 3.9 Protocol issue.
