"""
Fetch Brown Dining menus and write a small output/dining.json for the map.

Brown's menu site (menus.dining.brown.edu) loads one public JSON feed with a week of menus for every
location (~2.5 MB). This script keeps only what the map needs: the next few days, each meal's hours,
and item names with dietary tags and allergens (~a few hundred KB, gzips much smaller).

Run it on a schedule (see .github/workflows/update-dining.yml) or by hand:
  python3 fetch_dining.py
Standard library only, so it runs anywhere Python 3.9+ does.
"""
import ast
import datetime as dt
import json
import os
import ssl
import sys
import urllib.request
from zoneinfo import ZoneInfo

SOURCE = "https://esb-level1.brown.edu/services/oit/sys/brown-dining/v1/menus"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
DAYS = 3   # today plus the next two days
TZ = ZoneInfo("America/New_York")

# Where each location sits on the map. "building" is a name from buildings.json (its center is used, or
# its bottom-right corner with "corner"); "pos" is a fixed map position in meters as a fallback.
LOCATIONS = {
    "SHRP": {"building": "Sharpe Refectory", "nickname": "the Ratty", "offset": [-10, -4]},
    "IVY":  {"building": "Sharpe Refectory", "offset": [12, 8]},       # lower level of the same building
    "AC":   {"building": "Andrews Commons", "nickname": "Andrews"},
    "BR":   {"building": "Stephen Robert '62 Campus Center"},
    "VW":   {"building": "Verney-Woolley Hall", "nickname": "the V-Dub"},
    "SOE":  {"building": "Barus & Holley"},                             # 184 Hope St
    "JOS":  {"building": "Vartan Gregorian Quad A", "corner": True, "nickname": "Jo's"},   # its bottom-right corner
}


def parse_hours(h):
    """Hours arrive either as a dict or as a Python-dict-looking string."""
    if isinstance(h, str):
        h = ast.literal_eval(h)
    return h["start"], h["end"]


def main():
    req = urllib.request.Request(SOURCE, headers={"User-Agent": "PixelCampus dining sync"})
    try:   # python.org builds on macOS ship without CA certificates; certifi fills the gap when installed
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = None
    with urllib.request.urlopen(req, timeout=60, context=context) as r:
        raw = json.load(r)

    with open(os.path.join(OUT, "buildings.json")) as f:
        buildings = {b["name"]: b for b in json.load(f)}

    def anchor(b, corner):
        if not corner:
            return b["center"]
        x, y = max((p for poly in b["polygons"] for p in poly), key=lambda p: p[0] + p[1])
        return [x - 3, y - 3]   # just inside the corner (meters; x right, y down)

    today = dt.datetime.now(TZ).date()
    keep = {(today + dt.timedelta(days=i)).isoformat() for i in range(DAYS)}

    locations = []
    for loc in raw:
        lid = loc["locationId"]
        place = LOCATIONS.get(lid, {})
        pos = place.get("pos")
        if pos is None and place.get("building") in buildings:
            c, off = anchor(buildings[place["building"]], place.get("corner")), place.get("offset", [0, 0])
            pos = [round(c[0] + off[0], 1), round(c[1] + off[1], 1)]
        if pos is None:
            print(f"warning: no map position for {lid} ({loc['name']}); it will be listed but not pinned",
                  file=sys.stderr)

        meals = []
        for day, entries in sorted(loc.get("meals", {}).items()):
            if day not in keep:
                continue
            for e in entries or []:
                menu = e.get("menu") or {}
                if not menu.get("hours"):
                    continue
                start, end = parse_hours(menu["hours"])
                stations = []
                for s in menu.get("stations") or []:
                    # items as [name, tags, allergens] to keep the file small
                    items = [[it["item"].strip(), it.get("icons") or [], it.get("allergens") or []]
                             for it in s.get("items") or [] if it.get("item")]
                    if items:
                        stations.append({"name": s["name"].strip(), "items": items})
                meals.append({"date": day, "meal": e["meal"], "start": start, "end": end, "stations": stations})
        meals.sort(key=lambda m: m["start"])

        locations.append({
            "id": lid, "name": loc["name"], "nickname": place.get("nickname"),
            "address": loc.get("locationAddress"), "pos": pos, "meals": meals,
        })

    out = {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": SOURCE,
        "tags": {"VGN": "Vegan", "VGTN": "Vegetarian", "HL": "Halal", "KSHR": "Kosher", "SO": "Fried in shared oil"},
        "locations": locations,
    }
    path = os.path.join(OUT, "dining.json")
    try:   # leave the file alone when only the timestamp would change, so scheduled runs don't commit noise
        with open(path) as f:
            old = json.load(f)
        if {**old, "fetched_at": None} == {**out, "fetched_at": None}:
            print(f"{path} unchanged")
            return
    except (OSError, ValueError):
        pass
    with open(path, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    n = sum(len(l["meals"]) for l in locations)
    print(f"Wrote {path}: {len(locations)} locations, {n} meals, {os.path.getsize(path) // 1024} KB")


if __name__ == "__main__":
    main()
