"""reseat_models.py -- put hand-placed models back on the ground after terrain moves.

A `<Model>` stores an absolute world position:

    <Position Org="(x; y; z)" Up="(...)" Dir="(...)" Scale="1.0" />

Nothing re-evaluates that Y when the terrain under it changes. Reshape the ground
and every model in the area is left hanging in the air or swallowed by the hill,
which is exactly what happens when a pond is rebuilt by hand or a road is graded
through a site.

Ground truth is the assembled height TILES, not `_height_source.png`: the tiles
are what the editor builds from, so they are what the model will actually be
standing on.

`Up` and `Dir` are left alone. They are the model's orientation basis and a model
that was deliberately tilted to match a slope should stay tilted; only its height
is being corrected.
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

# ⛔ THESE WERE HARD-CODED AND WRONG ON 11 OF 12 MAPS.
#
# `MAX_HEIGHT = 256.0` matched exactly one map on this machine, Ozark, which
# is presumably what it was written against. Measured across the prebuild
# folder:
#
#     512 m  Diamondback Divide, Diamondback Divide 2, Shafer Trail,
#            Shafer Trail Monsoon, Wolverine Shelf
#     256 m  Ozark Hollow                              <- the only match
#     244 m  Hell's Revenge
#     200 m  AI Test 01
#     184 m  Lessons
#      64 m  Loot Test, Model Test, Objectives
#
# Heights are stored normalised 0..1, so the ground was being read at HALF its
# true elevation on Shafer and at FOUR TIMES on the 64 m maps. With the ground
# wrong, `dy = y - ground` is wrong, so the buried/floating test silently
# reports nothing (0 buried, 0 floating, worst 0.00 m across 16,589 models)
# and `--fix-floating` would have SNAPPED EVERY MODEL to the wrong height.
#
# `SPAN` was wrong too: NumBlocksX * 24 is 2016 only for the 84-block maps.
# The small maps are 432, 816 and 96 metres.
#
# Both now come from the level descriptor, like the other 36 tools already do.
def level_geometry(level: str):
    """(span_metres, max_height) from the level's own XML."""
    name = level[6:] if level.startswith("level_") else level
    xml = MEDIA / "prebuild" / f"level_{name}.xml"
    t = xml.read_text(encoding="utf-8")
    mh = re.search(r'MaxHeight="([\d.]+)"', t)
    nb = re.search(r'NumBlocksX="(\d+)"', t)
    if not mh or not nb:
        raise SystemExit(f"{xml} has no MaxHeight/NumBlocksX")
    return int(nb.group(1)) * 24.0, float(mh.group(1))

POSITION = re.compile(r'(<Position\s+Org="\()([-\d.eE]+)(;\s*)([-\d.eE]+)(;\s*)([-\d.eE]+)(\))')


def ground_sampler(level: str):
    """Bilinear terrain height in metres at any world (x, z)."""
    span, max_height = level_geometry(level)
    tiles = ph.assemble(MEDIA / "prebuild" / level / "data").astype(np.float64) * max_height
    n = tiles.shape[0]
    mpp = span / (n - 1)

    def at(x, z):
        c = (np.asarray(x) + span / 2) / mpp
        r = (np.asarray(z) + span / 2) / mpp
        c = np.clip(c, 0, n - 1.001)
        r = np.clip(r, 0, n - 1.001)
        r0, c0 = np.floor(r).astype(int), np.floor(c).astype(int)
        fr, fc = r - r0, c - c0
        return ((tiles[r0, c0] * (1 - fr) * (1 - fc) + tiles[r0 + 1, c0] * fr * (1 - fc)
                 + tiles[r0, c0 + 1] * (1 - fr) * fc + tiles[r0 + 1, c0 + 1] * fr * fc))
    return at


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level")
    ap.add_argument("--buried", type=float, default=0.25,
                    help="metres below ground before a model counts as buried")
    ap.add_argument("--floating", type=float, default=1.50,
                    help="metres above ground before a model counts as floating; "
                         "generous, because ledge and canopy models are placed high "
                         "on purpose")
    ap.add_argument("--fix-floating", action="store_true",
                    help="also drop models that hang in the air (off by default: "
                         "cliff ledges and canopy props are SUPPOSED to be up there)")
    ap.add_argument("--only", action="append",
                    help="limit to these subgroup files, e.g. Model_wrecks.xml")
    ap.add_argument("--near", metavar="X,Z,RADIUS",
                    help="limit to models within a circle, in world metres")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    subs = MEDIA / "prebuild" / args.level / "subgroups"
    at = ground_sampler(args.level)
    circle = None
    if args.near:
        cx, cz, rad = (float(v) for v in args.near.split(","))
        circle = (cx, cz, rad)

    total_b = total_f = 0
    print("%-30s %7s %8s %8s %9s" % ("file", "models", "buried", "floating", "worst"))
    for path in sorted(subs.glob("Model_*.xml")):
        if args.only and path.name not in args.only:
            continue
        text = path.read_text(encoding="utf8")
        buried = floating = 0
        worst = 0.0

        def fix(m):
            nonlocal buried, floating, worst
            x, y, z = float(m.group(2)), float(m.group(4)), float(m.group(6))
            if circle:
                cx, cz, rad = circle
                if (x - cx) ** 2 + (z - cz) ** 2 > rad * rad:
                    return m.group(0)
            g = float(at(x, z))
            dy = y - g
            if dy < -args.buried:
                buried += 1
                worst = max(worst, -dy)
                y = g
            elif args.fix_floating and dy > args.floating:
                floating += 1
                worst = max(worst, dy)
                y = g
            else:
                return m.group(0)
            return (f"{m.group(1)}{m.group(2)}{m.group(3)}{y:.5f}"
                    f"{m.group(5)}{m.group(6)}{m.group(7)}")

        out, n = POSITION.subn(fix, text)
        print("%-30s %7d %8d %8d %8.2f m" % (path.name[:30], n, buried, floating, worst))
        total_b += buried
        total_f += floating
        if (buried or floating) and args.apply:
            bdir = Path(__file__).parent / "backups" / args.level / "subgroups"
            bdir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, bdir / (path.name + ".pre-reseat-" +
                                       time.strftime("%Y%m%d-%H%M%S")))
            path.write_text(out, encoding="utf8")

    print(f"\n{total_b} buried, {total_f} floating")
    if not args.apply:
        print("(dry run -- pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
