"""smooth_roadway.py -- the editor's Smooth brush, applied along a whole network.

Reverse-engineered from one hand-made stroke rather than invented. He smoothed
203 m of `north haul` with the Geometry > Smooth brush and saved; diffing that
against a snapshot taken seconds earlier gives the spec:

    RMS about a 30 m trend   27.3 -> 17.6 cm   (36% removed)
    worst 0.5 m step         62.5 -> 37.5 cm   (40% removed)
    pixels lowered 9174, pixels raised 0
    attenuation peaks at 2.4-2.9 m wavelength (kept 0.40)
    4.7 m and longer survive intact; 1.1-1.8 m largely survive

Two things that spec tells us, neither of which was guessable:

* The brush is SMALL. Strongest attenuation near a 2.5 m wavelength means a
  kernel about 2.5-3 m across - radius ~1.5 m. A wide brush would flatten the
  terrain's shape along with the bumps; this removes a narrow band and leaves
  everything longer than ~5 m untouched.

* It only ever cut. Not because the filter is asymmetric, but because a circular
  kernel on a cross-sloped road has its local mean below the crown: the downhill
  half of the disc pulls the average down. Smoothing a sidehill benches the road
  INTO the hill, which is what a road actually is.

The 12.5 cm residual is the R16F height quantisation and no filter can go below
it - see notes on the heightmap format.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from saber import dds
from saber.river import MEDIA
import push_heights as ph

TILE, GRIDN = ph.TILE, ph.GRIDN
MASTER, SPAN, MAX_HEIGHT = ph.MASTER, ph.SPAN, ph.MAX_HEIGHT


def brush(radius_px, falloff=0.0):
    """The editor's brush profile: Size and Falloff.

    Falloff 0 is a hard brush - a flat top with vertical sides, which is what
    builds walls, benches and steps. Falloff 1 is soft - a rounded dome, which
    makes bumps. Every brush in the editor carries this control, and the Smooth
    brush specifically is documented as wanting Falloff near 0.

    Using a soft brush where a hard one belongs is how a road surface ends up as
    a row of domes instead of a bench.
    """
    r = int(np.ceil(radius_px))
    yy, xx = np.mgrid[-r:r+1, -r:r+1]
    d = np.hypot(xx, yy) / max(radius_px, 1e-9)
    inside = d <= 1.0
    if falloff <= 0:
        k = inside.astype(np.float64)
    else:
        # falloff eases the rim inwards; 1.0 reaches a full dome
        edge = np.clip((1.0 - d) / max(falloff, 1e-9), 0.0, 1.0)
        k = np.where(inside, edge * edge * (3 - 2 * edge), 0.0)
    return k / k.sum()


def smooth(height, mask, radius_m, passes, mpp,
           strength=1.0, falloff=0.0, lower_only=True):
    """The Geometry > Smooth brush, applied along a corridor.

    `strength` is the brush's Value: how much of the smoothed result replaces the
    original on each pass. Strength and passes together are what he meant by the
    brush "doing a more thorough job" - one light pass barely touches the surface,
    twenty firm ones level it.

    `lower_only` reproduces the measured behaviour and his description of it:
    the brush flattens the tops of the washboard down onto the low ground rather
    than filling the troughs up. Every one of the 9174 pixels in his stroke was
    lowered and none were raised.
    """
    from scipy.ndimage import convolve
    k = brush(radius_m / mpp, falloff)
    out = height.copy()
    for _ in range(passes):
        sm = convolve(out, k, mode='nearest')
        cand = out + strength * (sm - out)
        cand = np.where(mask, cand, out)
        if lower_only:
            cand = np.minimum(cand, out)
        out = cand
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level")
    ap.add_argument("--network", default=str(Path(__file__).parent / "network.npz"))
    ap.add_argument("--radius", type=float, default=1.0,
                    help="brush Size in metres; fitted to his stroke at 1.0")
    ap.add_argument("--passes", type=int, default=20)
    ap.add_argument("--strength", type=float, default=1.0,
                    help="brush Value, 0..1: how much each pass commits")
    ap.add_argument("--falloff", type=float, default=0.0,
                    help="0 = hard (flat top, vertical sides); 1 = soft dome. "
                         "The docs recommend near 0 for Smooth.")
    ap.add_argument("--width", type=float, default=14.0,
                    help="corridor to smooth, metres")
    ap.add_argument("--allow-fill", action="store_true",
                    help="let the filter raise ground as well as cut it")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    pre = MEDIA / "prebuild" / args.level
    data = pre / "data"
    H = ph.assemble(data).astype(np.float64) * MAX_HEIGHT
    mpp = SPAN / (MASTER - 1)

    import clear_roadway as cr
    mask = cr.road_mask_grid(args.network, width_m=args.width)
    print(f"corridor {args.width:.0f} m wide: {mask.sum()} px")
    print(f"brush: size r={args.radius:.2f} m, falloff {args.falloff:.2f}, "
          f"strength {args.strength:.2f}, {args.passes} passes, "
          f"{'cut and fill' if args.allow_fill else 'cut only'}")

    S = smooth(H, mask, args.radius, args.passes, mpp,
               strength=args.strength, falloff=args.falloff,
               lower_only=not args.allow_fill)
    d = S - H
    ch = np.abs(d) > 0.005
    print(f"\nchanged {int(ch.sum())} px; lowered {int((d < -0.005).sum())}, "
          f"raised {int((d > 0.005).sum())}")
    print(f"mean cut {-d[ch].mean():.3f} m, max cut {-d.min():.3f} m")

    net = np.load(args.network)
    print(f"\n{'road':<16} {'RMS before':>12} {'RMS after':>12} {'removed':>9} "
          f"{'worst step':>12}")
    for k in sorted(kk[:-2] for kk in net.files if kk.endswith('_x')):
        X = net[k + '_x'].astype(float); Y = net[k + '_y'].astype(float)
        seg = np.hypot(np.diff(X), np.diff(Y))
        s = np.concatenate([[0], np.cumsum(seg)])
        if s[-1] < 50:
            continue
        t = np.arange(0, s[-1], 1.0)
        px = np.round(np.interp(t, s, X)).astype(int).clip(0, MASTER-1)
        py = np.round(np.interp(t, s, Y)).astype(int).clip(0, MASTER-1)

        def stat(h):
            v = h[py, px]
            w = max(3, int(30.0 / mpp) | 1)
            tr = np.convolve(np.pad(v, w//2, mode='edge'), np.ones(w)/w,
                             mode='valid')[:len(v)]
            return (v - tr).std()*100, np.abs(np.diff(v)).max()*100
        a, am = stat(H); b, bm = stat(S)
        print(f"{k:<16} {a:11.1f}cm {b:11.1f}cm {100*(1-b/max(a,1e-9)):8.0f}% "
              f"{am:6.1f}->{bm:.1f}cm")

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
            crop = (S[row*TILE:row*TILE+h, col*TILE:col*TILE+w] / MAX_HEIGHT).astype(np.float32)
            if np.array_equal(crop, cur):
                continue
            shutil.copy2(p, bdir / f"{col}_{row}__height.dds.pre-smooth-{stamp}")
            dds.write_r16f(p, crop)
            n += 1
    print(f"\nwrote {n} tiles; backups in {bdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
