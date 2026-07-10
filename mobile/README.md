# Vermo Mobile

Expo / React Native app (iOS + Android + web) — Phase 3 of
`docs/PRODUCT_ROADMAP.md`. One TypeScript codebase talking to the same
Supabase backend as the web app: `supabase-js` for auth and (RLS-protected)
data; heavier business logic stays in the Python backend.

## Status: walking skeleton

- ✅ Email/password login + sign-up (confirm-password, email-confirmation
  notice) against Supabase Auth (sessions persist via AsyncStorage;
  auto-refresh).
- ✅ Tab navigation: Overview · Budget · Income · Goals · Debt.
- ✅ Overview: total-wealth hero, P/L tile, net-worth chart (SVG, from the
  snapshots history), holdings list with All/India/Global market filter,
  pull-to-refresh.
- ✅ Budget: salary vs spent vs remaining (+ savings rate), 6-month
  spending-trend chart, top-category bars, add-expense form.
- ✅ Income: 12m/this-year/monthly-average tiles + recent events.
- ✅ Goals: progress cards vs total wealth. (On-track/required-monthly
  verdicts stay server-side — they arrive with the hosted API in P2.)
- ✅ Debt: loan details form, payoff projection with extra-payment
  scenarios and balance-over-time chart (`src/lib/debt-math.ts` mirrors
  `finance_math.debt_projection` — keep the two in sync).
- ⬜ Next: price-refresh trigger, biometric unlock, push notifications,
  EAS store builds.

## Run it

Requires Node (installed via fnm on this machine: `fnm env` in a new shell).

```bash
cd mobile
npm install
npx expo start          # then scan the QR with the Expo Go app (iOS/Android)
npx expo start --web    # or run in the browser (port 8081)
```

Claude Code preview config: `vermo-mobile-web` in `.claude/launch.json`.

## Configuration

`.env` holds `EXPO_PUBLIC_SUPABASE_URL` and `EXPO_PUBLIC_SUPABASE_ANON_KEY`.
These are compiled into the client bundle **by design** — the publishable key
is safe to ship; row-level security protects the data. Never put the
service-role key or `DATABASE_URL` here.

## Layout

```
src/app/          expo-router routes (_layout wraps AuthProvider; index
                  shows LoginScreen or Dashboard by session state)
src/components/   login-screen, dashboard
src/lib/          supabase client + auth context
src/constants/    Vermo brand tokens + formatting helpers
```
