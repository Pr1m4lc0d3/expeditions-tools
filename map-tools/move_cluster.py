"""move_cluster.py -- relocate a group of models as a rigid cluster, then re-seat them.

A campsite, homestead or wreck scene is composed: the fire ring, the tent and the
chairs mean something in relation to each other. Moving the pieces individually
destroys that. This translates every model in the named files by one offset, so
the arrangement survives, and only then drops each onto the new ground.

Sites are chosen against the terrain, not by eye. Nobody pitches a tent on a 25
degree slope or inside a river channel, and a fire ring on a sidehill reads as
broken the moment it is seen.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from saber.river import MEDIA
import push_heights as ph

# ⛔ DEFAULTS ONLY -- see the note in push_heights.py. Correct on exactly one
# of twelve maps. terrain() reads this level's real geometry instead, so a
# 512 m map is no longer evaluated at half its elevation.
SPAN = 2016.0
MAX_HEIGHT = 256.0
POSITION = re.compile(r'(Org="\()([-\d.eE]+)(;\s*)([-\d.eE]+)(;\s*)([-\d.eE]+)(\))')


def terrain(level):
    span, max_height = ph.level_geometry(level)
    H = ph.assemble(MEDIA / "prebuild" / level / "data").astype(np.float64) * max_height
    n = H.shape[0]
    mpp = span / (n - 1)
    gy, gx = np.gradient(H, mpp)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))

    def idx(x, z):
        # `span`, not the module default: the closure must carry THIS level's
        # geometry or every world-metre to pixel conversion is wrong off-centre.
        return (int(round(np.clip((z + span/2)/mpp, 0, n-1))),
                int(round(np.clip((x + span/2)/mpp, 0, n-1))))
    return H, slope, idx


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level")
    ap.add_argument("--file", action="append", required=True,
                    help="subgroup file to move; repeatable, all share one offset")
    ap.add_argument("--to", required=True, metavar="X,Z",
                    help="world coordinates for the cluster centroid")
    ap.add_argument("--reseat", action="store_true", default=True,
                    help="drop each model onto the new ground (default on)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    subs = MEDIA / "prebuild" / args.level / "subgroups"
    H, slope, idx = terrain(args.level)
    tx, tz = (float(v) for v in args.to.split(","))

    # centroid of everything being moved
    pts = []
    for fn in args.file:
        t = (subs / fn).read_text(encoding="utf8")
        for m in POSITION.finditer(t):
            pts.append((float(m.group(2)), float(m.group(6))))
    P = np.array(pts)
    cx, cz = P[:, 0].mean(), P[:, 1].mean()
    dx, dz = tx - cx, tz - cz
    print(f"{len(P)} models, centroid ({cx:.1f}, {cz:.1f}) -> ({tx:.1f}, {tz:.1f})")
    print(f"offset ({dx:+.1f}, {dz:+.1f}) m")

    print(f"\n{'model':<26} {'old slope':>10} {'new slope':>10} {'new ground':>11}")
    worst = 0.0
    for fn in args.file:
        path = subs / fn
        text = path.read_text(encoding="utf8")
        brands = re.findall(r'Brand="([^"]*)"', text)
        out_parts = []
        k = 0

        def rep(m):
            nonlocal k, worst
            x, y, z = float(m.group(2)), float(m.group(4)), float(m.group(6))
            r0, c0 = idx(x, z)
            s_old = slope[r0, c0]
            nx, nz = x + dx, z + dz
            r, c = idx(nx, nz)
            s_new = float(slope[r, c])
            g = float(H[r, c])
            ny = g if args.reseat else y + (g - H[r0, c0])
            worst = max(worst, s_new)
            name = brands[k] if k < len(brands) else "?"
            print(f"  {name[:26]:<26} {s_old:9.1f}° {s_new:9.1f}° {g:10.2f}")
            k += 1
            return (f"{m.group(1)}{nx:.5f}{m.group(3)}{ny:.5f}"
                    f"{m.group(5)}{nz:.5f}{m.group(7)}")

        new = POSITION.sub(rep, text)
        if args.apply:
            bdir = Path(__file__).parent / "backups" / args.level / "subgroups"
            bdir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, bdir / (path.name + ".pre-move-" +
                                       time.strftime("%Y%m%d-%H%M%S")))
            path.write_text(new, encoding="utf8")

    print(f"\nworst slope at the new site: {worst:.1f}°")
    if worst > 8.0:
        print("  !! still too steep for a camp - pick a flatter site")
    if not args.apply:
        print("(dry run -- pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
