-- Clean Crop — Field Logger: photos
-- Run this once in the Supabase SQL Editor, after schema.sql.
-- Safe to run twice; every statement is idempotent.
--
-- Two halves: a bucket to hold the image files, and a table describing them.
-- The files go in Storage rather than in a column, because a phone photo is
-- megabytes and Postgres rows are the wrong place for that.

-- ── The bucket ─────────────────────────────────────────────────────────────
-- Created here rather than through the API on purpose: the anon key cannot
-- create buckets, and it should not be able to.
insert into storage.buckets (id, name, public)
values ('field-photos', 'field-photos', false)
on conflict (id) do nothing;

-- The app talks to Storage with the same anon key as everything else, so anon
-- needs read/write on this one bucket. The bucket is NOT public: files are
-- served through short-lived signed URLs, so a guessed path gets nothing.
drop policy if exists "anon upload field photos" on storage.objects;
create policy "anon upload field photos"
    on storage.objects for insert to anon
    with check (bucket_id = 'field-photos');

drop policy if exists "anon read field photos" on storage.objects;
create policy "anon read field photos"
    on storage.objects for select to anon
    using (bucket_id = 'field-photos');

drop policy if exists "anon delete field photos" on storage.objects;
create policy "anon delete field photos"
    on storage.objects for delete to anon
    using (bucket_id = 'field-photos');

-- ── What each file is a photo of ───────────────────────────────────────────
create table if not exists photos (
    id           bigserial primary key,
    field_id     text not null references fields(field_id)
                      on delete cascade on update cascade,
    season_year  int,                    -- null for a photo of the field itself
    stage        text not null,          -- field | planting | visit | harvest
    visit_id     bigint references visits(id) on delete cascade,
    storage_path text not null unique,   -- key within the field-photos bucket
    caption      text,
    taken_by     text,
    created_at   timestamptz default now()
);

create index if not exists idx_photos_field on photos(field_id, season_year);

alter table photos enable row level security;

drop policy if exists "anon full access on photos" on photos;
create policy "anon full access on photos"
    on photos for all to anon using (true) with check (true);
