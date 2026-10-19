-- Pixel Campus: events with flyers (Phase 0 of the event-posting plan)
--
-- Run once in the Supabase dashboard: SQL Editor -> New query -> paste this file -> Run.
-- It is safe to run again after edits: tables are created only if missing, and functions, triggers
-- and policies are replaced.
--
-- What it sets up:
--   * profiles, events, flyers, reports tables
--   * sign-up limited to @brown.edu email addresses
--   * row-level security: who can read and write what (see the event-posting plan's "Access rules")
--   * a private "flyers" storage bucket with upload rules
--
-- Everything the website can do is decided here. The public "anon" key in the site grants nothing
-- beyond these rules; never put the service_role key in the site or the repo.


-- ============================================================================================
-- Types
-- ============================================================================================

do $$ begin
  create type public.event_status as enum ('pending', 'approved', 'rejected', 'removed');
exception when duplicate_object then null;
end $$;


-- ============================================================================================
-- Tables
-- ============================================================================================

create table if not exists public.profiles (
  id            uuid primary key references auth.users (id) on delete cascade,
  display_name  text not null check (char_length(display_name) between 1 and 40),
  class_year    smallint check (class_year between 1900 and 2100),
  is_moderator  boolean not null default false,
  created_at    timestamptz not null default now()
);

create table if not exists public.events (
  id             uuid primary key default gen_random_uuid(),
  owner_id       uuid not null default auth.uid() references public.profiles (id) on delete cascade,
  title          text not null check (char_length(title) between 3 and 120),
  description    text not null default '' check (char_length(description) <= 2000),
  building       text not null check (char_length(building) between 1 and 120),
  pos_x          real not null check (pos_x between 0 and 2000),   -- map meters, same as buildings.json
  pos_y          real not null check (pos_y between 0 and 2000),
  starts_at      timestamptz not null,
  ends_at        timestamptz not null,
  status         public.event_status not null default 'pending',
  reject_reason  text check (char_length(reject_reason) <= 500),
  expires_at     timestamptz not null,                             -- set by trigger: ends_at + 3 days
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  check (ends_at > starts_at),
  check (ends_at - starts_at <= interval '14 days')
);
create index if not exists events_live_idx on public.events (status, expires_at);
create index if not exists events_owner_idx on public.events (owner_id, created_at);

create table if not exists public.flyers (
  id          uuid primary key default gen_random_uuid(),
  event_id    uuid not null unique references public.events (id) on delete cascade,   -- one flyer per event
  image_path  text not null,
  thumb_path  text not null,
  pdf_path    text,
  width       integer check (width between 1 and 4000),
  height      integer check (height between 1 and 6000),
  bytes       integer check (bytes between 1 and 3000000),
  created_at  timestamptz not null default now()
);

create table if not exists public.reports (
  id           uuid primary key default gen_random_uuid(),
  event_id     uuid not null references public.events (id) on delete cascade,
  reporter_id  uuid not null default auth.uid() references public.profiles (id) on delete cascade,
  reason       text not null check (char_length(reason) between 1 and 500),
  created_at   timestamptz not null default now(),
  resolved_by  uuid references public.profiles (id) on delete set null,
  resolved_at  timestamptz,
  unique (event_id, reporter_id)                                  -- one report per person per event
);


-- ============================================================================================
-- Helper functions
-- ============================================================================================

-- True when the signed-in user is a moderator. Runs with the owner's rights so policies can call it
-- without exposing the is_moderator column to the site.
create or replace function public.is_moderator()
returns boolean
language sql stable security definer set search_path = ''
as $$
  select coalesce((select p.is_moderator from public.profiles p where p.id = auth.uid()), false);
$$;


-- ============================================================================================
-- Accounts: @brown.edu only, and a profile for every new user
-- ============================================================================================

create or replace function public.require_brown_email()
returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
  if new.email is null or lower(new.email) !~ '@brown\.edu$' then
    raise exception 'Pixel Campus sign-up is limited to @brown.edu email addresses';
  end if;
  return new;
end;
$$;

drop trigger if exists require_brown_email on auth.users;
create trigger require_brown_email
  before insert or update of email on auth.users
  for each row execute function public.require_brown_email();

create or replace function public.create_profile()
returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, left(split_part(new.email, '@', 1), 40))
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists create_profile on auth.users;
create trigger create_profile
  after insert on auth.users
  for each row execute function public.create_profile();


-- ============================================================================================
-- Events: expiry, limits, moderation
-- ============================================================================================

create or replace function public.prepare_event()
returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
  new.expires_at := new.ends_at + interval '3 days';
  new.updated_at := now();

  if tg_op = 'INSERT' then
    if new.starts_at > now() + interval '60 days' then
      raise exception 'Events can be posted at most 60 days ahead';
    end if;
    if new.ends_at < now() then
      raise exception 'This event has already ended';
    end if;
    if (select count(*) from public.events e
        where e.owner_id = new.owner_id and e.created_at > now() - interval '1 day') >= 3 then
      raise exception 'You can post up to 3 events a day';
    end if;
    new.status := 'pending';              -- every new event waits for a moderator
    new.reject_reason := null;
  end if;
  return new;
end;
$$;

drop trigger if exists prepare_event on public.events;
create trigger prepare_event
  before insert or update on public.events
  for each row execute function public.prepare_event();

-- Moderators approve, reject or remove events through this function (the site cannot set status).
create or replace function public.moderate_event(event_id uuid, new_status public.event_status, reason text default null)
returns void
language plpgsql security definer set search_path = ''
as $$
begin
  if not public.is_moderator() then
    raise exception 'Only moderators can change an event''s status';
  end if;
  update public.events
     set status = new_status,
         reject_reason = case when new_status in ('rejected', 'removed') then reason end
   where id = event_id;
  if new_status <> 'pending' then         -- a decision resolves the open reports
    update public.reports
       set resolved_by = auth.uid(), resolved_at = now()
     where reports.event_id = moderate_event.event_id and resolved_at is null;
  end if;
end;
$$;

-- Three open reports send an approved event back to the review queue until a moderator looks.
create or replace function public.hide_reported_event()
returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
  if (select count(*) from public.reports r
      where r.event_id = new.event_id and r.resolved_at is null) >= 3 then
    update public.events set status = 'pending' where id = new.event_id and status = 'approved';
  end if;
  return new;
end;
$$;

drop trigger if exists hide_reported_event on public.reports;
create trigger hide_reported_event
  after insert on public.reports
  for each row execute function public.hide_reported_event();


-- ============================================================================================
-- Table permissions: what the site's two roles may touch at all (row rules come next)
--   anon          = visitors who are not signed in
--   authenticated = signed-in Brown users
-- ============================================================================================

revoke all on public.profiles, public.events, public.flyers, public.reports from anon, authenticated;

grant select (id, display_name) on public.profiles to anon;
grant select (id, display_name, class_year, created_at) on public.profiles to authenticated;
grant update (display_name, class_year) on public.profiles to authenticated;

grant select on public.events to anon, authenticated;
grant insert (title, description, building, pos_x, pos_y, starts_at, ends_at) on public.events to authenticated;
grant update (title, description, building, pos_x, pos_y, starts_at, ends_at) on public.events to authenticated;
grant delete on public.events to authenticated;

grant select on public.flyers to anon, authenticated;
grant insert (event_id, image_path, thumb_path, pdf_path, width, height, bytes) on public.flyers to authenticated;
grant delete on public.flyers to authenticated;

grant select on public.reports to authenticated;
grant insert (event_id, reason) on public.reports to authenticated;
grant delete on public.reports to authenticated;

revoke execute on function public.moderate_event(uuid, public.event_status, text) from public, anon;
grant execute on function public.moderate_event(uuid, public.event_status, text) to authenticated;
grant execute on function public.is_moderator() to anon, authenticated;


-- ============================================================================================
-- Row-level security
-- ============================================================================================

alter table public.profiles enable row level security;
alter table public.events   enable row level security;
alter table public.flyers   enable row level security;
alter table public.reports  enable row level security;

-- profiles ---------------------------------------------------------------------------------
drop policy if exists "profiles: names are readable" on public.profiles;
create policy "profiles: names are readable" on public.profiles
  for select to anon, authenticated using (true);

drop policy if exists "profiles: edit your own" on public.profiles;
create policy "profiles: edit your own" on public.profiles
  for update to authenticated
  using (id = (select auth.uid())) with check (id = (select auth.uid()));

-- events -----------------------------------------------------------------------------------
drop policy if exists "events: approved and live, for everyone" on public.events;
create policy "events: approved and live, for everyone" on public.events
  for select to anon, authenticated using (status = 'approved' and expires_at > now());

drop policy if exists "events: your own" on public.events;
create policy "events: your own" on public.events
  for select to authenticated using (owner_id = (select auth.uid()));

drop policy if exists "events: moderators see all" on public.events;
create policy "events: moderators see all" on public.events
  for select to authenticated using ((select public.is_moderator()));

drop policy if exists "events: post as yourself" on public.events;
create policy "events: post as yourself" on public.events
  for insert to authenticated with check (owner_id = (select auth.uid()));

drop policy if exists "events: edit your own while pending" on public.events;
create policy "events: edit your own while pending" on public.events
  for update to authenticated
  using (owner_id = (select auth.uid()) and status = 'pending')
  with check (owner_id = (select auth.uid()));

drop policy if exists "events: owner or moderator deletes" on public.events;
create policy "events: owner or moderator deletes" on public.events
  for delete to authenticated
  using (owner_id = (select auth.uid()) or (select public.is_moderator()));

-- flyers: visible whenever their event is visible to you ------------------------------------
drop policy if exists "flyers: same visibility as the event" on public.flyers;
create policy "flyers: same visibility as the event" on public.flyers
  for select to anon, authenticated
  using (exists (select 1 from public.events e where e.id = event_id));

drop policy if exists "flyers: attach to your own event" on public.flyers;
create policy "flyers: attach to your own event" on public.flyers
  for insert to authenticated
  with check (
    exists (select 1 from public.events e where e.id = event_id and e.owner_id = (select auth.uid()))
    and image_path like (select auth.uid())::text || '/' || event_id::text || '/%'
    and thumb_path like (select auth.uid())::text || '/' || event_id::text || '/%'
    and (pdf_path is null or pdf_path like (select auth.uid())::text || '/' || event_id::text || '/%')
  );

drop policy if exists "flyers: owner or moderator deletes" on public.flyers;
create policy "flyers: owner or moderator deletes" on public.flyers
  for delete to authenticated
  using (
    exists (select 1 from public.events e where e.id = event_id and e.owner_id = (select auth.uid()))
    or (select public.is_moderator())
  );

-- reports ----------------------------------------------------------------------------------
drop policy if exists "reports: yours, or all for moderators" on public.reports;
create policy "reports: yours, or all for moderators" on public.reports
  for select to authenticated
  using (reporter_id = (select auth.uid()) or (select public.is_moderator()));

drop policy if exists "reports: report an approved event" on public.reports;
create policy "reports: report an approved event" on public.reports
  for insert to authenticated
  with check (
    reporter_id = (select auth.uid())
    and exists (select 1 from public.events e where e.id = event_id and e.status = 'approved')
  );

drop policy if exists "reports: moderators delete" on public.reports;
create policy "reports: moderators delete" on public.reports
  for delete to authenticated using ((select public.is_moderator()));


-- ============================================================================================
-- Storage: private "flyers" bucket, files at <user id>/<event id>/<file>
-- ============================================================================================

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('flyers', 'flyers', false, 2097152,
        array['image/webp', 'image/jpeg', 'image/png', 'application/pdf'])
on conflict (id) do update
  set public = excluded.public,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "flyers bucket: upload into your own event's folder" on storage.objects;
create policy "flyers bucket: upload into your own event's folder" on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'flyers'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and exists (select 1 from public.events e
                where e.id::text = (storage.foldername(name))[2] and e.owner_id = (select auth.uid()))
  );

drop policy if exists "flyers bucket: readable when the event is" on storage.objects;
create policy "flyers bucket: readable when the event is" on storage.objects
  for select to anon, authenticated
  using (
    bucket_id = 'flyers'
    and exists (select 1 from public.events e where e.id::text = (storage.foldername(name))[2])
  );

drop policy if exists "flyers bucket: owner or moderator deletes" on storage.objects;
create policy "flyers bucket: owner or moderator deletes" on storage.objects
  for delete to authenticated
  using (
    bucket_id = 'flyers'
    and ((storage.foldername(name))[1] = (select auth.uid())::text or (select public.is_moderator()))
  );
