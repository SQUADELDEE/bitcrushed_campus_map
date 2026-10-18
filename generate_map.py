"""
Generate a multi-resolution pixel-art base map of an area from OpenStreetMap data.

The same OSM data is rasterized at several resolutions ("levels"). Coarse levels drop small
features (footpaths, single trees); fine levels add detail (sidewalks, crosswalks, road
markings, tree canopies, building shading). The viewer swaps levels as you zoom.

Outputs (in ./output):
  meta.json                 map size, levels, tile classes
  levels/<mpp>/<tx>_<ty>.png       colored pixel-art tiles, 1 pixel = <mpp> meters
  levels/<mpp>/grid_<tx>_<ty>.png  grayscale tiles, pixel value = tile class id (grass / path / building...)
  buildings.json            named buildings with polygons (meters from top-left)
  streets.json              named streets with polylines (meters from top-left), for labels

Usage:
  .venv/bin/python generate_map.py                    # Brown University defaults
  .venv/bin/python generate_map.py --lat 41.8268 --lon -71.4025 --radius 900 --levels 4,2,1,0.5
"""
import argparse
import json
import os
import shutil

import geopandas as gpd
import numpy as np
import osmnx as ox
from PIL import Image, ImageDraw
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon

# --- Tile classes: (name, base color, color variation for texture) -----------------
CLASSES = [
    ("ground",       "#b9c98c", 6),
    ("grass",        "#86ba5c", 8),
    ("trees",        "#4f8f3f", 10),
    ("water",        "#4a8fd1", 4),
    ("sand",         "#e2cf92", 5),
    ("pitch",        "#5fae4f", 3),
    ("track",        "#c0624a", 3),
    ("parking",      "#a3a3a6", 3),
    ("plaza",        "#d9cfbb", 4),
    ("road",         "#6c6c75", 2),
    ("path",         "#e8d8aa", 3),
    ("rail",         "#5a5050", 2),
    ("building",     "#b9ab9b", 3),   # generic buildings (houses, shops)
    ("campus_bldg",  "#bf5b3f", 3),   # university buildings
    ("bldg_edge",    "#5e3b2e", 0),
    ("tree",         "#3a7330", 6),
    ("sidewalk",     "#cfc8b8", 3),
    ("crosswalk",    "#eeeeea", 0),
    ("road_mark",    "#e9cf6a", 0),
]
CID = {name: i for i, (name, _, _) in enumerate(CLASSES)}
BUILDING_IDS = [CID["building"], CID["campus_bldg"], CID["bldg_edge"]]

# Area polygons: (class, predicate on tags). Drawn in this order, later wins.
AREA_RULES = [
    ("grass",   lambda t: t.get("landuse") in ("grass", "meadow", "recreation_ground", "village_green", "cemetery")
                          or t.get("leisure") in ("park", "garden", "common", "golf_course")
                          or t.get("natural") in ("grassland", "scrub")),
    ("trees",   lambda t: t.get("landuse") == "forest" or t.get("natural") == "wood"),
    ("sand",    lambda t: t.get("natural") in ("sand", "beach") or t.get("leisure") == "playground"),
    ("pitch",   lambda t: t.get("leisure") in ("pitch", "stadium")),
    ("track",   lambda t: t.get("leisure") == "track"),
    ("water",   lambda t: t.get("natural") == "water" or t.get("landuse") in ("reservoir", "basin")
                          or t.get("waterway") == "riverbank" or t.get("leisure") == "swimming_pool"),
    ("parking", lambda t: t.get("amenity") == "parking" and t.get("parking") not in ("underground", "multi-storey")),
    ("plaza",   lambda t: t.get("highway") == "pedestrian" or t.get("place") == "square"
                          or t.get("area:highway") is not None),
]

# Road widths in meters
ROAD_WIDTH = {
    "motorway": 16, "trunk": 14, "primary": 12, "secondary": 11, "tertiary": 9,
    "motorway_link": 8, "trunk_link": 8, "primary_link": 8, "secondary_link": 8, "tertiary_link": 8,
    "residential": 7, "unclassified": 7, "living_street": 6, "service": 4.5,
}
MARKED_ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary"}
PATH_WIDTH = {"footway": 2.5, "path": 2.5, "pedestrian": 4, "cycleway": 2.5, "steps": 2.5, "track": 3, "bridleway": 2.5}

# Label priority for street names: lower rank = shown when more zoomed out
STREET_RANK = {"motorway": 0, "trunk": 0, "primary": 0, "secondary": 0, "tertiary": 1,
               "residential": 2, "unclassified": 2, "living_street": 2}

TILE = 512  # levels are cut into TILE x TILE pngs so the viewer only loads what's on screen

TAGS = {
    "building": True, "highway": True, "landuse": True, "leisure": True, "natural": True,
    "amenity": ["parking", "university", "college"], "waterway": True, "railway": True,
    "place": "square", "area:highway": True,
}


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def clean(v):
    """GeoDataFrame cells are NaN for missing tags; normalize to None."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return v


def parts(geom):
    return list(geom.geoms) if isinstance(geom, (MultiPolygon, MultiLineString)) else [geom]


def skip_underground(t):
    return (t.get("tunnel") not in (None, "no")) or str(t.get("layer") or "0").startswith("-") \
        or t.get("location") == "underground" or t.get("indoor") not in (None, "no")


def render_level(rows, campus_union, to_m, size_m, mpp, seed=7):
    """Rasterize all features at `mpp` meters per pixel. Returns (grid uint8 array, rgb image)."""
    W = H = int(round(size_m / mpp))
    img = Image.new("L", (W, H), CID["ground"])
    d = ImageDraw.Draw(img)
    fine = mpp <= 1      # sidewalks, crosswalks, road markings, canopies
    coarse = mpp >= 4    # drop footpaths and single trees

    def to_px(coords):
        return [(x / mpp, y / mpp) for x, y in to_m(coords)]

    def fill(geom, cls):
        for p in parts(geom):
            if not p.interiors:
                d.polygon(to_px(p.exterior.coords), fill=cls)
                continue
            # Polygon with holes: paint through a mask so the holes keep what's underneath
            mask = Image.new("L", (W, H), 0)
            md = ImageDraw.Draw(mask)
            md.polygon(to_px(p.exterior.coords), fill=255)
            for hole in p.interiors:
                md.polygon(to_px(hole.coords), fill=0)
            img.paste(cls, mask=mask)

    def stroke(geom, cls, width_m, min_px=1):
        w = max(min_px, int(round(width_m / mpp)))
        for ln in parts(geom):
            pts = to_px(ln.coords)
            d.line(pts, fill=cls, width=w, joint="curve" if w > 2 else None)
            if w > 2:  # round caps so road ends meet cleanly at intersections
                r = w / 2
                for x, y in (pts[0], pts[-1]):
                    d.ellipse([x - r, y - r, x + r, y + r], fill=cls)

    def dashed(geom, cls, dash_m=3, gap_m=4, width_m=0):
        w = max(1, int(round(width_m / mpp)))
        for ln in parts(geom):
            dist = 0
            while dist < ln.length:
                seg = [ln.interpolate(dist), ln.interpolate(min(dist + dash_m, ln.length))]
                d.line(to_px([p.coords[0] for p in seg]), fill=cls, width=w)
                dist += dash_m + gap_m

    lines = [(t, g) for t, g in rows if isinstance(g, (LineString, MultiLineString)) and not skip_underground(t)]

    # 1. Area fills (landuse, parks, water, parking, plazas)
    for cls, pred in AREA_RULES:
        for t, g in rows:
            if isinstance(g, (Polygon, MultiPolygon)) and not t.get("building") and pred(t):
                fill(g, CID[cls])

    # 2. Rivers / streams
    for t, g in lines:
        if t.get("waterway") in ("river", "stream", "canal"):
            stroke(g, CID["water"], 10 if t["waterway"] == "river" else 3)

    # 3. Sidewalks under roads (fine only), then roads, markings, rail, paths, crosswalks
    for t, g in lines:
        if fine and t.get("footway") == "sidewalk":
            stroke(g, CID["sidewalk"], 2.5)
    for t, g in lines:
        if t.get("highway") in ROAD_WIDTH:
            stroke(g, CID["road"], ROAD_WIDTH[t["highway"]])
    if fine:
        for t, g in lines:
            if t.get("highway") in MARKED_ROADS and t.get("oneway") != "yes":
                dashed(g, CID["road_mark"])
    for t, g in lines:
        if t.get("railway") in ("rail", "light_rail", "tram"):
            stroke(g, CID["rail"], 3)
    for t, g in lines:
        hw, fw = t.get("highway"), t.get("footway")
        if hw not in PATH_WIDTH or fw == "sidewalk":
            continue
        if coarse and hw != "pedestrian":
            continue
        if fw == "crossing":
            if mpp <= 0.5:  # zebra bars need a few pixels each to read, so only at the finest level
                dashed(g, CID["crosswalk"], dash_m=1.2, gap_m=1.2, width_m=3.5)
            continue
        stroke(g, CID["path"], PATH_WIDTH[hw])

    # 4. Buildings
    for t, g in rows:
        if t.get("building") and isinstance(g, (Polygon, MultiPolygon)) and not skip_underground(t):
            fill(g, CID["campus_bldg" if is_campus(t, g, campus_union) else "building"])

    grid = np.array(img)

    # Building outline: building pixels that touch a non-building pixel
    bmask = np.isin(grid, [CID["building"], CID["campus_bldg"]])
    pad = np.pad(bmask, 1, constant_values=False)
    interior = pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:]
    grid[bmask & ~interior] = CID["bldg_edge"]

    # 5. Individual trees: plus-shaped blobs at medium detail, round canopies when fine
    if not coarse:
        blocked = set(BUILDING_IDS + [CID["road"], CID["road_mark"], CID["crosswalk"]])
        r = max(1, int(round(2.5 / mpp))) if fine else 1
        offs = [(dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1)
                if (dx * dx + dy * dy <= r * r + r * 0.5 if fine else abs(dx) + abs(dy) <= 1)]
        for t, g in rows:
            if t.get("natural") == "tree" and isinstance(g, Point):
                px, py = to_px([g.coords[0]])[0]
                for dx, dy in offs:
                    x, y = int(px) + dx, int(py) + dy
                    if 0 <= x < W and 0 <= y < H and grid[y, x] not in blocked:
                        grid[y, x] = CID["tree"]

    # --- Color it: per-class palette + per-pixel texture noise ---
    rng = np.random.default_rng(seed)
    noise = rng.integers(-1, 2, size=(H, W))
    rgb = np.zeros((H, W, 3), dtype=np.int16)
    for i, (_, color, var) in enumerate(CLASSES):
        m = grid == i
        rgb[m] = np.array(hex_rgb(color), dtype=np.int16) + (noise[m] * var)[:, None]

    # Shading for depth (light from the top-left)
    if mpp <= 2:
        s = max(1, int(round(2 / mpp)))
        is_b = np.isin(grid, BUILDING_IDS)
        cast = np.zeros_like(is_b)
        for k in range(1, s + 1):          # shadow falls down-right of buildings
            cast[k:, k:] |= is_b[:-k, :-k]
        rgb[cast & ~is_b] = (rgb[cast & ~is_b] * 0.78).astype(np.int16)
    if fine:
        roof = np.isin(grid, [CID["building"], CID["campus_bldg"]])
        top_lit = roof & np.vstack([np.zeros((1, W), bool), grid[:-1] == CID["bldg_edge"]])
        rgb[top_lit] += 22
        canopy = grid == CID["tree"]
        lit = canopy & np.vstack([np.zeros((1, W), bool), grid[:-1] != CID["tree"]])
        rgb[lit] += 25

    return grid, Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")


def is_campus(t, g, campus_union):
    return t["building"] in ("university", "college", "dormitory") \
        or (t.get("operator") or "").lower().startswith("brown university") \
        or (campus_union is not None and campus_union.contains(g.representative_point()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=41.8268)   # Brown University, Main Green
    ap.add_argument("--lon", type=float, default=-71.4025)
    ap.add_argument("--radius", type=float, default=900, help="half-width of the square area, meters")
    ap.add_argument("--levels", default="4,2,1,0.5", help="meters per pixel for each detail level")
    ap.add_argument("--out", default="output")
    args = ap.parse_args()
    levels = sorted((float(v) for v in args.levels.split(",")), reverse=True)

    os.makedirs(args.out, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = os.path.join(args.out, ".osm_cache")

    print(f"Fetching OSM features within {args.radius:.0f} m of ({args.lat}, {args.lon})...")
    gdf = ox.features_from_point((args.lat, args.lon), tags=TAGS, dist=args.radius * 1.15)
    gdf = ox.projection.project_gdf(gdf)
    print(f"  {len(gdf)} features")

    center = gpd.GeoSeries([Point(args.lon, args.lat)], crs=4326).to_crs(gdf.crs).iloc[0]
    x0, y1 = center.x - args.radius, center.y + args.radius
    size_m = 2 * args.radius

    def to_m(coords):
        """Projected coords -> meters from the map's top-left corner (y pointing down)."""
        return [(x - x0, y1 - y) for x, y, *_ in coords]

    rows = [({k: clean(v) for k, v in r.items() if k != "geometry"}, r.geometry) for _, r in gdf.iterrows()]
    campus_areas = [g for t, g in rows if t.get("amenity") in ("university", "college")
                    and isinstance(g, (Polygon, MultiPolygon))]
    campus_union = gpd.GeoSeries(campus_areas, crs=gdf.crs).union_all() if campus_areas else None

    shutil.rmtree(os.path.join(args.out, "levels"), ignore_errors=True)
    level_meta = []
    for mpp in levels:
        grid, rgb = render_level(rows, campus_union, to_m, size_m, mpp)
        key = f"{mpp:g}"
        ldir = os.path.join(args.out, "levels", key)
        os.makedirs(ldir)
        gimg = Image.fromarray(grid, "L")
        n = -(-grid.shape[1] // TILE)
        for ty in range(n):
            for tx in range(n):
                box = (tx * TILE, ty * TILE, min((tx + 1) * TILE, grid.shape[1]), min((ty + 1) * TILE, grid.shape[0]))
                rgb.crop(box).save(os.path.join(ldir, f"{tx}_{ty}.png"), optimize=True)
                gimg.crop(box).save(os.path.join(ldir, f"grid_{tx}_{ty}.png"), optimize=True)
        level_meta.append({"mpp": mpp, "size_px": grid.shape[1], "tiles": n, "dir": f"levels/{key}"})
        kb = sum(os.path.getsize(os.path.join(ldir, f)) for f in os.listdir(ldir)) // 1024
        print(f"  level {key} m/px: {grid.shape[1]}x{grid.shape[0]} px, {n}x{n} tiles, {kb} KB")

    def r1(pts):
        return [[round(x, 1), round(y, 1)] for x, y in pts]

    buildings = []
    for t, g in rows:
        if t.get("building") and t.get("name") and isinstance(g, (Polygon, MultiPolygon)) \
                and not skip_underground(t):
            buildings.append({
                "name": t["name"],
                "campus": bool(is_campus(t, g, campus_union)),
                "area_m2": round(g.area),
                "center": r1(to_m([g.representative_point().coords[0]]))[0],
                "polygons": [r1(to_m(p.exterior.coords)) for p in parts(g)],
            })
    buildings.sort(key=lambda b: -b["area_m2"])   # big buildings get label priority

    streets = {}
    for t, g in rows:
        hw, name = t.get("highway"), t.get("name")
        if not name or not hw or not isinstance(g, (LineString, MultiLineString)) or skip_underground(t):
            continue
        s = streets.setdefault(name, {"name": name, "rank": 3, "lines": []})
        s["rank"] = min(s["rank"], STREET_RANK.get(hw, 3))
        s["lines"] += [r1(to_m(ln.coords)) for ln in parts(g)]

    with open(os.path.join(args.out, "meta.json"), "w") as f:
        json.dump({
            "size_m": size_m, "center": {"lat": args.lat, "lon": args.lon},
            "tile_px": TILE, "levels": level_meta,
            "classes": [{"id": i, "name": n, "color": c} for i, (n, c, _) in enumerate(CLASSES)],
        }, f, indent=1)
    with open(os.path.join(args.out, "buildings.json"), "w") as f:
        json.dump(buildings, f)
    with open(os.path.join(args.out, "streets.json"), "w") as f:
        json.dump(sorted(streets.values(), key=lambda s: s["rank"]), f)

    print(f"Wrote {len(levels)} levels, {len(buildings)} named buildings, {len(streets)} named streets")


if __name__ == "__main__":
    main()
