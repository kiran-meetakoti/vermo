-- Named financial goals (Phase 2.5: "House deposit €60k by 2028 — on
-- track / add €X/mo"). Goals are lenses on total wealth, not allocations:
-- several goals may reference the same money. Idempotent.

create table if not exists public.goals (
    id                      uuid primary key default gen_random_uuid(),
    user_id                 uuid not null references auth.users (id) on delete cascade,
    name                    text not null,
    target_eur              numeric(14, 2) not null,
    target_date             date not null,
    monthly_contribution    numeric(14, 2) not null default 0,
    expected_return_percent double precision not null default 5.0,
    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now()
);
create index if not exists goals_user_idx on public.goals (user_id);

alter table public.goals enable row level security;
drop policy if exists goals_owner_all on public.goals;
create policy goals_owner_all on public.goals
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
