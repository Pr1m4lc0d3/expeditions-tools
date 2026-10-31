"""Extract every placed object on a map, with the rules that governed placement.

The placements are plain XML under ``prebuild/level_NAME/subgroups/`` - one file
per authored group, named ``Model_*``, ``Plant_*``, ``River_*``, ``Zone_*`` and
``Distribution_*``. There is no need to decode the binary ``data.stg`` for this.

    <Plant Brand="arizona_methuselah_tree_01" NoSnow="false" FreezePhysics="false">
      <Position Org="(362.59; 264.92; 589.05)" Dir="(-0.736; 0; 0.677)" Scale="1.315" />
      <Placing Perpendicularity="0" Land="false" />
    </Plant>

``Org`` is X;Y;Z with **Y as altitude**, and it is in world metres, so it can be
compared directly against the level's MaxHeight.

The scale rule that falls out of this data matters more than the inventory: on
Diamondback Divide, rock a truck drives over sits at scale 1-4 with a ceiling
near 8, while scenery rock runs 8-65. Oversized drivable rock lets tires pass
through the collision skin and costs grip, because facet size grows past the
tire contact patch. Friction itself is a constant per model (FrictionPreset
"Stone" = 0.8) with no scale term, so the effect is geometric.
"""
from __future__ import annotations

import csv
import math
import re
import statistics as st
from collections import defaultdict
from pathlib import Path

ORG = re.compile(r'Org\s*=\s*"\(([^)]*)\)"')
SCALE = re.compile(r'Scale\s*=\s*"([-\d.eE]+)"')
BRAND = re.compile(r'Brand\s*=\s*"([^"]*)"')
UP = re.compile(r'Up\s*=\s*"\(([^)]*)\)"')
DIR = re.compile(r'Dir\s*=\s*"\(([^)]*)\)"')
SPLIT = re.compile(r"(?=<(?:Plant|Model|Object|Instance)\b)", re.I)

DRIVABLE_SCALE_CEILING = 8.0     # observed on Diamondback; typical drivable rock is 1-4

# A placement carries `Dir` (its heading) always, but `Up` only when the author
# tilted it off vertical. No `Up` means the object stands square to the world -
# for a rock, that is the flat-bottomed slab sitting level on a slope, which is
# the tell of dropped rather than composed rock. So the presence of `Up`, and
# how far it leans, is the measurable signature of handbuilt rockwork.
TILT_COMPOSED = 8.0   # degrees; below this a tilt reads as level to the eye


def _vec(m) -> tuple[float, float, float] | None:
    if not m:
        return None
    try:
        v = [float(t) for t in m.group(1).split(";")]
    except ValueError:
        return None
    return (v[0], v[1], v[2]) if len(v) == 3 else None


def extract(subgroups_dir: str | Path) -> list[dict]:
    """Parse every placement in a map's subgroups folder."""
    rows: list[dict] = []
    for f in sorted(Path(subgroups_dir).glob("*.xml")):
        kind, _, group = f.stem.partition("_")
        text = f.read_text(encoding="utf-8", errors="replace")
        for chunk in SPLIT.split(text)[1:]:
            b, o = BRAND.search(chunk), ORG.search(chunk)
            if not (b and o):
                continue
            try:
                x, y, z = (float(v) for v in o.group(1).split(";"))
            except ValueError:
                continue
            s = SCALE.search(chunk)
            up = _vec(UP.search(chunk))
            dr = _vec(DIR.search(chunk))
            # angle between the object's own up and world up, in degrees
            if up:
                n = math.sqrt(sum(c * c for c in up)) or 1.0
                tilt = math.degrees(math.acos(max(-1.0, min(1.0, up[1] / n))))
            else:
                tilt = 0.0
            rows.append({
                "file": f.name, "kind": kind, "group": group, "brand": b.group(1),
                "x": x, "y": y, "z": z,
                "scale": float(s.group(1)) if s else 1.0,
                "land": 'Land="true"' in chunk,
                "nosnow": 'NoSnow="true"' in chunk,
                "has_up": up is not None,
                "tilt_deg": tilt,
                "inverted": bool(up and up[1] < 0),
                "yaw_deg": (math.degrees(math.atan2(dr[0], dr[2])) % 360.0) if dr else None,
            })
    return rows


def composition(rows: list[dict], kind: str = "Model") -> dict:
    """Measure the rock-composition grammar of each authored group.

    What separates a built rock wall from scattered rock is not the asset list,
    it is the orientation: composed rock is tilted, inverted, interlocked and
    varied in scale, while dropped rock sits square with its flat face level.
    This reports, per group:

    ``tilted_pct``   share of placements carrying an explicit ``Up``
    ``mean_tilt``    average lean of those, in degrees
    ``inverted``     how many are turned past horizontal - rolled over entirely
    ``scale_ratio``  largest scale over smallest, a measure of size variety
    ``yaw_spread``   share of the 36 ten-degree heading buckets that are used;
                     rock rotated only on a few headings repeats visibly
    ``spacing_p50``  median nearest-neighbour distance, in metres
    """
    per = defaultdict(list)
    for r in rows:
        if r["kind"].lower() == kind.lower():
            per[r["group"]].append(r)

    out: dict[str, dict] = {}
    for group, rs in per.items():
        tilted = [r for r in rs if r["has_up"]]
        scales = [r["scale"] for r in rs if r["scale"] > 0]
        yaws = {int(r["yaw_deg"] // 10) for r in rs if r["yaw_deg"] is not None}

        # nearest-neighbour spacing, sampled so a 20k-object group stays cheap
        pts = [(r["x"], r["y"], r["z"]) for r in rs]
        sample = pts[:: max(1, len(pts) // 400)]
        dists = []
        for i, a in enumerate(sample):
            best = min((math.dist(a, b) for j, b in enumerate(sample) if j != i),
                       default=None)
            if best is not None:
                dists.append(best)

        out[group] = {
            "count": len(rs),
            "brands": len({r["brand"] for r in rs}),
            "tilted_pct": round(100 * len(tilted) / len(rs), 1),
            "mean_tilt": round(st.mean([r["tilt_deg"] for r in tilted]), 1) if tilted else 0.0,
            "max_tilt": round(max((r["tilt_deg"] for r in tilted), default=0.0), 1),
            "inverted": sum(1 for r in rs if r["inverted"]),
            "scale_min": round(min(scales), 2) if scales else 0.0,
            "scale_max": round(max(scales), 2) if scales else 0.0,
            "scale_ratio": round(max(scales) / min(scales), 1) if scales and min(scales) else 0.0,
            "yaw_spread": round(len(yaws) / 36, 2),
            "spacing_p50": round(st.median(dists), 1) if dists else 0.0,
        }
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["count"]))


#: Measured from the handbuilt rock on Diamondback Divide - the cathedral halls,
#: the gorge, cathedral pit and the terrace. These are the canyon walls that were
#: composed rock by rock, and the numbers are what separates them from the groups
#: that were merely populated (upper bedrock sits at 9% tilted, angel falls floor
#: at 12%). Use as the bar for new rockwork, not as a hard gate: level bedrock
#: strata SHOULD read as level, so a low tilt share is only wrong for a wall.
WALL_REFERENCE = {
    "tilted_pct": (73.0, 96.0),    # share carrying an explicit Up
    "mean_tilt": (38.0, 122.0),    # degrees of lean, averaged
    "inverted_pct": (11.0, 52.0),  # share rolled past horizontal
    "brands": (15, 31),            # distinct rock meshes in one wall
    "scale_ratio": (16.0, 115.0),  # largest over smallest
    "yaw_spread": (1.0, 1.0),      # every 10-degree heading bucket used
}


def grade_group(stats: dict) -> list[str]:
    """Compare one group's `composition()` stats against the canyon-wall bar.

    Returns the ways it falls short, worst first. An empty list means the group
    is as varied as the handbuilt reference. Intended for rockwork; scenery that
    is meant to lie flat will fail this on purpose.
    """
    notes: list[str] = []
    inv_pct = 100 * stats["inverted"] / stats["count"] if stats["count"] else 0.0
    checks = [
        ("tilted_pct", stats["tilted_pct"], "only %.1f%% of rocks are tilted at all (wall bar %.0f%%+)"),
        ("mean_tilt", stats["mean_tilt"], "mean lean is only %.1f degrees (wall bar %.0f+)"),
        ("inverted_pct", inv_pct, "only %.1f%% rolled past horizontal (wall bar %.0f%%+)"),
        ("brands", stats["brands"], "only %d distinct rock meshes (wall bar %d+)"),
        ("scale_ratio", stats["scale_ratio"], "size range only %.1fx (wall bar %.0fx+)"),
        ("yaw_spread", stats["yaw_spread"], "only %.2f of headings used (wall bar %.2f)"),
    ]
    for key, value, msg in checks:
        low = WALL_REFERENCE[key][0]
        if value < low:
            notes.append(msg % (value, low))
    return notes


def to_csv(rows: list[dict], path: str | Path) -> Path:
    p = Path(path)
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return p


def summarise(rows: list[dict]) -> dict:
    """Counts, altitude bands and scale spread per asset - the placement grammar."""
    per = defaultdict(list)
    for r in rows:
        per[r["brand"]].append(r)
    assets = {}
    for brand, rs in per.items():
        ys = sorted(r["y"] for r in rs)
        ss = sorted(r["scale"] for r in rs)
        assets[brand] = {
            "count": len(rs),
            "y_min": ys[0], "y_med": st.median(ys), "y_max": ys[-1],
            "scale_min": ss[0], "scale_med": st.median(ss), "scale_max": ss[-1],
            "land_pct": 100 * sum(r["land"] for r in rs) / len(rs),
        }
    kinds = defaultdict(int)
    for r in rows:
        kinds[r["kind"]] += 1
    return {"total": len(rows), "distinct_assets": len(assets),
            "groups": len({r["group"] for r in rows}),
            "by_kind": dict(kinds), "assets": assets}


def flag_oversized_drivable(rows: list[dict], drivable_groups: list[str],
                            ceiling: float = DRIVABLE_SCALE_CEILING) -> list[dict]:
    """Find rock on a driving surface scaled past the point tires pass through it.

    ``drivable_groups`` is supplied rather than guessed: whether a rock is driven
    over is a design fact, not something the placement data records. Group names
    are a strong hint (floors, creek beds, cave systems are driven; gorge walls,
    bedrock and cap are not) but only the author knows for certain.
    """
    want = {g.lower() for g in drivable_groups}
    return [r for r in rows
            if r["group"].lower() in want and r["scale"] > ceiling]
