"""road_profile.py -- measure the humps a driver actually feels, along the road.

He drove Shafer and found "several areas of the road were very rough and
uneven, like there were large humps added to the road". This measures them so
the fix can be checked rather than admired.

WHY THE OBVIOUS TOOLS DO NOT APPLY, from the official pages:

  Flatten      "flattens road bumps ALONG ITS WIDTH (from one roadside to
               another one). Along its length, the road may remain uneven."
               It also says "the heightmap itself will remain unchanged".
               So Flatten fixes cross-slope and nothing else.
  ApplyOffset  adds ruts and bumps deliberately. Already `false` on all 17
               Shafer overlays, so it is not the source here.

That leaves the terrain's own LONGITUDINAL profile under the centreline. And
flattening the cross-section makes it WORSE to drive, because once the camber
is gone the only thing left varying is the length profile.

⛔ `smooth_roadway.py` is the wrong instrument for this. It reproduces the
editor's Smooth brush, measured at radius ~1.5 m with attenuation peaking at a
2.4-2.9 m wavelength and everything beyond 4.7 m surviving intact. A "large
hump" is tens of metres long. The brush is designed not to touch it.

WHAT IS MEASURED

  grade residual   terrain height minus a robust trend over `--window` metres.
                   This is the hump itself: a 40 m window keeps the real
                   descent of a switchback road and exposes what rides on top.
  hump             a run of consecutive stations whose residual stays above
                   `--threshold`. Reported with its length, peak and position,
                   so a fix can be aimed.
  grade break      change in gradient between consecutive 5 m chords, in
                   percentage points. This is what pitches a truck; a smooth
                   road can still drive badly if the grade steps.

⛔ The R16F floor. Heights are stored as 16-bit floats over the map's
MaxHeight, so the smallest representable step is MaxHeight/2048 -- 25 cm on
this 512 m map. No filter can produce a residual below that, and a "perfectly
clean" road will still read ~0.12 m RMS. Chasing zero is chasing quantisation.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from saber import dds, project  # noqa: E402

try:
    from defusedxml import ElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

NODE = re.compile(r'<Node Position="\(([-\d.eE]+); ([-\d.eE]+)\)"')


def stitch(lvl: Path, grid: int = 4):
    tiles = {}
    for p in (lvl / "data").iterdir():
        if p.is_dir() and (p / "_height.dds").exists():
            col, row = (int(c) for c in p.name.split("_"))
            tiles[(col, row)] = dds.read_r16f(p / "_height.dds")
    tp = min(min(a.shape) for a in tiles.values())
    n = grid * tp + 1
    m = np.zeros((n, n), np.float32)
    for (col, row), a in tiles.items():
        m[row*tp:row*tp+a.shape[0], col*tp:col*tp+a.shape[1]] = a
    return m


def densify(pts, step):
    """Walk the polyline at a fixed spacing; returns (x, z, chainage)."""
    out = []
    s = 0.0
    for (x1, z1), (x2, z2) in zip(pts, pts[1:]):
        d = math.hypot(x2 - x1, z2 - z1)
        if d < 1e-9:
            continue
        k = max(1, int(round(d / step)))
        for i in range(k):
            f = i / k
            out.append((x1 + (x2 - x1) * f, z1 + (z2 - z1) * f, s + d * f))
        s += d
    out.append((pts[-1][0], pts[-1][1], s))
    return np.array(out, np.float64)


def robust_trend(y, win_pts):
    """Centred moving median then mean -- a median first so a single tall hump
    does not drag the trend up to meet it, which a plain mean does."""
    n = len(y)
    half = max(1, win_pts // 2)
    med = np.empty(n)
    for i in range(n):
        a, b = max(0, i - half), min(n, i + half + 1)
        med[i] = np.median(y[a:b])
    k = max(1, win_pts // 3)
    ker = np.ones(k) / k
    return np.convolve(np.pad(med, (k, k), mode="edge"), ker, "same")[k:-k]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    ap.add_argument("--step", type=float, default=2.0, help="sampling interval, m")
    ap.add_argument("--window", type=float, default=40.0,
                    help="trend window; longer keeps more of the real descent")
    ap.add_argument("--threshold", type=float, default=0.30,
                    help="residual above which a station counts as humped, m")
    ap.add_argument("--json", default=None, help="write the hump list here")
    a = ap.parse_args()

    lvl = project.PREBUILD / f"level_{a.name}"
    root = ET.fromstring((project.PREBUILD / f"level_{a.name}.xml").read_text(encoding="utf-8"))
    mh = float(root.get("MaxHeight"))
    span = int(root.get("NumBlocksX")) * 24.0
    z = stitch(lvl) * mh
    n = z.shape[0]
    mpp = span / (n - 1)
    half = span / 2
    quant = mh / 2048.0

    def zq(x, zz):
        c = (x + half) / mpp
        r = (zz + half) / mpp
        c0, r0 = int(np.clip(c, 0, n - 2)), int(np.clip(r, 0, n - 2))
        fx, fy = c - c0, r - r0
        return float((1-fy)*((1-fx)*z[r0, c0] + fx*z[r0, c0+1]) +
                     fy*((1-fx)*z[r0+1, c0] + fx*z[r0+1, c0+1]))

    files = sorted((lvl / "data" / "Overlay").glob("*.xml"))
    win_pts = max(3, int(a.window / a.step))
    rows = []
    all_res = []
    humps = []
    for f in files:
        txt = f.read_text(encoding="utf-8", errors="replace")
        pts = [(float(x), float(y)) for x, y in NODE.findall(txt)]
        if len(pts) < 3:
            continue
        P = densify(pts, a.step)
        if len(P) < win_pts + 2:
            continue
        y = np.array([zq(px, pz) for px, pz, _ in P])
        trend = robust_trend(y, win_pts)
        res = y - trend
        all_res.append(res)

        # grade breaks: change of gradient between consecutive 5 m chords
        chord = max(2, int(5.0 / a.step))
        g = (y[chord:] - y[:-chord]) / (P[chord:, 2] - P[:-chord, 2] + 1e-9) * 100.0
        gb = np.abs(np.diff(g)) if len(g) > 1 else np.array([0.0])

        # contiguous runs above threshold
        over = res > a.threshold
        i = 0
        local = 0
        while i < len(over):
            if over[i]:
                j = i
                while j + 1 < len(over) and over[j + 1]:
                    j += 1
                k = int(np.argmax(res[i:j+1])) + i
                length = P[j, 2] - P[i, 2] + a.step
                humps.append(dict(overlay=f.stem, peak_m=round(float(res[k]), 2),
                                  length_m=round(float(length), 1),
                                  x=round(float(P[k, 0]), 1), z=round(float(P[k, 1]), 1),
                                  chainage_m=round(float(P[k, 2]), 1)))
                local += 1
                i = j + 1
            else:
                i += 1
        rows.append((f.stem, P[-1, 2], float(np.sqrt((res**2).mean())),
                     float(res.max()), float(res.min()), local,
                     float(np.percentile(gb, 95)) if len(gb) else 0.0))

    rows.sort(key=lambda r: -r[3])
    R = np.concatenate(all_res) if all_res else np.zeros(1)
    print(f"{a.name}: {len(rows)} overlays, {mpp:.2f} m/px, "
          f"R16F step {quant*100:.0f} cm (the floor on any of this)")
    print(f"trend window {a.window:.0f} m, sampled every {a.step:.0f} m, "
          f"hump threshold {a.threshold:.2f} m")
    print()
    print(f"  {'overlay':26} {'len m':>7} {'RMS':>6} {'max+':>6} {'max-':>6} "
          f"{'humps':>6} {'gradebrk p95':>12}")
    for nm, L, rms, mx, mn, cnt, gb in rows:
        flag = "  <<<" if mx > a.threshold else ""
        print(f"  {nm[:26]:26} {L:7.0f} {rms:6.2f} {mx:+6.2f} {mn:+6.2f} "
              f"{cnt:6d} {gb:11.1f}%{flag}")
    print()
    print(f"  WHOLE NETWORK  RMS {np.sqrt((R**2).mean()):.3f} m   "
          f"p95 |residual| {np.percentile(np.abs(R), 95):.2f} m   "
          f"worst +{R.max():.2f} / {R.min():.2f} m")
    print(f"  stations over {a.threshold:.2f} m: {100*(R > a.threshold).mean():.2f}%"
          f"   humps found: {len(humps)}")
    if humps:
        print("\n  worst humps:")
        for h in sorted(humps, key=lambda d: -d["peak_m"])[:12]:
            print(f"     {h['peak_m']:+.2f} m over {h['length_m']:5.1f} m  "
                  f"at ({h['x']:8.1f}, {h['z']:8.1f})  {h['overlay'][:28]}")
    if a.json:
        Path(a.json).write_text(json.dumps(humps, indent=1), encoding="utf-8")
        print(f"\n  wrote {len(humps)} humps to {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
