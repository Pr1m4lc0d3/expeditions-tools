"""fetch_trails.py -- pull real trails from OpenStreetMap and lay them as Overlays.

The terrain already contains the trail as a physical bench, but it does not say
which bench is a route and which is a ledge. OSM does: Hell's Revenge, the
Escalator and the rest of the Sand Flats network are mapped as ways, by people
who drove them.

So the geometry comes from OSM and the height comes from the terrain, which is
the right division - an Overlay node is 2D and drapes on the ground anyway.

Queried through Overpass. Ways are clipped to the map, split where they leave and
re-enter, and written as one Overlay per way with Flatten on and ApplyOffset off,
because the defaults are backwards for a road you intend to drive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

from saber import project

PREBUILD = project.PREBUILD
OVERPASS = "https://overpass-api.de/api/interpreter"
_UA = {"User-Agent": "expeditions-toolkit/1.0 (map authoring)"}

# What counts as a drivable route in OSM's vocabulary. `track` is the unpaved
# 4x4 case; `path` catches routes mapped by people on foot or bike that a truck
# can still follow across slickrock.
QUERY = """[out:json][timeout:90];
(
  way["highway"~"^(track|unclassified|service|residential|tertiary|secondary|path|footway)$"]({s},{w},{n},{e});
  way["route"="road"]({s},{w},{n},{e});
);
out geom;
"""

OVERLAY = """<Overlay
\tType="{type}"
\tFlatten="true"
\tApplyOffset="false"
\tBrushes=""
\tId="{id}"
\tisInvisible="false"
>
\t<Curve>
{nodes}
\t</Curve>
</Overlay>
"""


MIRRORS = [
    OVERPASS,
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]


def overpass(s, w, n, e, tries=3):
    """Query OSM, moving to another mirror when one refuses.

    The main endpoint returns 504 under load often enough to have killed a whole
    map build twice, leaving a map with terrain and no roads and no way to tell
    from the files that a step had failed. Any of these mirrors answers the same
    query, so a busy one is a delay rather than a failure.
    """
    body = urllib.parse.urlencode(
        {"data": QUERY.format(s=s, w=w, n=n, e=e)}).encode()
    last = None
    for attempt in range(tries):
        for url in MIRRORS:
            try:
                req = urllib.request.Request(url, data=body, headers=_UA)
                with urllib.request.urlopen(req, timeout=180) as r:
                    d = json.load(r)
                # AN EMPTY ANSWER IS NOT AN ANSWER. A mirror returned 200 with
                # zero elements for an area with 14 km of mapped trail, and it
                # was accepted -- the map lost every road and the run still
                # reported success. Treat it as a failure and ask the next one.
                if not d.get("elements"):
                    print(f"  {urllib.parse.urlparse(url).netloc}: 200 but EMPTY")
                    last = RuntimeError("empty response")
                    continue
                if attempt or url != OVERPASS:
                    print(f"  (answered by {urllib.parse.urlparse(url).netloc})")
                return d
            except Exception as exc:                       # 504, 429, timeout
                last = exc
                print(f"  {urllib.parse.urlparse(url).netloc}: {exc}")
        if attempt < tries - 1:
            wait = 15 * (attempt + 1)
            print(f"  every mirror busy; waiting {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"no Overpass mirror answered after {tries} rounds: {last}")


def utm(lat, lon, lon0):
    """WGS84 lat/lon -> UTM easting/northing for the given central meridian."""
    a = 6378137.0
    f = 1 / 298.257223563
    k0 = 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    lr = math.radians(lat)
    dl = math.radians(lon - lon0)
    N = a / math.sqrt(1 - e2 * math.sin(lr) ** 2)
    T = math.tan(lr) ** 2
    C = ep2 * math.cos(lr) ** 2
    A = math.cos(lr) * dl
    M = a * ((1 - e2/4 - 3*e2**2/64 - 5*e2**3/256) * lr
             - (3*e2/8 + 3*e2**2/32 + 45*e2**3/1024) * math.sin(2*lr)
             + (15*e2**2/256 + 45*e2**3/1024) * math.sin(4*lr)
             - (35*e2**3/3072) * math.sin(6*lr))
    east = k0 * N * (A + (1 - T + C) * A**3 / 6
                     + (5 - 18*T + T*T + 72*C - 58*ep2) * A**5 / 120) + 500000
    north = k0 * (M + N * math.tan(lr) * (A*A/2 + (5 - T + 9*C + 4*C*C) * A**4 / 24
                  + (61 - 58*T + T*T + 600*C - 330*ep2) * A**6 / 720))
    return east, north


def central_meridian(lon):
    return (int((lon + 180.0) // 6) + 1) * 6 - 183


from saber.frame import to_map          # noqa: E402  (one shared transform)
# Trails and terrain MUST come from the same transform. They did not: this file
# carried its own copy, so the map's handedness could change without the roads
# following, and the roads ended up beside the ground they belonged on.


# What a surface is, what renders it, and how wide it runs. Overlay names are
# the real ones from [media]/classes/overlays; materials are the real ones from
# [media]/classes/terrain_layers, chosen by measured colour (see palette.py).
#
# There is no grey gravel OVERLAY in the game, only dirt and asphalt. A gravel
# road at Sand Flats is plainly grey against pink-red ground, and that grey has
# to come from the terrain material under the road.
#
# MEASURED, not assumed. Against the satellite basemap, a road here is only
# +6 luminance and -0.03 redness against the ground beside it: slightly LIGHTER
# and slightly less red, not dark grey. The first version used us_concrete_old
# (#4c4741) and gravel_pbr_test (#6c6b61) and painted near-black stripes across
# pink-red ground, which is the wrong DIRECTION, not merely the wrong shade.
#
# dry_lake_bottom (#bba088, redness +0.315) is lighter and less red than the
# sand_pbr_03 base (#927957, +0.503), which is the relationship the imagery
# shows. The dark greys are kept only for genuine asphalt, which at this place
# is the Slickrock trailhead lot and nothing else.
SURFACES = {
    # graded and gravel-tended: GREY, because someone hauled gravel in. It is
    # the only road surface here whose colour did not come out of the ground.
    "paved":  {"label": "paved",  "overlay": "asphalt_road_double_pbr",
               "material": "rock",  "width": 7.0},
    "gravel": {"label": "gravel", "overlay": "dirt_road_can",
               "material": "rock",  "width": 5.5},
    # primitive two-track: the native ground with the scrub scraped off it, and
    # therefore exactly the colour of the ground. Painting these grey was the
    # error -- most of the routes here are this, which is why measuring all the
    # roads together showed no colour difference from their surroundings at all.
    # dirt_road_can, NOT arizone_dirt_road_01. Both are dirt roads; the
    # difference is that dirt_road_can's texture (overlays/dirt_road_01_rus) is
    # STORED in editor.pak and therefore patchable in place, so the trail can be
    # recoloured to match the rock it crosses. The arizona one is deflated and
    # would need the whole 8 GB archive rewritten.
    "dirt":   {"label": "dirt",   "overlay": "dirt_road_can",
               "material": "base",  "width": 4.0},
}
PAVED_TAGS = {"asphalt", "paved", "concrete", "chipseal", "paving_stones"}
GRAVEL_TAGS = {"gravel", "fine_gravel", "compacted", "pebblestone"}


def surface_spec(tags: dict) -> dict:
    """Decide a way's surface from its tags, falling back on its class.

    Half the ways here carry no `surface` tag at all, so an untagged way is read
    from `highway`: a tertiary road above Moab is paved, a track is not.
    """
    s = (tags.get("surface") or "").lower()
    if s in PAVED_TAGS:
        return SURFACES["paved"]
    if s in GRAVEL_TAGS:
        return SURFACES["gravel"]
    if s in ("ground", "dirt", "earth", "sand", "unpaved", "rock"):
        return SURFACES["dirt"]
    hw = (tags.get("highway") or "").lower()
    if hw in ("tertiary", "secondary", "primary", "residential"):
        return SURFACES["paved"]
    if hw in ("service", "unclassified"):
        return SURFACES["gravel"]
    return SURFACES["dirt"]


def split_inside(pts, half):
    """Break a way into the runs that lie inside the map."""
    runs, cur = [], []
    for x, z in pts:
        if abs(x) <= half and abs(z) <= half:
            cur.append((x, z))
        elif cur:
            runs.append(cur); cur = []
    if cur:
        runs.append(cur)
    return runs


def resample(pts, step):
    p = np.array(pts, float)
    if len(p) < 2:
        return p
    seg = np.hypot(np.diff(p[:, 0]), np.diff(p[:, 1]))
    s = np.concatenate([[0.0], np.cumsum(seg)])
    if s[-1] < step:
        return p
    t = np.arange(0.0, s[-1], step)
    if t[-1] < s[-1]:
        t = np.append(t, s[-1])
    return np.column_stack([np.interp(t, s, p[:, 0]), np.interp(t, s, p[:, 1])])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level")
    ap.add_argument("lat", type=float, help="map centre")
    ap.add_argument("lon", type=float)
    ap.add_argument("--span", type=float, default=2016.0)
    ap.add_argument("--type", default="arizone_dirt_road_01",
                    help="ONE overlay type: only one textural overlay renders "
                         "per terrain block")
    ap.add_argument("--width", type=float, default=5.0)
    ap.add_argument("--spacing", type=float, default=18.0)
    ap.add_argument("--min-length", type=float, default=60.0)
    ap.add_argument("--kinds", default="track,unclassified,service,residential,tertiary,secondary",
                    help="OSM highway values to lay. Foot and bike paths are "
                         "excluded by default: they are real, but they are not "
                         "vehicle routes, and only ONE textural overlay renders "
                         "per terrain block so they cannot get their own type.")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    half = args.span / 2
    dlat = half / 111320.0
    dlon = half / (111320.0 * math.cos(math.radians(args.lat)))
    s, n = args.lat - dlat, args.lat + dlat
    w, e = args.lon - dlon, args.lon + dlon
    print(f"querying OSM for {args.span:.0f} m around {args.lat:.5f},{args.lon:.5f}")
    data = overpass(s, w, n, e)
    ways = [el for el in data.get("elements", []) if el.get("type") == "way"]
    print(f"  {len(ways)} ways returned")

    out_dir = PREBUILD / args.level / "data" / "Overlay"
    kept, total_m = [], 0.0
    for way in ways:
        geom = way.get("geometry") or []
        if len(geom) < 2:
            continue
        pts = [to_map(g["lat"], g["lon"], args.lat, args.lon) for g in geom]
        tags = way.get("tags", {})
        name = tags.get("name") or tags.get("highway") or "track"
        if tags.get("highway", "") not in args.kinds.split(","):
            continue
        spec = surface_spec(tags)
        for k, run in enumerate(split_inside(pts, half)):
            if len(run) < 2:
                continue
            p = resample(run, args.spacing)
            length = float(np.hypot(np.diff(p[:, 0]), np.diff(p[:, 1])).sum())
            if length < args.min_length:
                continue
            kept.append((f"{name}#{way['id']}.{k}", name, p, length,
                         tags.get("highway", ""), spec))
            total_m += length

    kept.sort(key=lambda r: -r[3])
    print(f"\n  {len(kept)} usable runs, {total_m/1000:.2f} km inside the map")
    print(f"  {'name':<30} {'kind':<11} {'surface':<9} {'length':>8} {'w':>4}")
    for ident, name, p, length, kind, spec in kept[:14]:
        print(f"  {name[:30]:<30} {kind:<11} {spec['label']:<9} "
              f"{length:7.0f}m {spec['width']:4.1f}")
    if len(kept) > 14:
        print(f"  ... and {len(kept)-14} more")

    by_kind = {}
    for *_, spec in kept:
        by_kind[spec["label"]] = by_kind.get(spec["label"], 0) + 1
    print("\n  overlay type per surface")
    for lab, cnt in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        sp = next(s for *_, s in kept if s["label"] == lab)
        print(f"    {lab:<9} {cnt:3d} runs  ->  {sp['overlay']:<26} "
              f"material {sp['material']}")
    print("  NOTE: no grey gravel OVERLAY exists in the game. The grey of a "
          "gravel road\n        comes from the MATERIAL painted under it; the "
          "overlay only adds texture.")

    # Only ONE textural overlay renders per terrain block, so two roads of
    # different surface crossing the same block is a conflict that shows up as a
    # missing road rather than an error.
    blocks = {}
    bs = args.span / 84.0
    for ident, name, p, length, kind, spec in kept:
        for x, z in p:
            key = (int((x + half) // bs), int((z + half) // bs))
            blocks.setdefault(key, set()).add(spec["overlay"])
    clash = [k for k, v in blocks.items() if len(v) > 1]
    if clash:
        print(f"\n  !! {len(clash)} of {len(blocks)} occupied blocks carry more "
              f"than one overlay type.")
        print("     Only one renders per block, so some road texture will be "
              "missing there.")
    else:
        print(f"\n  {len(blocks)} occupied blocks, none with conflicting "
              f"overlay types")

    if not args.apply:
        print("\n(dry run -- pass --apply)")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    for ident, name, p, length, kind, spec in kept:
        oid = hashlib.sha1(ident.encode()).hexdigest()[:12].upper()
        w = spec["width"]
        nodes = "\n".join(
            f'\t\t<Node Position="({x:.5f}; {z:.5f})" Width="{w:g}" '
            f'EraseSnow="true" />' for x, z in p)
        (out_dir / f"Overlay_{oid}.xml").write_text(
            OVERLAY.format(type=spec["overlay"], id=oid, nodes=nodes),
            encoding="utf8")

    # The material stripes are the half of a road the overlay cannot do, so they
    # are handed on rather than silently skipped.
    strips = PREBUILD / args.level / "road_materials.json"
    strips.write_text(json.dumps({
        "span": args.span,
        "roads": [{"name": name, "surface": spec["label"],
                   "material": spec["material"], "width": spec["width"],
                   "points": [[round(float(x), 2), round(float(z), 2)]
                              for x, z in p]}
                  for ident, name, p, length, kind, spec in kept],
    }, indent=1), encoding="utf8")
    print(f"\nwrote {len(kept)} overlays to {out_dir}")
    print(f"wrote {strips.name} -- the material stripes for paint_terrain.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
