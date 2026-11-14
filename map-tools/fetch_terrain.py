"""fetch_terrain.py -- build a map's source heightmap from real USGS terrain.

    python fetch_terrain.py 37.2736 -109.9382 --span 2016 --blocks 84
    python fetch_terrain.py 36.0559 -93.3138 --span 2016 --blocks 84 \
        --level level_ozark_ar --apply

Writes `_height_source.png` at the TILE resolution the editor expects, then the
map is created or updated in the editor with Copy Source Heightmap.

The point of using real terrain is that roads come with it. A road is a cut-and-
fill bench in the actual ground, so 1 m lidar has it already graded, benched and
switchbacked by whoever surveyed it. Nothing here has to invent an alignment.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from saber import usgs, dds, frame
from saber.river import MEDIA

# blocks -> tile pixels, from the maps on disk
KNOWN = {4: 48, 34: 408, 84: 1008}


def write_tiles(level, terrain, tile_px, max_height, vertical_scale):
    """Write real elevation straight into the 16 height tiles.

    The editor builds from `data/<col>_<row>/_height.dds`, so this is the whole
    import: no Copy Source Heightmap, no GUI beyond reopening the map. The tiles
    must already exist - the editor creates them when the level is first opened
    and the "Could not load terrain .STG file. Create it?" prompt is answered.

    Master is `blocks * 48 + 1` px at a fixed 0.5 m/px, and tiles carry the
    shared +1 edge, so the last tile in each direction is one pixel larger.
    """
    from saber import usgs as _usgs
    data = MEDIA / "prebuild" / level / "data"
    if not data.is_dir():
        print(f"  !! {data} does not exist -- create the map in the editor first")
        return 2
    master_n = tile_px * 4 + 1
    z = _usgs.resample(terrain.elevation, master_n)
    # A USGS raster is north-up and west-left; the map's X axis is not. Every
    # other raster tool here calls frame.orient (build_map, paint_tints,
    # palette, inspect_map, study_clusters) and this one did not, so terrain
    # came in mirrored east-west while fetch_trails placed roads through
    # frame.to_map, which DOES apply the flip. Measured on Shafer Trail: only
    # 10.6% of overlay nodes landed on a road bench, below the 14.7% chance
    # level, against 53.5% once the terrain was flipped.
    z = frame.orient(z)
    lo = float(np.nanmin(z))
    norm = np.clip((z - lo) * vertical_scale / max_height, 0.0, 1.0).astype(np.float32)
    print(f"\nwriting {master_n}x{master_n} master into 16 tiles "
          f"({terrain.span_m/(master_n-1):.3f} m/px)")
    n = 0
    for col in range(4):
        for row in range(4):
            p = data / f"{col}_{row}" / "_height.dds"
            if not p.exists():
                print(f"  !! {p} missing -- open the level in the editor once first")
                return 2
            cur = dds.read_r16f(p)
            h, w = cur.shape
            crop = norm[row*tile_px:row*tile_px+h, col*tile_px:col*tile_px+w]
            # ONE backup, fixed name, overwritten. A timestamp in a backup
            # filename cannot overwrite, so every run leaked another 16 files
            # into data/<tile>/ -- a folder the editor scans.
            shutil.copy2(p, p.with_name(p.name + ".prev"))
            dds.write_r16f(p, crop.astype(np.float32))
            n += 1
    print(f"wrote {n} tiles")
    print(f"\nNext: set Blocks MaxHeight to {max_height:.0f} in the editor, then "
          f"CLOSE and REOPEN the map -- tiles are read at open, not on Rebuild.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lat", type=float)
    ap.add_argument("lon", type=float)
    ap.add_argument("--span", type=float, default=2016.0,
                    help="ground metres across the map (84 blocks = 2016 m)")
    ap.add_argument("--blocks", type=int, default=84)
    ap.add_argument("--tile-px", type=int, default=None,
                    help="override the tile size if the map is unusual")
    ap.add_argument("--resolution", type=float, default=1.0,
                    help="metres per pixel to fetch before resampling")
    ap.add_argument("--max-height", type=float, default=None,
                    help="engine MaxHeight; default is relief + 5%%")
    ap.add_argument("--vertical-scale", type=float, default=1.0,
                    help="exaggeration. 1.0 means this IS that place")
    ap.add_argument("--level", help="write into this map's prebuild folder")
    ap.add_argument("--write-tiles", action="store_true",
                    help="write the 16 _height.dds tiles DIRECTLY. The editor "
                         "builds from these, so this needs no Copy Source "
                         "Heightmap and no GUI step beyond reopening the map.")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    tile = args.tile_px or KNOWN.get(args.blocks)
    if not tile:
        print(f"unknown block count {args.blocks}; pass --tile-px "
              f"(known: {KNOWN})")
        return 2

    print(f"fetching {args.span:.0f} x {args.span:.0f} m at "
          f"{args.resolution:g} m/px around {args.lat:.5f},{args.lon:.5f} ...")
    t = usgs.fetch(args.lat, args.lon, args.span, args.resolution)
    print("  " + t.describe())
    if t.coverage < 0.999:
        print(f"  !! only {100*t.coverage:.1f}% has 3DEP coverage -- "
              f"outside the US, or a gap. Missing ground is filled with the mean.")

    norm, mh, report = usgs.to_map(t, tile, args.max_height,
                                   vertical_scale=args.vertical_scale)
    # Same flip as the tile path: _height_source feeds Copy Source Heightmap,
    # so leaving it north-up/west-left imports a mirrored map by another route.
    norm = frame.orient(norm)
    print("\n" + report)

    # how much of the road network came free?
    from scipy import ndimage
    z = t.elevation
    zf = np.nan_to_num(z, nan=np.nanmean(z))
    mpp = t.metres_per_px
    gy, gx = np.gradient(zf, mpp)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    relief = ndimage.maximum_filter(zf, 41) - ndimage.minimum_filter(zf, 41)
    bench = (slope < 10) & (relief > 20)
    area = float(bench.sum()) * mpp * mpp
    print(f"\n  benches on steep ground (roads, tracks, terraces): "
          f"{area:.0f} m2 ~= {area/6:.0f} m of 6 m-wide road")

    if not args.level:
        print("\n(no --level given, nothing written)")
        return 0
    out = MEDIA / "prebuild" / args.level / "_height_source.png"
    print(f"\ntarget: {out}")
    if out.exists():
        h = dds.read_header(out)
        print(f"  existing source is {h['width']}x{h['height']}"
              + ("" if h['width'] == tile else f"  <-- WRONG, should be {tile}"))
    if not args.apply:
        print("\n(dry run -- pass --apply to write)")
        return 0

    if args.write_tiles:
        return write_tiles(args.level, t, tile, mh, args.vertical_scale)

    out.parent.mkdir(parents=True, exist_ok=True)
    # create_r16f clones a header from an existing R16F file; any height tile of
    # this map is the right donor, and the map's own source is better still
    template = out if out.exists() else None
    if template is None:
        cands = sorted((out.parent / "data").glob("*/_height.dds"))
        if not cands:
            print("  !! no R16F file in this map to clone a header from")
            return 2
        template = cands[0]
    if out.exists():
        # One fixed-name backup, overwritten. The previous form built the name
        # from strftime TWICE, so a run that crossed a second boundary set
        # `template` to a file that was never written.
        prev = out.with_name(out.name + ".prev")
        shutil.copy2(out, prev)
        template = prev
    dds.create_r16f(out, norm, template)
    print(f"wrote {out} ({tile}x{tile}, R16F)")
    print(f"\nNext: open the map in the editor, set Blocks MaxHeight to {mh:.0f},")
    print("then Geometry context menu -> Copy Source Heightmap, then Rebuild Terrain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
