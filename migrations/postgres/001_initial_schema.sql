-- Vermo — Postgres (Supabase) schema, Stage 2 of MULTI_USER_ROADMAP.md
--
-- Mirrors the SQLite schema documented in docs/DATA_MODEL.md with the
-- Stage-2 upgrades:
--   * TEXT uuid strings            -> uuid columns (existing UUID4 ids migrate as-is)
--   * user_id TEXT                 -> uuid REFERENCES auth.users(id) (Supabase Auth
--                                     replaces auth/local_auth.py's users/sessions)
--   * ISO-string timestamps        -> timestamptz
--   * YYYY-MM-DD text dates        -> date
--   * REAL money                   -> numeric(14,2)  (exact; rates/percents stay
--                                     double precision)
--   * "every query remembers to filter by user_id" -> Row Level Security:
--     user_id = auth.uid() enforced by the database on every table.
--
-- Idempotent: safe to re-run (IF NOT EXISTS / OR REPLACE / drop-then-create
-- policies). Apply via the Supabase SQL editor or any migration runner.

-- ── holdings ────────────────────────────────────────────────────────────────
create table if not exists public.holdings (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid not null references auth.users (id) on delete cascade,
    name            text not null,
    ticker          text not null,
    market          text not null check (market in ('India', 'Global')),
    value_eur       numeric(14, 2) not null,
    return_percent  double precision not null,
    asset_class     text not null,
    quantity        double precision,
    average_cost    numeric(14, 4),
    current_price   numeric(14, 4),
    invested_eur    numeric(14, 2) not null,
    source_currency text not null default 'EUR',
    updated_at      timestamptz not null default now(),
    asset_category  text not null default 'Stock',
    cap_bucket      text not null default 'Unclassified',
    barbell_role    text not null default 'Review',
    barbell_reason  text not null default 'Review whether this holding has a clear role.',
    unique (user_id, ticker, market)
);
create index if not exists holdings_user_idx on public.holdings (user_id);

-- ── imports (CSV import audit log) ──────────────────────────────────────────
create table if not exists public.imports (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references auth.users (id) on delete cascade,
    filename   text not null,
    source     text not null,
    imported   integer not null,
    updated    integer not null,
    total_rows integer not null,
    created_at timestamptz not null default now()
);
create index if not exists imports_user_idx on public.imports (user_id);

-- ── snapshots (daily net-worth history) ─────────────────────────────────────
create table if not exists public.snapshots (
    user_id        uuid not null references auth.users (id) on delete cascade,
    snapshot_date  date not null,
    market         text not null check (market in ('All', 'India', 'Global')),
    net_worth_eur  numeric(14, 2) not null,
    invested_eur   numeric(14, 2) not null,
    holdings_count integer not null,
    created_at     timestamptz not null default now(),
    primary key (user_id, snapshot_date, market)
);

-- ── settings (global, NOT per-user: FX reference rates) ─────────────────────
-- Written only by the backend (service role) during price refresh; readable
-- by any signed-in user. No user_id by design — see docs/DATA_MODEL.md.
create table if not exists public.settings (
    key        text primary key,
    value      text not null,
    updated_at timestamptz not null default now()
);

-- ── refresh_runs (price-refresh audit) ──────────────────────────────────────
create table if not exists public.refresh_runs (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references auth.users (id) on delete cascade,
    refreshed   integer not null,
    skipped     integer not null,
    failed      integer not null,
    fx_rate_inr double precision not null,
    details     jsonb not null default '[]',
    created_at  timestamptz not null default now()
);
create index if not exists refresh_runs_user_idx on public.refresh_runs (user_id);

-- ── budget_settings (per-user numeric settings: salary, debt inputs) ────────
create table if not exists public.budget_settings (
    user_id    uuid not null references auth.users (id) on delete cascade,
    key        text not null,
    value      double precision not null,
    updated_at timestamptz not null default now(),
    primary key (user_id, key)
);

-- ── budget_expenses ──────────────────────────────────────────────────────────
create table if not exists public.budget_expenses (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references auth.users (id) on delete cascade,
    name         text not null,
    category     text not null,
    amount_eur   numeric(14, 2) not null,
    expense_date date,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    is_recurring boolean not null default false
);
create index if not exists budget_expenses_user_date_idx
    on public.budget_expenses (user_id, expense_date);
-- The duplicate-import guard (budget_db.expense_exists) matches on
-- user + name + date + rounded amount; this index serves that lookup.
create index if not exists budget_expenses_dedup_idx
    on public.budget_expenses (user_id, name, expense_date);

-- ── manual_assets ────────────────────────────────────────────────────────────
create table if not exists public.manual_assets (
    id             uuid primary key default gen_random_uuid(),
    user_id        uuid not null references auth.users (id) on delete cascade,
    name           text not null,
    category       text not null,
    value_eur      numeric(14, 2) not null,
    cost_eur       numeric(14, 2),
    currency       text not null default 'EUR',
    original_value numeric(14, 2),
    notes          text,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);
create index if not exists manual_assets_user_idx on public.manual_assets (user_id);

-- ── broker_imports / broker_transactions ────────────────────────────────────
create table if not exists public.broker_imports (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references auth.users (id) on delete cascade,
    broker     text not null,
    filename   text not null,
    imported   integer not null,
    created_at timestamptz not null default now()
);
create index if not exists broker_imports_user_idx on public.broker_imports (user_id);

create table if not exists public.broker_transactions (
    id               uuid primary key default gen_random_uuid(),
    user_id          uuid not null references auth.users (id) on delete cascade,
    import_id        uuid not null references public.broker_imports (id) on delete cascade,
    broker           text not null,
    trade_date       date not null,
    transaction_type text not null,
    name             text not null,
    ticker           text not null,
    market           text not null,
    quantity         double precision,
    price            numeric(14, 4),
    amount           numeric(14, 2),
    fee              numeric(14, 2),
    currency         text not null,
    created_at       timestamptz not null default now()
);
create index if not exists broker_transactions_user_idx
    on public.broker_transactions (user_id, trade_date);

-- ── Row Level Security ───────────────────────────────────────────────────────
-- One owner-only policy per per-user table: a signed-in user can see and
-- touch exactly the rows whose user_id is their auth.uid(). This turns
-- tenant isolation from an application-level discipline into a database
-- guarantee (backend service-role connections bypass RLS by design).

do $$
declare
    t text;
begin
    foreach t in array array[
        'holdings', 'imports', 'snapshots', 'refresh_runs',
        'budget_settings', 'budget_expenses', 'manual_assets',
        'broker_imports', 'broker_transactions'
    ]
    loop
        execute format('alter table public.%I enable row level security', t);
        execute format('drop policy if exists %I on public.%I', t || '_owner_all', t);
        execute format(
            'create policy %I on public.%I for all to authenticated '
            'using (user_id = (select auth.uid())) '
            'with check (user_id = (select auth.uid()))',
            t || '_owner_all', t
        );
    end loop;
end $$;

-- settings: global read for signed-in users, writes only via service role
-- (which bypasses RLS), so no write policy is defined at all.
alter table public.settings enable row level security;
drop policy if exists settings_read_all on public.settings;
create policy settings_read_all on public.settings
    for select to authenticated using (true);
