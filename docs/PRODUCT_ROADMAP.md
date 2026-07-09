# Vermo — Product Roadmap to World-Class

**Vision:** the wealth tracker for people whose money lives in more than one
country — starting with the wedge Vermo already serves better than anyone:
**Indian professionals in Europe** tracking Indian + European + global assets,
debt, and monthly cash flow in one place, in one currency view.

**End state:** native iOS and Android apps + web app, automatic data updates,
paying subscribers, and infrastructure a stranger can trust with their
financial data.

This is the umbrella plan. `MULTI_USER_ROADMAP.md` is the infrastructure log
that got us to hosted multi-tenant (Stages 1–3, largely complete); its Stage
4/5 items are absorbed into the phases below.

## Where we are (2026-07-07)

- ✅ Multi-tenant Supabase Postgres with row-level security; data migrated.
- ✅ Supabase Auth (JWT + refresh tokens), in-app password change.
- ✅ Hosted web app: https://vermoo.streamlit.app (free tier), connection-pooled.
- ✅ 102 tests on the risk-bearing logic; docs suite; clean git history.
- ⬜ Everything below.

---

## The mobile decision (read this first)

Streamlit cannot become a mobile app — it's a server-rendered Python UI. Any
iOS/Android path means building a real frontend. The choice determines the
next six months:

| Option | What it is | Verdict for Vermo |
|---|---|---|
| **Expo / React Native** | One TypeScript codebase → native iOS + Android + web (Expo Web) | **Recommended.** One codebase, three platforms, biggest ecosystem, first-class Supabase SDK, over-the-air updates, EAS handles store builds without a Mac build farm. |
| Flutter | One Dart codebase → iOS/Android/web | Excellent product, but Dart is a new language to carry, and the Supabase/JS ecosystem fit is weaker. |
| Capacitor + Next.js | Web app wrapped in native shells | Fastest if a polished web app existed already — ours doesn't. Wrapped apps also review worse in stores and feel less native. |
| PWA (stopgap) | "Install" the web app from the browser | Not a real store presence, but free and available **today** — see Phase 1. |

**Decision: Expo (React Native + TypeScript), one codebase for iOS, Android,
and eventually web.** Streamlit remains the web app until the Expo app reaches
feature parity, then becomes the internal/admin tool (as the old roadmap's
Stage 5 always intended).

Architecture consequence: the mobile app cannot read Postgres directly the way
Streamlit does. Two clients are involved:

1. **Supabase client SDK** for auth (sign-in/up, token refresh, password
   reset **with a real email flow** — finally) and simple CRUD, protected by
   the RLS policies we already verified.
2. **FastAPI** for everything with business logic (imports, refresh,
   projections, classification) — it already exists, is token-authenticated,
   and only needs its surface completed and a host.

---

## Phase 1 — Production-grade (1–2 weeks) · "trustable"

Make what exists safe and reliable for strangers. Mostly configuration and
small code; every item is independently shippable.

1. [x] **CI:** done 2026-07-08 — `.github/workflows/ci.yml`, pytest on every
   push/PR, VERMO_BACKEND pinned to sqlite. Still to do: enable branch
   protection on `main` (GitHub Settings → Branches).
2. [x] **Error monitoring:** done 2026-07-08 — `monitoring.py` (Sentry,
   opt-in via SENTRY_DSN, no PII) wired into Streamlit, FastAPI, and the
   scheduled jobs. Needs a Sentry project + DSN in Streamlit Cloud secrets
   to activate.
3. [x] **Scheduled price refresh:** done 2026-07-08 —
   `scripts/refresh_all_users.py` + `price-refresh.yml` (hourly,
   weekdays 04–21 UTC; red when failures outnumber refreshes). Needs the
   DATABASE_URL repo secret. Cross-user ticker dedup deferred until there
   are enough users for rate limits to matter (refresh already dedups
   within a user).
4. **Custom SMTP** (Resend/Brevo free tier → Supabase Auth settings): real
   signup-confirmation and password-reset emails; the built-in service is
   rate-limited to a handful per hour.
5. **Auth hardening:** enable leaked-password protection (dashboard toggle);
   rate limits on auth endpoints; rotate the DB password (overdue).
6. [x] **Backups:** done 2026-07-08 — `backup.yml`: nightly pg_dump 17
   (public + auth), AES-256-encrypted, 30-day artifacts; restore drill in
   DEVELOPMENT.md. Needs DATABASE_URL + BACKUP_PASSPHRASE repo secrets.
   (Supabase PITR when revenue justifies the paid tier.)
7. **Legal minimum:** privacy policy + terms pages (host on the app +
   landing page), GDPR basics — we're EU-based holding EU users' financial
   data: data export and account deletion must work (deletion largely does:
   `ON DELETE CASCADE` from `auth.users`; add self-serve export/delete
   buttons under sidebar → Account).
8. **PWA stopgap for mobile:** app icon + `display: standalone` manifest so
   "Add to Home Screen" on iOS/Android feels app-like while the real app is
   built. One evening of work, available immediately.

**Exit criteria:** a stranger can sign up, confirm by email, use the app, and
nothing about it depends on Kiran's laptop being on. Failures alert. Data is
backed up and restorable.

## Phase 2 — API completion + EU hosting (2–4 weeks) · "mobile-ready backend"

The Expo app needs a complete API; latency needs to die.

1. **Host FastAPI in the EU** (Fly.io Frankfurt or Hetzner ~€5/mo, Docker,
   TLS, custom domain e.g. `api.vermo.app`). Same continent as the database
   kills the US↔EU round-trip tax the web app pays today.
2. **Verify JWTs locally via JWKS** instead of calling GoTrue per request
   (one cached keys fetch; removes an HTTP hop from every API call).
3. **Complete the API surface** — everything Streamlit does by direct DB
   access becomes endpoints: budget (expenses CRUD, PDF statement import,
   recurring), debt settings/projections, manual assets, snapshots/history,
   classification maps. OpenAPI schema is the contract the mobile app builds
   against; `TestClient` tests per endpoint (the pattern `tests/test_api.py`
   already sets).
4. **Typed API client generation** from the OpenAPI schema for TypeScript —
   the mobile app never hand-writes a request shape.
5. Migrate the Streamlit web app onto the same API where cheap (optional —
   Streamlit is on borrowed time; don't gold-plate it).

**Exit criteria:** every user action possible in Streamlit is possible via
documented, tested, EU-hosted API endpoints with JWT auth.

## Phase 2.5 — Product core (interleaves with P2) · "how am I doing?"

Vermo records what you own; attractive products answer *how am I doing* and
*what next*. Backend-first features that land in the web app now and the
mobile app automatically:

1. [x] **Add any ticker via search** — done 2026-07-07: Yahoo symbol search
   in Add holding ("Search any instrument" tab), per-holding `yahoo_symbol`
   mapping (002 migration) with curated maps as fallback.
2. [x] **Instant history backfill** — done 2026-07-07:
   `portfolio_core.backfill_history` builds daily snapshots from Yahoo
   closes + ECB FX time series (never overwrites real snapshots); Overview
   offers it when history is sparse. Run for the founding account: 55
   instruments → a full year of chart history.
3. [x] **XIRR** — done 2026-07-07: bisection solver in `finance_math.xirr`
   (Excel-parity tested), cash flows derived from cost-basis deltas in the
   snapshot history (`portfolio_core.snapshot_cash_flows`), shown on the
   Overview P/L tile when ≥90 days of history exist. Refinement for later:
   feed real `broker_transactions` dates once broker imports are
   first-class (P4), replacing the cost-basis approximation.
4. [x] **Dividends & income view** — done 2026-07-07: new Income page
   (trailing-12m / this-year / monthly-average tiles, stacked monthly
   chart, per-holding trailing yield); `income_events` table (003
   migration, RLS) + tested `income_db.py` module.
5. [x] **Named goals** — done 2026-07-07: Goals page with progress bars and
   On track / Behind (+ "needs €X/mo") / Reached status; `goals` table
   (004 migration, RLS), tested `goals_db.py` +
   `finance_math.required_monthly_contribution` (annuity solver,
   projection-roundtrip tested). **Tier 1 of Phase 2.5 is complete.**

**Wedge differentiators (build with/into the mobile launch, P3):**
- **FX-split performance** — "18% market, −4% rupee": separate market gain
  from currency effect per market. No mainstream tracker does this.
- **Remittance radar** — EUR↔INR target-rate alerts + transfer log with
  achieved rates; creates a daily-open habit and pairs with push.
- **Proof-of-funds PDF** — polished net-worth statement export (visas,
  mortgages).
- **Tax awareness (light)** — DE: Sparer-Pauschbetrag usage, Vorabpauschale
  estimate; IN: LTCG/STCG bucketing. Awareness, not filing.

Retention compounders for later (post-P3): rebalancing with drift alerts,
benchmark-vs-VWCE comparison, insight notifications (subscription creep,
budget pace), household read-only sharing.

## Phase 3 — The mobile app (6–10 weeks) · "in your pocket"

Expo/React Native, TypeScript, one repo (`vermo-mobile` or monorepo folder).

1. [~] **Weeks 1–2, walking skeleton:** STARTED 2026-07-07 — `mobile/` (Expo
   SDK 57 + expo-router + TypeScript): Supabase email/password login with
   persisted sessions, Overview dashboard (total wealth, P/L, holdings
   list, pull-to-refresh) reading through RLS via supabase-js. Verified
   E2E on Expo web with a throwaway account. Still to do from this item:
   sign-up flow, biometric unlock, tab navigation, run on Expo Go devices.
2. **Weeks 3–5, core screens:** Holdings (list, detail, add/edit), net-worth
   chart (victory-native/skia), Other assets, Budget (expense list, add,
   monthly view), Debt tracker. Pull-to-refresh triggers price refresh.
3. **Weeks 6–7, mobile-native value:** push notifications (price alerts,
   "monthly budget summary ready", recurring-expense posted) via Expo
   Notifications; offline read cache (last-fetched data visible without
   network); statement upload from phone camera/files.
4. **Weeks 8–10, store launch:** app icons/splash/screenshots, privacy
   "nutrition labels", Apple Developer ($99/yr) + Google Play ($25 once)
   accounts, EAS builds, TestFlight + Play internal track with 5–10 real
   testers from the wedge audience, fix, then public listing.

Store-review realities to plan for: finance-category apps get extra scrutiny —
the privacy policy URL, account-deletion-in-app requirement (Apple), and
"not financial advice" disclaimers (barbell view!) must be in place — all
covered by Phase 1 groundwork.

**Exit criteria:** Vermo installable from the App Store and Play Store,
sign-up → portfolio → budget all native, push notifications working.

## Phase 4 — Data automation (parallel/after Phase 3) · "it updates itself"

The single biggest product-quality gap vs. world-class competitors is manual
data entry. In priority order:

1. **AMFI NAV feed** for Indian mutual funds (free, official) — scheme-code
   mapping table replaces "FX-only" refresh for MF holdings.
2. **Broker-statement importers** as first-class, tested parsers (the
   `statement_parser.py` pattern): Zerodha/Groww/Kuvera exports for India;
   Trade Republic/Scalable/ING for Germany. Each new format = fixture file +
   tests.
3. **Bank connectivity (EU):** GoCardless Bank Account Data (free tier) or
   Tink for PSD2 account/transaction sync — replaces PDF parsing for
   supported banks. PDF import stays as the universal fallback.
4. **Corporate actions sanity:** splits/mergers detection heuristics, or at
   minimum a "this position moved >20% on a refresh — confirm?" flag. Silent
   wrong numbers are the category's cardinal sin.

## Phase 5 — Business (when Phase 3 ships) · "sellable, literally"

1. **Pricing:** freemium — free: 1 portfolio, manual import; paid (~€4–6/mo):
   automation (bank sync, AMFI, alerts), unlimited accounts, reports.
2. **Payments:** RevenueCat wrapping StoreKit/Play Billing (mobile) + Stripe
   (web) so entitlements live in one place.
3. **Landing page** (vermo.app): positioning for the wedge — "your wealth
   across two countries, one app". Waitlist → TestFlight funnel.
4. **Privacy-friendly analytics** (PostHog EU/Plausible) — funnel and
   retention, no ad-tech.
5. **Support loop:** in-app feedback, a real support email, public changelog.

## Quality bar (applies to every phase)

- Tests for data-corrupting logic before merge; suite stays green (CI enforces).
- Docs updated in the same PR that changes behavior (ARCHITECTURE/DATA_MODEL/
  DEVELOPMENT + this roadmap's status).
- Every per-user query filters by user id; RLS stays on; secrets never in git.
- Money is `numeric`, EUR-based, cent-exact; timestamps UTC.
- No feature ships without being exercised end-to-end (the app, not just tests).

## Sequencing summary

```
Now ──► P1 trustable (1–2 wk) ──► P2 API+EU host (2–4 wk) ──► P3 mobile (6–10 wk) ──► P5 business
                                        └─────────► P4 data automation (parallel) ───────┘
```

Realistic wall-clock to App Store, working solo with AI leverage: **~3–4
months**, dominated by Phase 3. Phases 1–2 are the cheap insurance that makes
everything after them safe to build fast.
