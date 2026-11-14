"""make_overlays.py -- lay a road network as Overlay objects, the engine's own roads.

A road in this engine is NOT carved terrain. It is an `Overlay`: a curve with a
road material that drapes over the ground. Trying to build one by excavating the
heightmap instead produced a bare scar on a sidehill, and forcing a drivable
gradient across 150 m of relief made the grader move 30 m of earth, which puts a
cliff at every boundary with untouched ground.

Format, read from a file the editor itself wrote:

    <Overlay Type="dirt_road_rus_pbr" Flatten="false" ApplyOffset="true"
             Brushes="" Id="2753CAF01DF1" isInvisible="false">
        <Curve>
            <Node Position="(413.9551; 349.81213)" Width="7" EraseSnow="true" />
        </Curve>
    </Overlay>

Two details that are easy to get wrong and are not guessable:

* nodes sit inside a `<Curve>` element, unlike `River`, whose nodes are direct
  children;
* `Position` is TWO components, x and z. There is no height. The road follows the
  terrain, which is the whole reason it is the right tool.

Defaults are inverted from what a usable road wants:

* `Flatten="false"` -> the terrain under the road is left alone. Set TRUE and it
  is levelled ACROSS the width (never along the length, so it fixes camber and
  cross-slope, not longitudinal roughness).
* `ApplyOffset="true"` -> the road deliberately ADDS elevations, ruts and bumps.
  Set FALSE for a graded road; leave true for an abandoned track.

Engine limit: only ONE textural overlay renders per terrain block, so every road
here uses a single `Type`. Mixing types means one of them silently disappears.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import numpy as np

from saber.river import MEDIA

SPAN = 2016.0
GRID = 4033


def overlay_id(name: str) -> str:
    """12 uppercase hex characters, matching the editor's own Id format.

    Derived from the name so regenerating a road keeps its identity instead of
    orphaning the previous file.
    """
    return hashlib.sha1(name.encode("utf8")).hexdigest()[:12].upper()


def resample(px, py, step_px):
    seg = np.hypot(np.diff(px), np.diff(py))
    s = np.concatenate([[0.0], np.cumsum(seg)])
    if s[-1] <= 0:
        return px, py
    t = np.arange(0.0, s[-1], step_px)
    if t[-1] < s[-1]:
        t = np.append(t, s[-1])
    return np.interp(t, s, px), np.interp(t, s, py)


def build(name, px, py, road_type, width, flatten, apply_offset, spacing_m):
    mpp = SPAN / (GRID - 1)
    # the network stores 4033-grid PIXELS, not metres
    x = px * mpp - SPAN / 2
    z = py * mpp - SPAN / 2
    x, z = resample(x, z, spacing_m)
    nodes = "\n".join(
        f'\t\t<Node Position="({a:.5f}; {b:.5f})" Width="{width:g}" EraseSnow="true" />'
        for a, b in zip(x, z))
    return (f'<Overlay\n'
            f'\tType="{road_type}"\n'
            f'\tFlatten="{"true" if flatten else "false"}"\n'
            f'\tApplyOffset="{"true" if apply_offset else "false"}"\n'
            f'\tBrushes=""\n'
            f'\tId="{overlay_id(name)}"\n'
            f'\tisInvisible="false"\n'
            f'>\n\t<Curve>\n{nodes}\n\t</Curve>\n</Overlay>\n'), len(x)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level")
    ap.add_argument("--network", default=str(Path(__file__).parent / "network.npz"))
    ap.add_argument("--type", default="dirt_road_rus_pbr",
                    help="ONE type for the whole map; only one renders per block")
    ap.add_argument("--width", type=float, default=7.0)
    ap.add_argument("--spacing", type=float, default=20.0,
                    help="metres between curve nodes; too close risks a crash")
    ap.add_argument("--flatten", action="store_true", default=True,
                    help="level the terrain across the road's width (default on)")
    ap.add_argument("--no-flatten", dest="flatten", action="store_false")
    ap.add_argument("--ruts", action="store_true", default=False,
                    help="ApplyOffset: add ruts and bumps (default OFF)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    out_dir = MEDIA / "prebuild" / args.level / "data" / "Overlay"
    net = np.load(args.network)
    roads = sorted(k[:-2] for k in net.files if k.endswith("_x"))

    print(f"type={args.type}  width={args.width:g} m  "
          f"Flatten={args.flatten}  ApplyOffset={args.ruts}")
    print(f"{'road':<18} {'length':>9} {'nodes':>7}  file")
    total = 0
    for name in roads:
        px = net[name + "_x"].astype(float)
        py = net[name + "_y"].astype(float)
        mpp = SPAN / (GRID - 1)
        length = float(np.hypot(np.diff(px), np.diff(py)).sum()) * mpp
        if length < 30:
            print(f"{name:<18} {length:8.0f}m  -- too short, skipped")
            continue
        xml, n = build(name, px, py, args.type, args.width,
                       args.flatten, args.ruts, args.spacing)
        path = out_dir / f"Overlay_{overlay_id(name)}.xml"
        print(f"{name:<18} {length:8.0f}m {n:7d}  {path.name}")
        total += 1
        if args.apply:
            out_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(xml, encoding="utf-8", newline="\n")

    print(f"\n{total} overlays {'written' if args.apply else 'would be written'} "
          f"to {out_dir}")
    if not args.apply:
        print("(dry run -- pass --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
