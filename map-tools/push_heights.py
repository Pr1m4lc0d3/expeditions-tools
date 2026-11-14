"""push_heights.py -- move graded terrain from the master into the height tiles.

`_height_source.png` is the master the road grader writes. The EDITOR DOES NOT
BUILD FROM IT. It builds from `data/<col>_<row>/_height.dds`, sixteen R16F tiles,
which is why a map can be graded on disk and still drive like a washboard:

    built height_map vs the pre-grading master:  0.051 m mean difference
    built height_map vs the graded master:       0.241 m mean difference

Exactly the trap `sync_tiles.py` documents for distribution masks, one directory
over and in a different file format.

The push is deliberately NOT a copy. Tiles can hold hand sculpting done in the
editor after the master was written - on this map a pond rebuilt at 05:52 lives
in tile 3_2 alone - and overwriting them wholesale would destroy it. Only pixels
that are BOTH changed by the grader AND within the road corridor are written.
Everything else in every tile is left byte-identical.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from saber import dds
from saber.river import MEDIA

TILE = 1008
GRIDN = 4
MASTER = TILE * GRIDN + 1

# ⛔ DEFAULTS ONLY. Both were hard-coded and are correct on exactly ONE of the
# twelve maps on this machine (Ozark). Heights are stored normalised 0..1 and
# multiplied by the map's own MaxHeight, so on a 512 m map -- Shafer Trail,
# Diamondback Divide, Wolverine Shelf -- 256 reads the ground at HALF its true
# elevation, and on the 64 m maps at four times.
#
# It fails SILENTLY: with the ground wrong, `dy = y - ground` is wrong, so
# neither the buried nor the floating branch fires and the tool reports zero of
# everything. `reseat_models.py` reported "0 buried, 0 floating" across 16,589
# models on Shafer before this was found.
#
# main() overwrites both from the level descriptor. Never rely on the default.
SPAN = 2016.0
MAX_HEIGHT = 256.0


def level_geometry(level: str) -> tuple[float, float]:
    """(span_metres, max_height) from the level's OWN descriptor."""
    name = level[6:] if level.startswith("level_") else level
    xml = MEDIA / "prebuild" / f"level_{name}.xml"
    if not xml.is_file():
        # Refuse rather than fall back to the default. A wrong MaxHeight is
        # silent -- it reports zero buried and zero floating and looks clean.
        raise SystemExit(f"no level descriptor at {xml}. The level has not been "
                         f"built, or the name is wrong. Refusing to guess its "
                         f"height: a wrong MaxHeight fails silently.")
    t = xml.read_text(encoding="utf-8")
    mh = re.search(r'MaxHeight="([\d.]+)"', t)
    nb = re.search(r'NumBlocksX="(\d+)"', t)
    if not mh or not nb:
        raise SystemExit(f"{xml} has no MaxHeight/NumBlocksX")
    return int(nb.group(1)) * 24.0, float(mh.group(1))


def adopt_level_geometry(level: str) -> tuple[float, float]:
    """Point the module globals at this level's real geometry."""
    global SPAN, MAX_HEIGHT
    SPAN, MAX_HEIGHT = level_geometry(level)
    return SPAN, MAX_HEIGHT


def tile_path(data: Path, col: int, row: int) -> Path:
    return data / f"{col}_{row}" / "_height.dds"


def assemble(data: Path):
    """Stitch the 16 height tiles into one master -- the editor's actual terrain."""
    master = np.zeros((MASTER, MASTER), np.float32)
    for col in range(GRIDN):
        for row in range(GRIDN):
            p = tile_path(data, col, row)
            a = dds.read_r16f(p)
            h, w = a.shape
            master[row*TILE:row*TILE+h, col*TILE:col*TILE+w] = a
    return master


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level")
    ap.add_argument("--network", default=str(Path(__file__).parent / "network.npz"))
    ap.add_argument("--corridor", type=float, default=44.0,
                    help="metres; must cover running surface + shoulder + ditch + batter")
    ap.add_argument("--threshold", type=float, default=0.01,
                    help="metres; ignore differences smaller than this")
    ap.add_argument("--protect-water", action="store_true", default=True,
                    help="never write inside a river or pond channel (default on)")
    ap.add_argument("--no-protect-water", dest="protect_water", action="store_false")
    ap.add_argument("--water-margin", type=float, default=20.0,
                    help="metres of bank to protect beyond the channel edge")
    ap.add_argument("--running", type=float, default=12.0,
                    help="metres of running surface that must take the graded "
                         "height verbatim, with no blending")
    ap.add_argument("--feather", type=float, default=8.0,
                    help="metres over which the graded surface ramps into untouched "
                         "terrain; a hard edge across a road IS a ditch")
    ap.add_argument("--protect-circle", action="append", metavar="X,Z,RADIUS",
                    help="no-go circle in world metres; repeatable. Use for ground "
                         "reshaped by hand in the editor since the master was written.")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    span, max_height = adopt_level_geometry(args.level)
    print(f"  {args.level}: span {span:.0f} m, MaxHeight {max_height:.0f} m")

    pre = MEDIA / "prebuild" / args.level
    data = pre / "data"

    graded = dds.read_r16f(pre / "_height_source.png").astype(np.float64)
    tiles = assemble(data).astype(np.float64)
    print(f"graded master {graded.shape}, tiles assembled {tiles.shape}")

    diff = (graded - tiles) * MAX_HEIGHT
    changed = np.abs(diff) > args.threshold
    print(f"grader differs from the tiles on {changed.sum()} px "
          f"({100*changed.mean():.2f}% of the map), "
          f"max {np.abs(diff).max():.2f} m")

    import clear_roadway as cr
    corridor = cr.road_mask_grid(args.network, width_m=args.corridor)

    # Never regrade terrain that holds water. A road may run right past a pond --
    # on this map one does, and 98.9% of the pixels within 15 m of the pond centre
    # fell inside the corridor, differing by up to 4.25 m. Pushing there would have
    # bulldozed a basin that had been rebuilt by hand in the editor that morning.
    protect = np.zeros_like(corridor)
    if args.protect_water:
        from saber import river
        mpp = SPAN / (MASTER - 1)
        for g in river.load_map_rivers(args.level):
            for rc in g.reaches:
                for nd in rc.nodes:
                    rad_m = nd.width / 2.0 + args.water_margin
                    rad = int(np.ceil(rad_m / mpp))
                    r0 = int(round((nd.z + SPAN/2) / mpp))
                    c0 = int(round((nd.x + SPAN/2) / mpp))
                    rs, re = max(0, r0-rad), min(MASTER, r0+rad+1)
                    cs, ce = max(0, c0-rad), min(MASTER, c0+rad+1)
                    if rs >= re or cs >= ce:
                        continue
                    yy, xx = np.mgrid[rs-r0:re-r0, cs-c0:ce-c0]
                    protect[rs:re, cs:ce] |= (np.hypot(xx, yy) * mpp <= rad_m)
        print(f"water protection: {protect.sum()} px "
              f"(river and pond channels + {args.water_margin:.0f} m margin)")

    # An explicit no-go circle, for ground that was reshaped by hand in the editor.
    # A uniform water margin cannot do this job: widening it enough to cover the
    # ground around a pond also protects the full width of every river on the map,
    # which on a river-length map blocks most of the grading.
    mpp = SPAN / (MASTER - 1)
    for spec in args.protect_circle or []:
        cx, cz, rad_m = (float(v) for v in spec.split(","))
        rad = int(np.ceil(rad_m / mpp))
        r0 = int(round((cz + SPAN/2) / mpp))
        c0 = int(round((cx + SPAN/2) / mpp))
        rs, re = max(0, r0-rad), min(MASTER, r0+rad+1)
        cs, ce = max(0, c0-rad), min(MASTER, c0+rad+1)
        yy, xx = np.mgrid[rs-r0:re-r0, cs-c0:ce-c0]
        disc = np.hypot(xx, yy) * mpp <= rad_m
        protect[rs:re, cs:ce] |= disc
        print(f"protected circle at ({cx:.0f}, {cz:.0f}) r={rad_m:.0f} m: "
              f"{int((disc & changed[rs:re, cs:ce] & corridor[rs:re, cs:ce]).sum())} px "
              f"spared that the grader would have written")
        overlap = int((corridor & protect & changed).sum())
        print(f"   of which {overlap} px would otherwise have been overwritten")

    # Write the WHOLE corridor, not just the pixels that differ.
    #
    # Gating the write on `changed` looks conservative and is in fact the bug that
    # turned a washboard into a series of ditches. A graded road crosses the
    # original ground at every cut-to-fill transition, so the difference passes
    # through zero there; gating on it skipped a 1-4 m plug of ORIGINAL terrain at
    # each crossing, leaving a hole punched through an otherwise flat road every
    # few metres. `changed` is now only a report.
    allow = corridor & ~protect
    print(f"road corridor ({args.corridor:.0f} m wide): {corridor.sum()} px")
    print(f"writable after protection: {allow.sum()} px")

    # Feather ACROSS the road, never ALONG it.
    #
    # Weighting by distance to the edge of the writable region looks reasonable
    # and is wrong: wherever protection bites into the corridor the weight drops
    # ON THE CENTRELINE, so the smooth graded surface gets mixed with the rough
    # original right where the wheels go. Measured result - the centreline went
    # from 6.0 cm RMS in the graded master to 15.2 cm in the tiles, and one road
    # came out ROUGHER than before it was graded.
    #
    # Two independent smooth weights instead:
    #   across  - 1 out to the running surface, easing to 0 at the corridor edge,
    #             so the road itself always gets the graded height verbatim
    #   clear   - 1 outside protected ground, easing to 0 inside it, so water is
    #             approached on a ramp rather than a step
    from scipy.ndimage import distance_transform_edt
    mpp_m = SPAN / (MASTER - 1)

    def smoothstep(x):
        x = np.clip(x, 0.0, 1.0)
        return x * x * (3.0 - 2.0 * x)

    d_centre = distance_transform_edt(~cr.road_mask_grid(args.network, width_m=1.0)) * mpp_m
    run_half = args.running / 2.0
    corr_half = args.corridor / 2.0
    across = smoothstep((corr_half - d_centre) / max(corr_half - run_half, 1e-6))

    d_clear = distance_transform_edt(~protect) * mpp_m if protect.any() else np.full_like(across, 1e9)
    clear = smoothstep(d_clear / max(args.feather, 1e-6))

    w = across * clear
    w[~corridor] = 0.0
    on_road = d_centre <= run_half
    print(f"road surface (<= {run_half:.1f} m from centre): "
          f"{int((on_road & (w >= 0.999)).sum())} of {int(on_road.sum())} px "
          f"take the graded height verbatim")
    print(f"transition: {int(((w > 0) & (w < 0.999)).sum())} px")

    merged = tiles * (1.0 - w) + graded * w
    moved = np.abs(merged - tiles) * MAX_HEIGHT > args.threshold
    print(f"pixels actually moved: {int(moved.sum())}")
    outside = changed & ~corridor
    print(f"leaving alone: {outside.sum()} px of difference outside the corridor "
          f"(hand sculpting, pond work, anything the editor did since)")

    print("\n%-6s %-12s %10s %10s %10s" % ("tile", "shape", "px changed", "max m", "note"))
    plan = []
    for col in range(GRIDN):
        for row in range(GRIDN):
            p = tile_path(data, col, row)
            cur = dds.read_r16f(p)
            h, w = cur.shape
            crop = merged[row*TILE:row*TILE+h, col*TILE:col*TILE+w].astype(np.float32)
            d = np.abs(crop.astype(np.float64) - cur.astype(np.float64)) * MAX_HEIGHT
            n = int((d > args.threshold).sum())
            note = ""
            mt = time.localtime(p.stat().st_mtime)
            if time.strftime("%m-%d", mt) != "09-14":
                note = "edited " + time.strftime("%m-%d %H:%M", mt) + " - preserving"
            print("%-6s %-12s %10d %10.1f %s" % (f"{col}_{row}", f"{w}x{h}", n, d.max(), note))
            if n:
                plan.append((p, crop))

    if not args.apply:
        print(f"\n{len(plan)} tiles would be rewritten -- dry run, pass --apply")
        return 0

    bdir = Path(__file__).parent / "backups" / args.level / "height_tiles"
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for p, crop in plan:
        shutil.copy2(p, bdir / f"{p.parent.name}_{p.name}.pre-push-{stamp}")
        dds.write_r16f(p, crop)
    print(f"\nwrote {len(plan)} tiles; backups in {bdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
