# Vermo — Privacy Policy

*Draft v0.1 (2026-07-13). Plain-language and honest; have a professional
review before public/store launch.*

**Who we are.** Vermo is a personal-finance tracker operated from Germany.
Contact: meetakoti.kirankumar@gmail.com.

**What we store.** Exactly what you enter or import: your holdings, account
snapshots, budget expenses (including imported bank-statement transactions),
income events, goals, debt details, and your email address with a securely
hashed password (via Supabase Auth). Money values are stored in EUR.

**Where it lives.** In a Supabase (PostgreSQL) database in the EU
(eu-west-1, Ireland). Every table is protected by row-level security: your
rows are readable and writable only by your authenticated account.

**What we DON'T do.** We don't sell your data, share it with advertisers,
or use it to train AI models. There is no ad-tech or cross-site tracking in
the app.

**Third parties we rely on.**
- *Supabase* — database and authentication (EU region).
- *Streamlit Community Cloud* — serves the web app (US-hosted servers; your
  data stays in the EU database and transits encrypted).
- *Yahoo Finance & Frankfurter (ECB)* — market prices and FX rates. Only
  instrument tickers are sent — never your identity or amounts.
- *Sentry* (if enabled) — error reports without personal data.

**Backups.** Encrypted nightly backups are retained for 30 days, then
expire automatically.

**Your rights (GDPR).** In the app under Account you can **download all
your data** (JSON) and **delete your account permanently** — deletion
erases every row you own and your login identity immediately; it cannot be
undone. Backups containing your data expire within 30 days. For anything
else (correction, complaints), email us; you may also complain to your
local data-protection authority.

**Not financial advice.** Vermo displays and organizes your own numbers.
Nothing in the app — including health scores, barbell roles, projections,
or suggestions — is investment, tax, or legal advice.
