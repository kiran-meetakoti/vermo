# Atlas Portfolio — Multi-User Roadmap

Plan to take Atlas Portfolio from a single-user local app to a product other
people can sign up for and use to track their own portfolios.

## Where we are

- [x] Every per-user table (`holdings`, `imports`, `snapshots`, `refresh_runs`,
      `budget_settings`, `budget_expenses`, `broker_imports`,
      `broker_transactions`, `manual_assets`) has a `user_id` column and
      correct composite uniqueness constraints.
- [x] Every query in `main.py` and `streamlit_app.py` is scoped by `user_id`.
- [x] `auth/local_auth.py` exists — email/password signup+login against a
      local `data/auth.db`, session-based — but is **not wired into the
      running app**. Everything still defaults to a fixed
      `LOCAL_USER_ID = "local-user"`.
- [x] Migration is non-destructive and was run against the live DB; all
      existing data preserved under `local-user`.

Not done yet: a real login screen, a real database for concurrent users, and
hosting anyone but you can reach.

---

## Stage 1 — Wire local auth into the running app

Goal: prove multi-tenancy works end to end, still on your laptop, still on
SQLite. No new infra.

1. **Streamlit**: call `auth.local_auth.require_login()` at the top of
   `streamlit_app.py`, before any DB reads. It blocks with a login/signup
   form until `current_user()` returns a real user.
2. Replace every `LOCAL_USER_ID` reference in `streamlit_app.py` with
   `current_user()["id"]`.
3. **FastAPI**: the Streamlit "Refresh prices" button calls
   `POST /api/prices/refresh` over HTTP. It needs to tell FastAPI *which*
   user it's acting for — add an `X-User-Id` header to that call, and have
   FastAPI read it (falling back to `LOCAL_USER_ID` only if absent, so the
   API still works standalone during testing).
4. Add a logout button to the sidebar (`auth.local_auth.logout()` +
   `st.rerun()`).
5. **Test**: create two accounts locally, add different holdings to each,
   confirm neither sees the other's data, confirm price refresh doesn't
   mix them up.
6. **Known gap to accept for now**: price refresh calls Yahoo Finance
   per-user. If two users hold NVDA, you fetch the NVDA quote twice. Fine
   at small scale; revisit in Stage 4.

Effort: small. Risk: low — it's additive, nothing about the schema changes.

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
- **Password reset flow** — local_auth.py doesn't have one; Supabase Auth
  does, for free, once Stage 2 lands.

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

1. Stage 1 now — cheap, proves the concept, no new accounts/infra needed.
2. Pause and use it yourself (or with 1-2 friends) for a bit.
3. Stage 2 + 3 together once you're confident it's worth the Postgres/
   hosting migration.
4. Stage 4 before sharing the URL with anyone outside a trusted circle.
5. Stage 5 only after Stage 4 has real usage to justify it.
