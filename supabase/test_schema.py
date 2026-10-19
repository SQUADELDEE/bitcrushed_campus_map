"""
Test supabase/schema.sql without touching the real project: it starts a throwaway local Postgres,
stubs the parts of Supabase's auth and storage the schema uses, runs the schema (twice, to check it
can be re-run), then exercises every rule as a visitor, signed-in users and a moderator.

  uv venv .pgtest && uv pip install --python .pgtest/bin/python pgserver 'psycopg[binary]'
  .pgtest/bin/python supabase/test_schema.py supabase/schema.sql
"""
import sys, tempfile, uuid
import pgserver, psycopg

SCHEMA = open(sys.argv[1]).read()

STUB = """
create role anon nologin; create role authenticated nologin; create role service_role nologin;
create schema auth; create schema storage;
create table auth.users (id uuid primary key default gen_random_uuid(), email text);
create function auth.uid() returns uuid language sql stable as
  $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
create table storage.buckets (id text primary key, name text, public boolean, file_size_limit bigint, allowed_mime_types text[]);
create table storage.objects (id uuid primary key default gen_random_uuid(), bucket_id text, name text);
alter table storage.objects enable row level security;
create function storage.foldername(name text) returns text[] language sql immutable as
  $$ select (string_to_array(name, '/'))[1:array_length(string_to_array(name, '/'), 1) - 1] $$;
grant usage on schema public, auth, storage to anon, authenticated;
grant execute on function auth.uid() to anon, authenticated;
grant all on storage.objects to anon, authenticated;
alter default privileges in schema public grant all on tables to anon, authenticated;
"""

results = []
def check(name, ok, detail=""):
    results.append((ok, name, detail))

d = tempfile.mkdtemp()
srv = pgserver.get_server(d, cleanup_mode="stop")
uri = srv.get_uri()
with psycopg.connect(uri, autocommit=True) as c:
    c.execute(STUB)
    c.execute(SCHEMA)
    c.execute(SCHEMA)            # re-running must also work
    check("schema runs, and runs twice", True)

    def as_user(uid):
        c.execute("reset role"); c.execute("select set_config('request.jwt.claim.sub', %s, false)", (uid or "",))
        c.execute("set role authenticated" if uid else "set role anon")

    def expect_error(name, sql, params=()):
        try:
            c.execute(sql, params); check(name, False, "no error raised")
        except psycopg.Error as e:
            check(name, True, str(e).splitlines()[0][:80])

    def rows(sql, params=()):
        return c.execute(sql, params).fetchall()

    # --- accounts ---
    c.execute("reset role")
    expect_error("gmail sign-up is refused", "insert into auth.users (email) values ('x@gmail.com')")
    expect_error("look-alike domain refused", "insert into auth.users (email) values ('x@notbrown.edu')")
    A, B, M, R1, R2, R3 = (str(uuid.uuid4()) for _ in range(6))
    for uid, mail in [(A, "alice@brown.edu"), (B, "bob@Brown.edu"), (M, "mod@brown.edu"),
                      (R1, "r1@brown.edu"), (R2, "r2@brown.edu"), (R3, "r3@brown.edu")]:
        c.execute("insert into auth.users (id, email) values (%s, %s)", (uid, mail))
    check("brown.edu sign-up creates a profile", rows("select display_name from public.profiles where id=%s", (A,)) == [("alice",)])
    c.execute("update public.profiles set is_moderator = true where id=%s", (M,))

    # --- posting ---
    as_user(A)
    ev = "insert into public.events (title, building, pos_x, pos_y, starts_at, ends_at) values (%s, 'Sayles Hall', 900, 950, now() + interval '1 day', now() + interval '1 day 2 hours') returning id, status, expires_at - ends_at"
    e1, st, grace = rows(ev, ("Open mic night",))[0]
    check("new event is pending", st == "pending")
    check("expires 3 days after it ends", str(grace) == "3 days, 0:00:00", str(grace))
    expect_error("cannot set status when posting", "insert into public.events (title, building, pos_x, pos_y, starts_at, ends_at, status) values ('Sneaky', 'X', 1, 1, now()+interval '1 day', now()+interval '2 days', 'approved')")
    expect_error("cannot post as someone else", "insert into public.events (owner_id, title, building, pos_x, pos_y, starts_at, ends_at) values (%s, 'Imposter', 'X', 1, 1, now()+interval '1 day', now()+interval '2 days')", (B,))
    expect_error("cannot post more than 60 days ahead", "insert into public.events (title, building, pos_x, pos_y, starts_at, ends_at) values ('Far', 'X', 1, 1, now()+interval '90 days', now()+interval '91 days')")
    rows(ev, ("Second",)); rows(ev, ("Third",))
    expect_error("4th event in a day is refused", ev, ("Fourth",))

    as_user(None)
    check("visitors cannot see pending events", rows("select count(*) from public.events")[0][0] == 0)
    as_user(B)
    check("other users cannot see pending events", rows("select count(*) from public.events")[0][0] == 0)
    n = c.execute("update public.events set title='Hacked' where id=%s", (e1,)).rowcount
    check("other users cannot edit your event", n == 0)
    expect_error("users cannot call moderate_event", "select public.moderate_event(%s, 'approved')", (e1,))
    expect_error("users cannot make themselves moderator", "update public.profiles set is_moderator = true where id=%s", (B,))
    as_user(A)
    check("owner sees own pending events", rows("select count(*) from public.events")[0][0] == 3)
    check("owner can edit while pending", c.execute("update public.events set title='Open mic night!' where id=%s", (e1,)).rowcount == 1)

    # --- storage ---
    as_user(A)
    c.execute("insert into storage.objects (bucket_id, name) values ('flyers', %s)", (f"{A}/{e1}/flyer.webp",))
    c.execute("insert into storage.objects (bucket_id, name) values ('flyers', %s)", (f"{A}/{e1}/thumb.webp",))
    check("owner uploads into own event folder", True)
    c.execute("insert into public.flyers (event_id, image_path, thumb_path, bytes) values (%s, %s, %s, 150000)",
              (e1, f"{A}/{e1}/flyer.webp", f"{A}/{e1}/thumb.webp"))
    check("owner attaches flyer row", True)
    as_user(B)
    expect_error("cannot upload into someone else's folder", "insert into storage.objects (bucket_id, name) values ('flyers', %s)", (f"{A}/{e1}/x.webp",))
    expect_error("cannot upload under own id to another's event", "insert into storage.objects (bucket_id, name) values ('flyers', %s)", (f"{B}/{e1}/x.webp",))
    as_user(None)
    check("visitors cannot see pending flyer files", rows("select count(*) from storage.objects")[0][0] == 0)

    # --- moderation ---
    as_user(M)
    check("moderator sees all pending", rows("select count(*) from public.events where status='pending'")[0][0] == 3)
    c.execute("select public.moderate_event(%s, 'approved')", (e1,))
    as_user(None)
    check("visitors see approved event", rows("select title from public.events") == [("Open mic night!",)])
    check("visitors see its flyer row", rows("select count(*) from public.flyers")[0][0] == 1)
    check("visitors can read its files", rows("select count(*) from storage.objects")[0][0] == 2)
    check("visitors see display names only", rows("select display_name from public.profiles where id=%s", (A,)) == [("alice",)])
    expect_error("visitors cannot read emails/moderator flag", "select is_moderator from public.profiles")
    as_user(A)
    check("owner cannot edit after approval", c.execute("update public.events set title='Changed' where id=%s", (e1,)).rowcount == 0)

    # --- reports ---
    for r in (R1, R2):
        as_user(r); c.execute("insert into public.reports (event_id, reason) values (%s, 'spam')", (e1,))
    as_user(None)
    check("2 reports keep it visible", rows("select count(*) from public.events")[0][0] == 1)
    as_user(R3); c.execute("insert into public.reports (event_id, reason) values (%s, 'spam')", (e1,))
    as_user(None)
    check("3rd report hides it", rows("select count(*) from public.events")[0][0] == 0)
    as_user(R1)
    check("reporters see only their own report", rows("select count(*) from public.reports")[0][0] == 1)

    # --- expiry ---
    c.execute("reset role")
    c.execute("update public.events set status='approved', starts_at=now()-interval '5 days', ends_at=now()-interval '4 days' where id=%s", (e1,))
    as_user(None)
    check("expired events disappear", rows("select count(*) from public.events")[0][0] == 0)

srv.cleanup()
for ok, name, detail in results:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
print(f"\n{sum(r[0] for r in results)}/{len(results)} passed")
