"""build_map.py -- create a complete map from real terrain, without the editor.

    python build_map.py hells_revenge 38.57835 -109.52818 --apply

A map is a pair, and both halves have to exist or it fails silently:

    Media/prebuild/level_NAME.xml     descriptor - blocks, MaxHeight, materials
    Media/prebuild/level_NAME/        data folder - 16 tiles of layer rasters

Building the 180-file data folder from nothing is possible and pointless, so the
skeleton is cloned from an existing map of the same block count, then every layer
that carried the donor's character is blanked and the heights are replaced with
real elevation. What survives the clone is exactly what should: valid rasters at
the right dimensions for every layer the engine expects.

84 blocks is the ceiling. The editor's own dialog (label 1085) states the engine
limit as a 2048 m terrain side and the slider stops at 84 blocks = 2016 m.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from saber import dds, tga, usgs, project, frame

MEDIA = project.MEDIA
PREBUILD = project.PREBUILD
BLOCK_M = 24.0
PX_PER_BLOCK = 12

# Layers that carried the donor map's identity. Reset rather than deleted, since
# the engine expects each file at its exact dimensions.
#
# NEUTRAL IS NOT ZERO. `_base_tints` is a multiplier on surface albedo, and a
# fresh map from the editor holds 127 in RGB with alpha 0. Zeroing it multiplies
# the ground to black, which is exactly how the first build came out: black and
# shiny, with no material visible at all. Values below are what the editor itself
# writes into a newly created map.
NEUTRAL = {
    "_base_tints.tga":        (127, 127, 127, 0),
    "_tint.tga":              (127, 127, 0, 255),
    "_snow.tga":              (0, 0, 0, 255),
    "_quickmud.tga":          (0, 0, 0, 255),
    "_merge_mud_wetness.tga": (0, 0, 0, 255),
    "_water.tga":             (0, 0, 0, 255),
    "_water_flow.tga":        (0, 0, 0, 255),
    "_water_mud.tga":         (0, 0, 0, 255),
    "mtrl_base_blends.tga":   (0, 0, 0, 255),
}
BLANK_LAYERS = tuple(NEUTRAL)
DROP = ("dstr_", )                     # donor's distributions
DROP_SUFFIX = ("-bak", ".orig-bak", ".disabled")

# Four layers: a base plus the three the blend map RGB channels select.
#
# THESE ARE CUSTOM BRUSHES, built by make_brush.py and living loose in
# Media/textures/tiles and Media/classes/terrain_layers, per section 4 of the
# Saber guide "Custom Level Entities: Models, Overlays, and PbrMaterials".
#
#   moab_sand       the red-orange ground, and the dominant surface
#   moab_gravel     its coarser apron
#   moab_slickrock  the pale pink-tan sandstone domes
#   moab_rubber     dark: tyre-stained rock, and the asphalt car parks
#
# The vanilla palette cannot reach this place. Nearest-match picked either
# Arizona red or dry-lake cream, and in the engine the map came out as cream
# ground with blue-grey gravel across a third of it.
#
# DO NOT RE-DERIVE A COLOUR BY MATCHING A TEXTURE MEAN TO AERIAL PHOTOGRAPHY.
# A texture mean is ALBEDO; a photograph is albedo times sunlight, white
# balanced and atmospherically corrected. Matching one to the other asks an
# unlit surface to equal a lit one and lands far too pale every time. It is why
# arizona_sand measured "+0.793 redness against the real +0.364" and was
# rejected for being too red when it was the closest thing shipped.
# The palette is MEASURED from the reference imagery, by k-means over 15.2
# million terrain pixels with UI, sky, vegetation and shadow excluded:
#
#   30.9%  L* 73.1  chroma 13.8  hue 359.5    the pale slickrock domes
#   30.1%  L* 66.0  chroma 20.6  hue  14.6    the open ground
#   14.1%  L* 52.4  chroma 10.9  hue 356.6    shaded rock
#    8.8%  L* 30.1  chroma  3.0  hue 328.0    rubber, asphalt and deep shade
#
# HUE IS THE WHOLE STORY: this place sits near 0-15 degrees and every arizona_*
# layer sits at 62. A 45-degree error is why nothing looked right, and it is not
# fixable by picking a different vanilla layer -- they are all at 62.
#
DESERT = [("sand_pbr_03", 3), ("arizona_cracked_ground", 3),
          ("dry_lake_bottom", 3), ("us_concrete_old", 3)]


def clean_tiles(folder: Path, keep_layout=True) -> dict:
    """Strip the donor's painted character, keeping every raster's dimensions."""
    stats = {"blanked": 0, "dropped": 0}
    for tile in sorted((folder / "data").glob("[0-9]*_[0-9]*")):
        for p in sorted(tile.iterdir()):
            if not p.is_file():
                continue
            if p.name.startswith(DROP) or p.name.endswith(DROP_SUFFIX):
                p.unlink(); stats["dropped"] += 1; continue
            if p.name in NEUTRAL:
                a = tga.read(p)
                z = np.zeros_like(a)
                for ch, v in enumerate(NEUTRAL[p.name][:a.shape[-1]]):
                    z[..., ch] = v
                tga.write(p, z)
                stats["blanked"] += 1
    return stats


def write_heights(folder: Path, terrain, tile_px: int, max_height: float,
                  floor_m: float | None = None) -> int:
    """Real elevation straight into the 16 `_height.dds` tiles.

    The raster goes through `frame.orient` and the coordinates through
    `frame.to_map`, so the terrain and everything placed on it share one
    handedness. Flipping only one of the two is what put roads on mirrored
    ground in their un-mirrored places.
    """
    master_n = tile_px * 4 + 1
    z = usgs.resample(terrain.elevation, master_n)
    z = frame.orient(z)
    lo = float(np.nanmin(z)) if floor_m is None else floor_m
    norm = np.clip((z - lo) / max_height, 0.0, 1.0).astype(np.float32)
    n = 0
    for col in range(4):
        for row in range(4):
            p = folder / "data" / f"{col}_{row}" / "_height.dds"
            cur = dds.read_r16f(p)
            h, w = cur.shape
            dds.write_r16f(p, norm[row*tile_px:row*tile_px+h,
                                   col*tile_px:col*tile_px+w].astype(np.float32))
            n += 1
    return n


def clear_content(folder: Path):
    """Remove the donor's objects, zones and objectives. A fresh map has none."""
    sub = folder / "subgroups"
    if sub.is_dir():
        shutil.rmtree(sub)
    sub.mkdir()
    for d in ("data/Overlay", "data/River"):
        p = folder / d
        if p.is_dir():
            shutil.rmtree(p)
        p.mkdir(parents=True)
    (folder / "_zones.xml").write_text("<Zones>\n</Zones>\n", encoding="utf8")
    (folder / "_sounds.xml").write_text("<Sounds/>\n", encoding="utf8")
    for f in ("zone_settings.json", "objective_settings.json"):
        p = folder / f
        if p.exists():
            p.unlink()
    for junk in list(folder.glob("_height_source.png*")):
        junk.unlink()


def add_zone(folder: Path, zid: str, x: float, z: float, diameter=10.0):
    """A single deploy zone, so the map is enterable.

    TEN METRES, because that is what both working reference maps use --
    Diamondback's Cathedral Pit and the objectives test map's deploy are each
    LengthDiameter 10. The default here was 40, and on slickrock a zone that
    wide swallowed a 31 degree wall with 5.2 m of relief inside it; at 10 m the
    same spot is 9.6 degrees at worst over 1.0 m. A deploy zone has to hold
    trucks.
    """
    p = folder / "_zones.xml"
    body = p.read_text(encoding="utf8").replace("</Zones>", "").rstrip()
    body += f"""
\t<Zone
\t\tShapeType="0"
\t\tOrg="({x:.5f}; {z:.5f})"
\t\tDir="(1; 0)"
\t\tId="{zid}"
\t\tDistributionGroupName=""
\t\tUiName="{zid}"
\t\tHeight="2"
\t\tLengthDiameter="{diameter:g}"
\t\tWidth="{diameter:g}"
\t\tIsWall="false"
\t\tBorderRoundingRadius="1.5"
\t\tBorderThickness="0.75"
\t\tBorderScrollSpeed="-1"
\t\tColor="(1; 1; 1; 1)"
\t/>
</Zones>
"""
    p.write_text(body, encoding="utf8")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="without the level_ prefix")
    ap.add_argument("lat", type=float)
    ap.add_argument("lon", type=float)
    ap.add_argument("--blocks", type=int, default=84,
                    help="84 is the engine maximum (2016 m)")
    ap.add_argument("--donor", default="level_ozark_ar",
                    help="map of the same block count to clone the skeleton from")
    ap.add_argument("--max-height", type=float, default=None)
    ap.add_argument("--resolution", type=float, default=2.0)
    ap.add_argument("--deploy", metavar="LAT,LON",
                    help="put a deploy zone at this real-world point")
    ap.add_argument("--title", default=None)
    ap.add_argument("--description", default="")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    span = args.blocks * BLOCK_M
    tile_px = args.blocks * PX_PER_BLOCK
    folder = PREBUILD / f"level_{args.name}"
    print(f"level_{args.name}: {args.blocks} blocks = {span:.0f} m, "
          f"tiles {tile_px} px, master {tile_px*4+1} px, 0.5 m/px")
    if args.blocks > 84:
        print("  !! over the engine limit of 84 blocks / 2048 m")
        return 2

    print(f"\nfetching terrain at {args.resolution:g} m/px ...")
    t = usgs.fetch(args.lat, args.lon, span, args.resolution)
    print("  " + t.describe())
    relief = t.relief
    mh = args.max_height or float(np.ceil(relief * 1.05))
    print(f"  relief {relief:.1f} m -> MaxHeight {mh:.0f}, "
          f"height step {100*relief/2048:.1f} cm")

    if not args.apply:
        print(f"\nwould clone {args.donor} -> {folder}")
        print("(dry run -- pass --apply)")
        return 0
    if folder.exists():
        print(f"  !! {folder} exists; refusing to overwrite a map project")
        return 2

    print(f"\ncloning skeleton from {args.donor} ...")
    shutil.copytree(PREBUILD / args.donor, folder)
    st = clean_tiles(folder)
    print(f"  blanked {st['blanked']} layer rasters, dropped {st['dropped']} files")
    clear_content(folder)
    print("  cleared subgroups, zones, overlays, rivers, objectives")
    n = write_heights(folder, t, tile_px, mh)
    print(f"  wrote real elevation into {n} height tiles")

    if args.deploy:
        dlat, dlon = (float(v) for v in args.deploy.split(","))
        dx, dz = frame.to_map(dlat, dlon, args.lat, args.lon)
        add_zone(folder, "deploy", dx, dz)
        print(f"  deploy zone at map ({dx:.0f}, {dz:.0f}), "
              f"{min(span/2-abs(dx), span/2-abs(dz)):.0f} m from the nearest edge")

    from saber import layers as _layers
    _layers.check([n for n, _ in DESERT])
    project.write_descriptor(args.name, args.blocks, mh, layers=DESERT,
                             terrain_id=args.title or args.name)
    print(f"  wrote descriptor level_{args.name}.xml")

    # A map with no zone_settings and no objective_settings opens, builds and
    # drives, and then will not pack. Stripping the donor removed both the first
    # time and the failure only showed up at the very last step.
    import json as _json
    import finish_map as fin
    lvl = f"level_{args.name}"
    # NOTE the pattern: a literal backspace (0x08) lived here for weeks because a
    # shell heredoc turned the \b word-boundary escape into the control character
    # it also spells. The pattern then required a backspace before Id, matched
    # nothing, and the build reported "no zones" on a file that plainly had one.
    # Never edit a regex through sed or a heredoc.
    zx = folder / "_zones.xml"
    ztext = zx.read_text(encoding="utf8") if zx.is_file() else ""
    zones = [(z, i == 0, 4) for i, z in enumerate(
        re.findall(r'\bId="([^"]+)"', ztext))]
    if not zones:
        print(f"  !! _zones.xml exists={zx.is_file()} chars={len(ztext)} "
              f"head={ztext[:70]!r}")
    title = args.title or args.name.replace("_", " ").title()
    if zones:
        try:
            for fn, data in (
                ("zone_settings.json", fin.zone_settings(lvl, zones)),
                ("objective_settings.json", fin.objective_settings(
                    title, args.description or f"Open exploration of {title}.",
                    zones[0][0])),
                ("level_desc.json", fin.level_desc(f"{lvl}_preview",
                                                   f"{lvl}_save")),
            ):
                (folder / fn).write_text(_json.dumps(data, indent=4),
                                         encoding="utf8")
            print(f"  wrote zone, objective and level settings "
                  f"({len(zones)} zone(s), deploy '{zones[0][0]}')")
        except Exception as exc:
            print(f"  !! settings NOT written: {type(exc).__name__}: {exc}")
    else:
        print("  !! no zones -- map will not be enterable or packable")

    # A step that fails quietly is how a map reached the packer missing the two
    # files that make it packable, with nothing in the output saying so. The
    # check is on the FILES, not on whether the code above thinks it ran.
    missing = [fn for fn in ("zone_settings.json", "objective_settings.json",
                             "level_desc.json") if not (folder / fn).is_file()]
    if missing:
        print(f"\n  !! INCOMPLETE -- missing {', '.join(missing)}")
        print(f"  !! run: python finish_map.py {lvl} --title \"{title}\" --apply")
        return 1
    print(f"\ndone. Open it in the editor -- the terrain is already in the tiles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
