-- Clean Crop — Mung Bean Field Logger
-- Run this once in the Supabase SQL Editor (Dashboard -> SQL Editor -> New query).
--
-- Design note: `fields` holds STABLE field identity that persists across
-- seasons, and `field_seasons` holds one row per field per year. That split is
-- the whole point of this tool — it's what turns scattered annual spreadsheets
-- into a panel where the same field can be tracked year over year.

-- ── Stable field identity (one row per physical field, forever) ─────────────
create table if not exists fields (
    field_id      text primary key,          -- e.g. 'LOYAL-01' — never changes
    grower_name   text not null,
    farm_name     text,
    location_name text,                      -- nearest town
    county        text,
    lat           double precision not null,
    lon           double precision not null,
    irrigated     boolean default false,     -- program is dryland, captured anyway
    notes         text,
    created_at    timestamptz default now()
);

-- ── One row per field per season ───────────────────────────────────────────
create table if not exists field_seasons (
    id                     bigserial primary key,
    field_id               text not null references fields(field_id) on delete cascade,
    season_year            int  not null,

    -- planting
    planting_date          date,
    acres                  double precision,
    seed_lbs               double precision,   -- seed_lbs/acres = seeding rate
    variety                text,
    previous_crop          text,
    soil_condition_planting text,              -- 'dry' | 'adequate' | 'wet'
    planting_method        text,               -- 'drilled' | 'broadcast' | 'other'
    row_spacing_in         double precision,
    grower_years_experience int,

    -- harvest (filled in later in the season)
    harvest_date           date,
    net_lbs                double precision,
    cleanout_pct           double precision,

    notes                  text,
    created_at             timestamptz default now(),
    updated_at             timestamptz default now(),
    unique (field_id, season_year)             -- one record per field per year
);

-- ── Mid-season visit observations (many per field per season) ──────────────
create table if not exists visits (
    id              bigserial primary key,
    field_id        text not null references fields(field_id) on delete cascade,
    season_year     int  not null,
    visit_date      date not null,
    growth_stage    text,        -- emergence | vegetative | flowering | pod fill | maturity
    condition_score int,         -- 1 (poor) .. 5 (excellent)
    notes           text,
    created_at      timestamptz default now()
);

create index if not exists idx_seasons_field on field_seasons(field_id, season_year);
create index if not exists idx_visits_field  on visits(field_id, season_year);

-- ── Access ─────────────────────────────────────────────────────────────────
-- The app gates on a shared passcode and connects with the public anon key,
-- so the anon role needs read/write here. Anyone who obtains the anon key
-- could reach this table directly, bypassing the passcode — acceptable for
-- agronomic field logs, but do not put anything sensitive in these tables.
alter table fields        enable row level security;
alter table field_seasons enable row level security;
alter table visits        enable row level security;

create policy "anon full access on fields"
    on fields for all to anon using (true) with check (true);
create policy "anon full access on field_seasons"
    on field_seasons for all to anon using (true) with check (true);
create policy "anon full access on visits"
    on visits for all to anon using (true) with check (true);
