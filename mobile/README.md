# Vermo Mobile

Expo / React Native app (iOS + Android + web) — Phase 3 of
`docs/PRODUCT_ROADMAP.md`. One TypeScript codebase talking to the same
Supabase backend as the web app: `supabase-js` for auth and (RLS-protected)
data; heavier business logic stays in the Python backend.

## Status: walking skeleton

- ✅ Email/password login against Supabase Auth (sessions persist via
  AsyncStorage; auto-refresh).
- ✅ Overview dashboard: total wealth, P/L, positions, holdings list,
  pull-to-refresh. RLS scopes every query to the signed-in user.
- ⬜ Next: net-worth chart (snapshots), Income/Goals screens, price-refresh
  trigger, biometric unlock, push notifications, EAS store builds.

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
