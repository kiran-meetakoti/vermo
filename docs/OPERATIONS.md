# Vermo — Operations Runbook

How to run, deploy, and maintain the live system. What the code *does* is in
[ARCHITECTURE.md](ARCHITECTURE.md); what's *planned* is in
[PRODUCT_ROADMAP.md](PRODUCT_ROADMAP.md).

## The live system at a glance

| Piece | Where | How it updates |
|---|---|---|
| Web app | https://vermoo.streamlit.app (Streamlit Community Cloud, free) | Auto-redeploys on every push to `main` (~2 min) |
| Database + auth | Supabase project `oxsfgdhrdkhpyfmgmrhe`, region eu-west-1 | Schema via `migrations/postgres/*.sql` (applied manually) |
| Mobile app | Expo Go on-device (dev); stores later via EAS | `npx expo start` from `mobile/`, hot-reloads |
| CI | GitHub Actions "CI" | Every push/PR: 138-test suite, SQLite-pinned |
| Price refresh | GitHub Actions "Price refresh" | Weekdays 22:10 UTC (after all three markets close) |
| Backups | GitHub Actions "Nightly backup" | Daily 02:17 UTC, AES-256-encrypted artifact, 30-day retention |
| Error monitoring | Sentry (opt-in) | Activates when `SENTRY_DSN` is set |

## Operating the web app

- **Deploy**: `git push` to `main`. Nothing else. Watch progress (or force a
  redeploy) at share.streamlit.io → the app → Manage app → Reboot.
- **A browser tab that was already open keeps running old code** until the
  page is fully refreshed (Cmd+Shift+R). The login page never changes with
  releases — check the Overview to judge "am I on the new version".
- **Cold starts**: the free tier sleeps the app after inactivity; first
  visitor waits ~30–60 s. Expected, not a bug.
- **Secrets** live in the Streamlit Cloud dashboard (app → Settings →
  Secrets): `VERMO_BACKEND`, `DATABASE_URL`, `SUPABASE_URL`,
  `SUPABASE_ANON_KEY`, optionally `SENTRY_DSN`.

## Operating the mobile app (development)

```bash
cd ~/Documents/FinApp/mobile
npx expo start            # QR code → scan with phone camera → opens Expo Go
npx expo start --tunnel   # if phone and Mac aren't on the same network / VPN in the way
```

- Requires Node (installed via fnm; open a new terminal if `npx` is missing).
- Code edits hot-reload onto the phone within seconds.
- Only ONE Metro server per port — if a Claude session runs a preview on
  8081, the personal one moves to 8082 (or stop one of them).
- Store builds (later): EAS — see PRODUCT_ROADMAP Phase 3.

## Local development (Python)

```bash
uv sync && uv run pytest             # must stay green
uv run streamlit run streamlit_app.py   # local web app
```
- Local `.env` selects the backend: `VERMO_BACKEND=postgres` = the REAL
  production database — treat every write as production. Unset/`sqlite` =
  local `data/portfolio.db`, safe to experiment.
- Tests always run on SQLite regardless (`db.connect(tmp)` + CI pin).

## Scheduled jobs (GitHub Actions)

All in `.github/workflows/`; run any of them manually via the Actions tab →
workflow → "Run workflow".

| Workflow | Schedule (UTC) | Red means | Needs secrets |
|---|---|---|---|
| CI | on push/PR | a commit broke the suite — fix before anything else | — |
| Price refresh | 22:10 Mon–Fri | quote/FX provider failures outnumbered successes | `DATABASE_URL` |
| Nightly backup | 02:17 daily | dump or encryption failed — data is NOT backed up that night | `DATABASE_URL`, `BACKUP_PASSPHRASE` |

Repo secrets live at github.com/kiran-meetakoti/vermo → Settings → Secrets
and variables → Actions. Secrets can only be replaced, never viewed.

## Backups & restore

Nightly encrypted dump (public + auth schemas) as a workflow artifact,
30-day retention. Restore drill (practice it once):
[DEVELOPMENT.md → Backups](DEVELOPMENT.md#backups). Rule: restore into a
scratch database first, verify row counts, never straight into prod.
Losing `BACKUP_PASSPHRASE` makes every backup unreadable — it must exist in
a password manager.

## Secrets inventory (what lives where)

| Secret | Local `.env` | Streamlit Cloud | GitHub Actions | `mobile/.env` (committed) |
|---|---|---|---|---|
| `DATABASE_URL` (service access, bypasses RLS) | ✓ | ✓ | ✓ | NEVER |
| `SUPABASE_ANON_KEY` / publishable key (safe: RLS enforces) | ✓ | ✓ | — | ✓ by design |
| `BACKUP_PASSPHRASE` | — | — | ✓ | NEVER |
| `SENTRY_DSN` (optional) | opt | opt | opt | later |

## Routine tasks

- **Rotate the database password** (⚠️ overdue — the current one passed
  through a chat transcript): Supabase → Settings → Database → Reset
  database password, then update `DATABASE_URL` in all three places above
  (local `.env`, Streamlit Cloud secrets, GitHub Actions secret).
- **Schema change**: new numbered file in `migrations/postgres/`
  (idempotent), apply via Supabase SQL editor or MCP, mirror in the SQLite
  init, update DATA_MODEL.md. Never destructive.
- **Security/performance audit**: Supabase MCP `get_advisors` after any
  DDL change.
- **Test-user recipe** (agent sessions): SQL-insert into `auth.users`
  (bcrypt via `crypt()`, empty-string token columns) + `auth.identities`
  row; ALWAYS delete afterwards — cascades are verified to leave zero
  orphans. Never test against the founding account's data.

## Incident quickies

- Web app slow → check Supabase status + remember US↔EU latency floor;
  pooled connections already in place (db.py).
- Web app down after deploy → Manage app → logs; revert = `git revert` +
  push.
- Refresh workflow red → open the run log; usually Yahoo rate-limiting or a
  changed symbol; the app keeps serving last-known prices meanwhile.
- Locked out of Supabase data → RLS policies are in `migrations/postgres/`;
  service-role `DATABASE_URL` bypasses RLS for repair work.
