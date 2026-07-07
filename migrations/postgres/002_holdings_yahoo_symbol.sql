-- Per-holding Yahoo symbol mapping (Phase 2.5: "add any ticker via search").
-- Curated maps in market_data.py stay as fallback for existing holdings;
-- user-added holdings store their own verified symbol here, so any user can
-- get live quotes for any instrument — not just the hand-curated ones.
-- Idempotent.
alter table public.holdings add column if not exists yahoo_symbol text;
