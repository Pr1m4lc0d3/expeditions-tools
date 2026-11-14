"""sculpt_roads.py -- run the validated stroke sequence over a whole road network.

The sequence is the one measured against his hand-made stroke, not invented:

    1. flatten to a smoothed centreline   builds the bench: level across the
                                          corridor at the road's own height,
                                          following the hill down
    2. smooth, small and hard, cut only   knocks the tops off the washboard, which
                                          is what the editor's Smooth brush was
                                          measured doing - 9174 px lowered, 0 raised
    3. smooth, wider and soft             eases the shoulder back into the hill so
                                          the bench has no step at its edge

Measured on north haul: 26.0 -> 9.5 cm RMS for 1.67 m of cut and 1.71 m of fill.
The one-shot grader this replaces needed 30 m of earthworks and still finished
rougher than untouched ground.

Water is protected the same way the height push protects it: never reshape a
channel or its banks, and take explicit no-go circles for ground that was
sculpted by hand in the editor.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from saber import dds, river, sculpt
from saber.river import MEDIA
import push_heights as ph

TILE, GRIDN = ph.TILE, ph.GRIDN
MASTER, SPAN, MAX_HEIGHT = ph.MASTER, ph.SPAN, ph.MAX_HEIGHT


def protection(level, water_margin, circles, mpp):
    prot = np.zeros((MASTER, MASTER), bool)
    for g in river.load_map_rivers(level):
        for rc in g.reaches:
            for nd in rc.nodes:
                rad_m = nd.width / 2 + water_margin
                rad = int(np.ceil(rad_m / mpp))
                r0 = int(round((nd.z + SPAN/2) / mpp))
                c0 = int(round((nd.x + SPAN/2) / mpp))
                rs, re = max(0, r0-rad), min(MASTER, r0+rad+1)
                cs, ce = max(0, c0-rad), min(MASTER, c0+rad+1)
                if rs >= re or cs >= ce:
                    continue
                yy, xx = np.mgrid[rs-r0:re-r0, cs-c0:ce-c0]
                prot[rs:re, cs:ce] |= np.hypot(xx, yy) * mpp <= rad_m
    for spec in circles or []:
        cx, cz, rad_m = (float(v) for v in spec.split(","))
        rad = int(np.ceil(rad_m / mpp))
        r0 = int(round((cz + SPAN/2) / mpp))
        c0 = int(round((cx + SPAN/2) / mpp))
        rs, re = max(0, r0-rad), min(MASTER, r0+rad+1)
        cs, ce = max(0, c0-rad), min(MASTER, c0+rad+1)
        yy, xx = np.mgrid[rs-r0:re-r0, cs-c0:ce-c0]
        prot[rs:re, cs:ce] |= np.hypot(xx, yy) * mpp <= rad_m
    return prot


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level")
    ap.add_argument("--network", default=str(Path(__file__).parent / "network.npz"))
    ap.add_argument("--grade-window", type=float, default=40.0,
                    help="metres of longitudinal smoothing on the bench profile")
    ap.add_argument("--water-margin", type=float, default=20.0)
    ap.add_argument("--protect-circle", action="append", metavar="X,Z,RADIUS")
    ap.add_argument("--max-move", type=float, default=3.0,
                    help="metres; abort if any road needs more earthworks than this")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    pre = MEDIA / "prebuild" / args.level
    data = pre / "data"
    H0 = ph.assemble(data).astype(np.float64) * MAX_HEIGHT
    mpp = SPAN / (MASTER - 1)
    prot = protection(args.level, args.water_margin, args.protect_circle, mpp)
    print(f"water + hand-sculpted protection: {int(prot.sum())} px")

    s = sculpt.Sculpt(H0, mpp)
    net = np.load(args.network)
    roads = sorted(k[:-2] for k in net.files if k.endswith('_x'))
    paths = {}
    for k in roads:
        X = net[k + '_x'].astype(float) * mpp - SPAN/2
        Z = net[k + '_y'].astype(float) * mpp - SPAN/2
        paths[k] = list(zip(X, Z))

    before = {k: s.roughness(paths[k]) for k in roads}

    for k in roads:
        p = paths[k]
        bench = s.centreline_field(p, smooth_m=args.grade_window)
        # protection is applied by intersecting every stroke mask with ~prot
        for brush, kw in (
            (sculpt.Brush('flatten', 5.0, 0.0, 0.30),
             dict(width_m=10.0, passes=4, target_field=bench)),
            (sculpt.Brush('smooth', 1.0, 0.0, 1.00),
             dict(width_m=14.0, passes=10, lower_only=True)),
            (sculpt.Brush('smooth', 3.0, 0.8, 0.40),
             dict(width_m=26.0, passes=3)),
        ):
            mask = s.path_mask(p, kw.pop('width_m')) & ~prot
            s.apply(mask, brush, passes=kw.pop('passes'),
                    target_field=kw.pop('target_field', None),
                    lower_only=kw.pop('lower_only', False), kind=f"{k}")

    d = s.h - H0
    print(f"\n{'road':<16} {'before':>10} {'after':>10} {'removed':>9}")
    for k in roads:
        a, b = before[k], s.roughness(paths[k])
        print(f"{k:<16} {a:9.1f}cm {b:9.1f}cm {100*(1-b/max(a,1e-9)):8.0f}%")
    print(f"\nearthworks: cut {-d.min():.2f} m, fill {d.max():.2f} m "
          f"({int((np.abs(d) > 0.005).sum())} px)")

    if max(-d.min(), d.max()) > args.max_move:
        print(f"!! exceeds --max-move {args.max_move} m -- refusing to write")
        return 1
    if not args.apply:
        print("\n(dry run -- pass --apply)")
        return 0

    bdir = Path(__file__).parent / "backups" / args.level / "height_tiles"
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    n = 0
    for col in range(GRIDN):
        for row in range(GRIDN):
            p = ph.tile_path(data, col, row)
            cur = dds.read_r16f(p)
            h, w = cur.shape
            crop = (s.h[row*TILE:row*TILE+h, col*TILE:col*TILE+w] / MAX_HEIGHT).astype(np.float32)
            if np.array_equal(crop, cur):
                continue
            shutil.copy2(p, bdir / f"{col}_{row}__height.dds.pre-sculpt-{stamp}")
            dds.write_r16f(p, crop)
            n += 1
    print(f"\nwrote {n} tiles; backups in {bdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
