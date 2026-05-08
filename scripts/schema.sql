-- Gatto Farioli — Sprint 1 memory layer schema.
-- Paste this into the Supabase SQL editor (project -> SQL -> New query).
-- Safe to re-run: every statement is idempotent.

create table if not exists regime (
  id serial primary key,
  as_of timestamptz not null default now(),
  state jsonb not null,           -- {"day":70,"war_started":"2026-02-28","khamenei":"deceased","hormuz":"closed",...}
  notes text
);

create table if not exists thesis_facts (
  id serial primary key,
  topic text not null,             -- "fertilizer", "tankers", "hormuz", "buffett_cash"
  fact text not null,              -- "NDSU projects urea +13% through 2028"
  source text,                     -- URL or citation
  confidence int default 7,        -- 1-10
  created_at timestamptz default now(),
  superseded_at timestamptz        -- null = still active
);

create table if not exists thesis_log (
  id serial primary key,
  symbol text not null,            -- "IPI", "CF", "KXHORMUZNORM-26MAR17-B260601"
  action text not null,            -- "open", "add", "trim", "exit", "review"
  rationale text not null,
  conviction int,                  -- 1-10
  created_at timestamptz default now()
);

create table if not exists daily_brief (
  id serial primary key,
  brief_date date not null,
  brief_text text not null,        -- the full Druckenmiller brief
  state_snapshot jsonb,            -- full /api/state response at time of writing
  created_at timestamptz default now()
);

create table if not exists chat_history (
  id serial primary key,
  role text not null,              -- "user" or "assistant"
  content text not null,
  created_at timestamptz default now()
);

-- Helpful indexes for the read patterns lib/memory.js uses.
create index if not exists regime_as_of_idx on regime (as_of desc);
create index if not exists thesis_facts_active_idx on thesis_facts (created_at desc) where superseded_at is null;
create index if not exists thesis_facts_topic_idx on thesis_facts (topic);
create index if not exists daily_brief_date_idx on daily_brief (brief_date desc, created_at desc);
create index if not exists chat_history_created_idx on chat_history (created_at desc);
create index if not exists thesis_log_symbol_idx on thesis_log (symbol, created_at desc);
