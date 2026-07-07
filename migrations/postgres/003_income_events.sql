-- Income tracking (Phase 2.5: dividends & income view — "what does my
-- wealth pay me?"). Dividends, interest, rent, and other income events;
-- optionally linked to a holding for per-holding yield. Idempotent.

create table if not exists public.income_events (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references auth.users (id) on delete cascade,
    source_type text not null check (source_type in ('Dividend', 'Interest', 'Rent', 'Other')),
    holding_id  uuid references public.holdings (id) on delete set null,
    name        text not null,
    amount_eur  numeric(14, 2) not null,
    income_date date not null,
    notes       text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index if not exists income_events_user_date_idx
    on public.income_events (user_id, income_date);
create index if not exists income_events_holding_idx
    on public.income_events (holding_id);

alter table public.income_events enable row level security;
drop policy if exists income_events_owner_all on public.income_events;
create policy income_events_owner_all on public.income_events
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
