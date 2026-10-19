"""
Daily cleanup for Pixel Campus events (run by .github/workflows/cleanup-events.yml).

Deletes, together with every file in their flyer folder:
  * events past their expiry (ends_at + 3 days, set by the database)
  * rejected or removed events nobody has touched for 7 days
and sweeps the flyers bucket for folders whose event no longer exists (left behind by an interrupted
upload or delete), once their files are over an hour old.

Files are deleted before rows, so if anything fails the event is still there and the next run retries.

It needs the project's service_role key, which bypasses every access rule, so the key only ever lives in
a GitHub Actions secret named SUPABASE_SERVICE_ROLE_KEY. Never commit it or put it in the website.

  SUPABASE_SERVICE_ROLE_KEY=... python3 cleanup_events.py --dry-run     # report only, delete nothing
Standard library only.
"""
import argparse
import datetime as dt
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ioomfxnbjfavzgcaqhbf.supabase.co")
BUCKET = "flyers"
REJECTED_GRACE = dt.timedelta(days=7)
ORPHAN_GRACE = dt.timedelta(hours=1)    # never touch files a post-in-progress might still be using
PAGE = 1000


def parse_time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


class Supabase:
    """The few REST and Storage calls the cleanup needs."""

    def __init__(self, url, key):
        self.url, self.key = url.rstrip("/"), key
        try:   # python.org builds on macOS ship without CA certificates; certifi fills the gap when installed
            import certifi
            self.ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            self.ctx = None

    def _call(self, method, path, body=None, headers=None):
        req = urllib.request.Request(self.url + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header("apikey", self.key)
        req.add_header("Authorization", f"Bearer {self.key}")
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=60, context=self.ctx) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    def events(self):
        out, offset = [], 0
        while True:
            q = urllib.parse.urlencode({"select": "id,status,expires_at,updated_at", "order": "id",
                                        "limit": PAGE, "offset": offset})
            page = self._call("GET", f"/rest/v1/events?{q}")
            out += page
            if len(page) < PAGE:
                return out
            offset += PAGE

    def list(self, prefix):
        """Entries directly under a folder: folders have no id, files have id, size and created_at."""
        out, offset = [], 0
        while True:
            page = self._call("POST", f"/storage/v1/object/list/{BUCKET}",
                              {"prefix": prefix, "limit": PAGE, "offset": offset,
                               "sortBy": {"column": "name", "order": "asc"}})
            out += page
            if len(page) < PAGE:
                return out
            offset += PAGE

    def delete_files(self, paths):
        for i in range(0, len(paths), 100):
            self._call("DELETE", f"/storage/v1/object/{BUCKET}", {"prefixes": paths[i:i + 100]})

    def delete_events(self, ids):
        for i in range(0, len(ids), 50):
            q = urllib.parse.urlencode({"id": f"in.({','.join(ids[i:i + 50])})"})
            self._call("DELETE", f"/rest/v1/events?{q}", headers={"Prefer": "return=minimal"})


def plan(api, now):
    """Work out what to delete. Returns (event ids, file paths, bytes, notes); deletes nothing."""
    events = api.events()
    by_id = {e["id"]: e for e in events}
    doomed = set()
    for e in events:
        if parse_time(e["expires_at"]) < now:
            doomed.add(e["id"])
        elif e["status"] in ("rejected", "removed") and parse_time(e["updated_at"]) < now - REJECTED_GRACE:
            doomed.add(e["id"])

    files, size, orphans = [], 0, 0
    for user in api.list(""):
        if user.get("id") is not None:          # a stray file at the top level: not ours to judge
            continue
        for folder in api.list(user["name"] + "/"):
            if folder.get("id") is not None:
                continue
            event_id, prefix = folder["name"], f"{user['name']}/{folder['name']}/"
            if event_id in doomed:
                take = api.list(prefix)
            elif event_id not in by_id:          # the event is gone but its files stayed behind
                take = [f for f in api.list(prefix)
                        if f.get("created_at") and parse_time(f["created_at"]) < now - ORPHAN_GRACE]
                if take:
                    orphans += 1
            else:
                continue
            for f in take:
                if f.get("id") is None:
                    continue
                files.append(prefix + f["name"])
                size += (f.get("metadata") or {}).get("size", 0)
    return sorted(doomed), files, size, orphans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report what would be deleted, delete nothing")
    args = ap.parse_args()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        sys.exit("SUPABASE_SERVICE_ROLE_KEY is not set (it belongs in a GitHub Actions secret)")

    api = Supabase(SUPABASE_URL, key)
    now = dt.datetime.now(dt.timezone.utc)
    ids, files, size, orphans = plan(api, now)
    verb = "Would delete" if args.dry_run else "Deleting"
    print(f"{verb} {len(ids)} event(s) and {len(files)} file(s), {size / 1024:.0f} KB"
          f" ({orphans} leftover folder(s) without an event)")
    for p in files:
        print("  file", p)
    for i in ids:
        print("  event", i)
    if args.dry_run or not (ids or files):
        return
    api.delete_files(files)      # files first: if this fails, the rows stay and tomorrow retries
    api.delete_events(ids)
    print("Done.")


if __name__ == "__main__":
    main()
