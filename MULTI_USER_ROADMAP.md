# Atlas Portfolio — Multi-User Roadmap

Plan to take Atlas Portfolio from a single-user local app to a product other
people can sign up for and use to track their own portfolios.

## Where we are

- [x] Every per-user table (`holdings`, `imports`, `snapshots`, `refresh_runs`,
      `budget_settings`, `budget_expenses`, `broker_imports`,
      `broker_transactions`, `manual_assets`) has a `user_id` column and
      correct composite uniqueness constraints.
- [x] Every query in `main.py` and `streamlit_app.py` is scoped by `user_id`.
- [x] Migration is non-destructive and was run against the live DB; all
      existing data preserved under `local-user`.
- [x] **Stage 1 is done** (this section used to say auth wasn't wired in —
      it now is). `streamlit_app.py` calls `require_login()` at the top and
      reassigns `LOCAL_USER_ID` to the real session user for the rest of the
      script run; there's a working login/signup/logout flow, a
      no-email password-reset flow (`auth/local_auth.py::reset_password`),
      and it's been hand-verified that two different logged-in accounts see
      completely separate data.
- [x] `auth/local_auth.py` has unit tests (`tests/test_auth.py`, 11 tests)
      covering register/login/reset/session-invalidation.
- [x] `budget_db.py` (the expense dedup + insert logic) has unit tests
      (`tests/test_budget.py`, 8 tests), including a regression test for the
      duplicate-import bug that caused two rounds of manual data cleanup.
- [x] Real git history exists (`data/` is gitignored — DBs, backups, and
      uploaded bank statements never get committed).

**Not done yet, and more urgent than Stage 2 below:**
- [x] ~~**FastAPI has no authentication.**~~ Fixed: every data route in
  `main.py` now takes `user_id` from `Depends(require_user_id)`, which
  resolves the `Authorization: Bearer <session-token>` header against
  `auth.db` via `auth.local_auth.resolve_session()` and 401s otherwise.
  Client-supplied `user_id` params are ignored. Covered by
  `tests/test_api.py` (missing/garbage/expired tokens, cross-user
  read/delete isolation, query-param spoofing).
- The bulk of `streamlit_app.py` (2,700+ lines: PDF statement parsing,
  auto-categorization, recurring-expense generation, FX/debt/portfolio math,
  all page rendering) has **zero test coverage**. The PDF parser and
  recurring-expense logic are the highest-risk pieces to carry into a
  Postgres migration untested, since a silent parsing bug there is exactly
  what caused this week's duplicate-transaction incidents.
- A real database for concurrent users, and hosting anyone but you can
  reach.

---

## Stage 1 — Wire local auth into the running app ✅ DONE

Goal: prove multi-tenancy works end to end, still on your laptop, still on
SQLite. No new infra.

1. [x] **Streamlit**: `require_login()` is called at the top of
   `streamlit_app.py`, before any DB reads. Blocks with a login/signup form
   until `current_user()` returns a real user.
2. [x] `LOCAL_USER_ID` is reassigned to `_session_user["id"]` once, right
   after login — every function that reads it as a module global picks up
   the real user for the rest of the script run. (Functionally the same
   outcome as "replace every reference," done via one reassignment instead
   of touching every call site.)
3. [~] **FastAPI**: the price-refresh call passes `?user_id=...` as a query
   param rather than the `X-User-Id` header originally planned — works, but
   see the "known gap" below, this is the part that still needs hardening.
4. [x] Logout button in the sidebar (`auth.local_auth.logout()` + `st.rerun()`).
5. [x] **Tested**: two accounts (a real account + a disposable "test"
   account) were logged into during this week's work and confirmed to see
   completely separate holdings/budget data.
6. **Known gap, now more urgent than originally scoped**: it's not just
   that price-refresh could double-fetch a shared ticker (the original
   note) — it's that **`main.py` never verifies the `user_id` it's given**.
   Any of its endpoints will return or mutate any user's data for whoever
   asks. Treat this as a blocking prerequisite for Stage 3, not a Stage 4
   nice-to-have — see the new step below.

Effort: small. Risk: low — it's additive, nothing about the schema changes.

---

## Stage 1.5 — Close the gaps before touching Postgres

Goal: don't carry known security and coverage gaps into a bigger migration.
Do this now, while everything is still SQLite and low-stakes to fix.

1. [x] **Authenticate FastAPI requests.** — done as described below
   (`require_user_id` in `main.py`, Streamlit sends the Bearer token on
   price refresh). `auth/local_auth.py` already issues a
   session token (`st.session_state["session_token"]`, also mirrored into
   `st.query_params["session"]`). Have Streamlit send that token to FastAPI
   (header, e.g. `Authorization: Bearer <token>`) instead of a raw
   `user_id`. Add a small dependency in `main.py` that looks the token up
   against `data/auth.db` (same `_resolve_session` logic `local_auth.py`
   already has — may be worth moving session lookup into a shared module
   both `main.py` and `streamlit_app.py` import) and derives `user_id` from
   *that*, ignoring any `user_id` the client claims. Reject with 401 if the
   token doesn't resolve.
2. **Backfill tests for the highest-risk untested logic** before it gets
   copied into a Postgres migration script:
   - [x] PDF statement parsing — extracted into `statement_parser.py`
     (Streamlit-free, importable) and covered by
     `tests/test_statement_parser.py`: date/amount format variants,
     auto-categorization, N26 block extraction (income skipped, FX lines
     stripped, N26 category labels honored), page-boilerplate stripping,
     and the single-line fallback format.
   - `ensure_recurring_expenses` — month-rollover and idempotency are easy
     to get subtly wrong and hard to notice until rent/loan entries are
     missing or doubled for some month.
   - [x] `main.py`'s FastAPI endpoints, via `TestClient` —
     `tests/test_api.py` now covers the auth dependency: 401 on
     missing/garbage/non-Bearer/expired tokens, identity derived from the
     token (not the client), and cross-user read/delete isolation.
3. [x] **Test**: same two-account isolation check as Stage 1, but this time
   also confirm that calling a FastAPI endpoint with someone *else's*
   `user_id` and your own session token gets rejected, not honored.
   (`tests/test_api.py::test_user_id_query_param_is_ignored`.)

Effort: small-medium. Risk of skipping this: the auth gap is a real data
leak once hosted; the untested parsing logic is exactly what already broke
twice this week.

---

## Stage 2 — Move to Postgres (Supabase)

Goal: a database that handles real concurrent multi-user writes, plus a
managed auth provider instead of the homegrown password table.

1. Create a Supabase project (free tier to start).
2. Recreate the schema in Postgres:
   - SQLite `TEXT PRIMARY KEY` → Postgres `uuid PRIMARY KEY DEFAULT gen_random_uuid()`
   - `user_id` columns become `uuid REFERENCES auth.users(id)` — Supabase
     Auth gives you a `users` table for free, so `auth/local_auth.py` gets
     retired here in favor of Supabase's client SDK.
   - Turn on **Row Level Security** per table with a policy like
     `user_id = auth.uid()` — this makes cross-user data leaks a database-
     level guarantee, not just an application-level discipline (a real
     upgrade over today's "every query remembers to filter" approach).
3. Write a one-time migration script: read every row out of `portfolio.db`
   and `auth.db`, insert into Postgres under your own new Supabase user.
4. Swap `sqlite3.connect()` for `psycopg2`/`supabase-py` in both
   `main.py` and `streamlit_app.py`. The query shapes mostly carry over;
   placeholder syntax changes (`?` → `%s`), and `INSERT ... ON CONFLICT`
   stays the same in Postgres.
5. Config via environment variables: `DATABASE_URL`, `SUPABASE_URL`,
   `SUPABASE_ANON_KEY` — `.env` locally (already gitignored), real secrets
   set on the host in Stage 3.
6. **Test**: same two-account isolation test as Stage 1, now against
   Postgres.

Effort: medium — this is the biggest single chunk of work, mostly
mechanical query-syntax changes plus the RLS policies.

---

## Stage 3 — Host it

Goal: a URL anyone can sign up at, not `localhost`.

1. **Backend (FastAPI)**: Fly.io or Railway — both have simple `Dockerfile`
   deploys and free/cheap tiers for a project this size.
2. **Frontend (Streamlit)**: Streamlit Community Cloud (free, built for
   this) or co-locate it on the same host as the backend.
3. Custom domain + HTTPS (Fly/Railway/Streamlit Cloud all handle TLS
   automatically once a domain is pointed at them).
4. Move price refresh off a manual button and onto a scheduled job (cron
   on the host, or Supabase's pg_cron) — e.g. every 30 minutes during
   market hours, once for all users combined (sets up the dedup work in
   Stage 4 anyway).
5. **Test**: full signup → add holdings → refresh → see live prices, from
   a browser that isn't your dev machine.

Effort: small-medium, mostly configuration not code.

---

## Stage 4 — Harden for real users

Do this before telling people it's available, not after.

- **Price refresh dedup**: fetch each distinct ticker once per refresh
  cycle, fan the result out to every user holding it — turns N×API-calls
  into 1×API-calls, and avoids Yahoo Finance rate-limiting you once you
  have more than a handful of users.
- **Error monitoring**: Sentry (or similar) on both FastAPI and Streamlit
  so failures surface instead of silently breaking someone's dashboard.
- **Backups**: Supabase has point-in-time recovery on paid tiers — turn it
  on once there's real user data you can't lose.
- **Rate limiting / abuse protection** on signup and API endpoints.
- **Terms of service + privacy policy** — required once you're holding
  other people's financial data, even informally.
- ~~Password reset flow~~ — done. `local_auth.py::reset_password` lets a
  user reset by email + new password directly, no email round-trip (there's
  no email service configured). This is fine for a local/small-trust-circle
  product; it's the kind of thing worth swapping for Supabase Auth's real
  reset-by-email flow once Stage 2 lands, since at that point "anyone who
  knows your email can reset your password" stops being an acceptable
  trade-off for a hosted product with strangers on it.

---

## Stage 5 — Frontend rewrite (later, only if it's working)

Streamlit reruns the whole script on every interaction — fine for an
internal tool, sluggish for a polished product. Once Stages 1–4 prove
people actually want this:

- Rebuild the frontend in React/Next.js, calling the same FastAPI backend
  (which by then is already multi-tenant and Postgres-backed, so this
  stage touches only the UI layer, not data or auth).
- Streamlit can stay alive as an internal/admin view even after this.

Effort: large. Don't start here — validate first.

---

## Suggested order of attack

1. ~~Stage 1~~ — done, and already load-bearing: you've been using it
   yourself this week (that's how this week's real data and bugs surfaced).
2. **Stage 1.5 now** — close the FastAPI auth gap and backfill tests on the
   PDF-parsing/recurring-expense logic, while it's still cheap to fix on
   SQLite and before either gets carried into a Postgres migration.
3. Stage 2 + 3 together once you're confident it's worth the Postgres/
   hosting migration.
4. Stage 4 before sharing the URL with anyone outside a trusted circle.
5. Stage 5 only after Stage 4 has real usage to justify it.
