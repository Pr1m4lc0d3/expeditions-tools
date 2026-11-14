"""new_map_from.py -- start a new map from an existing one's support layers.

`project.clone_skeleton` is a blind `copytree`. Pointed at a real map that is a
straight copy of somebody's finished work: every rock placement, every zone,
every objective, sitting on terrain they were never placed against. The models
are the expensive part of a map and the wrong ones are worse than none.

This copies what a NEW map legitimately reuses -- the per-tile support layers
that must exist and be valid (tints, water, snow, mud masks, material layout,
distribution masks) -- and refuses to carry:

  subgroups/            model placements and distribution setups. Somebody's
                        hand-composed rock, in coordinates that mean nothing here.
  *zone*, *objective*   the map's missions and deploy points.
  features.json         feature markers tied to the old terrain.
  _sounds.xml           area sounds placed against the old landform.
  _height_source.png    the old terrain itself.
  fullTexture/          OUTPUT, not input. The editor builds from data/<col>_<row>/.
                        On Hell's Revenge this alone is 2.3 GB of the 3.06 GB.
  *.bak, *.pre-*,       backup chains. Never propagate one into a new project;
  *.orig-bak, *.disabled see the one-backup-only rule.

Usage:
    python new_map_from.py hells_revenge shafer_trail \
        --terrain-id "Shafer Trail" --max-height 512 \
        --description "The Shafer switchbacks, Canyonlands, Utah."
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

try:                                    # XXE-safe parser when available
    from defusedxml import ElementTree as ET
except ImportError:                     # stdlib fallback; input is a local map descriptor
    import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).parent))
from saber import project  # noqa: E402

# Directories never copied, by name at the project root.
SKIP_DIRS = {"subgroups", "fullTexture"}

# Directories never copied wherever they appear. `data/Overlay` is the trap:
# roads live UNDER data/, not at the root, so a root-level skip list misses them
# and the new map silently inherits the old map's entire road network. Found on
# the first real use -- 35 of Hell's Revenge's overlays had come across.
# `data/River` is the same trap for water: Objectives' pond (surface 62 m) came
# across onto road_test's 20 m ground as a 42 m-deep lake (11-08).
SKIP_ANYWHERE = {"Overlay", "River"}

# Root files never copied. Matched case-insensitively as substrings, because the
# same content appears as `zone_settings.json` and `_zones.xml`.
SKIP_ROOT = ("zone", "objective", "features.json", "_sounds.xml", "_height_source",
             # the old map's compiled scene: Objectives' data_editor.stg put its sample
             # cabins and logs on level_road_test at Objectives' heights (11-08)
             ".stg", "data_for_ssl")

# Backup suffixes, anywhere in the tree.
BACKUP = re.compile(r"(\.bak$|\.orig-bak$|\.disabled$|\.pre-[^.]*-?\d*$|\.pre-.*-\d{8}-\d{6})", re.I)


def is_backup(p: Path) -> bool:
    return bool(BACKUP.search(p.name)) or ".pre-" in p.name


def neutralise(dst: Path, skeleton: str = "level_model_test") -> list[tuple[str, str]]:
    """Blank every carried raster that the source map had PAINTED.

    The support layers must exist and be valid, which is why they are copied at
    all. Their CONTENT is a different matter: a painted raster is registered to
    the landform it was painted on, so on new terrain it is noise.

    Hell's Revenge shipped into Shafer Trail with mtrl_base_blends holding 4,221
    distinct colours -- its slickrock mask -- and that mask drives layer index 1,
    which was `us_concrete_old`. The result was grey-blue concrete slabs across
    Canyonlands, which is what the owner saw.

    The test is uniformity, and it is a measurement rather than a list: a
    UNIFORM raster carries no landform information and is left alone; a
    NON-UNIFORM one IS the old map's painting. Reset value comes from an
    untouched skeleton where that skeleton is itself uniform (measured:
    _base_tints 127,127,127 and mtrl_base_blends 0,0,0 in both level_model_test
    and level_lessons), and otherwise from the raster's own modal colour, which
    is its own "unpainted" value.
    """
    import numpy as np
    from saber import tga

    skel_dir = None
    for cand in sorted((project.PREBUILD / skeleton / "data").glob("*_*")):
        if cand.is_dir():
            skel_dir = cand
            break

    neutral_cache: dict[str, np.ndarray | None] = {}

    def skeleton_neutral(name: str):
        if name in neutral_cache:
            return neutral_cache[name]
        val = None
        if skel_dir and (skel_dir / name).exists():
            try:
                a = tga.read(skel_dir / name)
                flat = a.reshape(-1, a.shape[-1])
                if len(np.unique(flat, axis=0)) == 1:
                    val = flat[0].copy()
            except Exception:
                val = None
        neutral_cache[name] = val
        return val

    tiles = sorted(p for p in (dst / "data").iterdir() if p.is_dir())

    # The reset value must be ONE value per raster NAME, not per tile. Taking a
    # per-tile modal froze each tile at whatever the old map happened to hold
    # there -- dstr_* came out with G between 106 and 134 across the 16 tiles,
    # which is a tile seam built in on purpose. Decide once, globally.
    targets: dict[str, list[Path]] = {}
    for t in tiles:
        for f in t.glob("*.tga"):
            targets.setdefault(f.name, []).append(f)

    done = []
    for name, files in sorted(targets.items()):
        painted = []
        counter: dict[tuple, int] = {}
        for f in files:
            try:
                a = tga.read(f)
            except Exception:
                continue
            if a.ndim != 3:
                continue
            flat = a.reshape(-1, a.shape[-1])
            if (flat == flat[0]).all():
                continue                       # uniform: carries nothing
            painted.append(f)
            cols, counts = np.unique(flat, axis=0, return_counts=True)
            for c, k in zip(cols, counts):
                key = tuple(int(v) for v in c)
                counter[key] = counter.get(key, 0) + int(k)
        if not painted:
            continue
        val = skeleton_neutral(name)
        src_of = "skeleton"
        if val is None:
            val = np.array(max(counter, key=counter.get), dtype=np.uint8)
            src_of = "modal across all tiles"
        for f in painted:
            a = tga.read(f)
            # a 3-channel skeleton value on a 4-channel tile (Objectives' RGBA tiles,
            # 11-03): keep the tile's own modal alpha rather than crash
            v = val[:a.shape[-1]]
            if len(v) < a.shape[-1]:
                alpha = np.bincount(a[..., 3].ravel()).argmax()
                v = np.r_[v, np.full(a.shape[-1] - len(v), alpha, np.uint8)]
            tga.write(f, np.broadcast_to(v, a.shape).copy())
        done.append((name, f"{src_of} {tuple(int(v) for v in val[:3])}", len(painted)))
    return done


def plan(src: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Return (files to copy, [(file, reason) skipped]). Nothing is written."""
    take, drop = [], []
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        top = rel.parts[0]
        if top in SKIP_DIRS:
            drop.append((rel, f"{top}/ not carried"))
        elif SKIP_ANYWHERE.intersection(rel.parts[:-1]):
            hit = next(iter(SKIP_ANYWHERE.intersection(rel.parts[:-1])))
            drop.append((rel, f"{hit}/ not carried (old map's roads and water)"))
        elif is_backup(p):
            drop.append((rel, "backup"))
        elif len(rel.parts) == 1 and any(s in rel.name.lower() for s in SKIP_ROOT):
            drop.append((rel, "map-specific"))
        elif top == "data" and len(rel.parts) == 3 and rel.suffix.lower() == ".xml":
            # data/<col>_<row>/Model.xml, Truck.xml: the editor's MAIN group objects, one
            # file per tile. Carried into level_road_test, they put Objectives' cabins,
            # adit, pickup, billboard and trucks on it at Objectives' heights (11-08).
            drop.append((rel, "the old map's placed objects (per-tile)"))
        else:
            take.append(rel)
    return take, drop


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="existing level name, without the level_ prefix")
    ap.add_argument("name", help="new level name, without the level_ prefix. LOWERCASE ONLY: "
                                 "the editor's distribution path validator lowercases the level "
                                 "path then compares it unchanged, so any uppercase makes "
                                 "distributions impossible to assign.")
    ap.add_argument("--terrain-id", default=None)
    ap.add_argument("--description", default=None)
    ap.add_argument("--max-height", type=float, default=None,
                    help="metres for normalised 1.0. Default keeps the source's.")
    ap.add_argument("--layers", default=None,
                    help="replace the PbrMaterial layers: 'tile:uv,tile:uv,...'. "
                         "The FIRST is the base (MaskIndex -1) and covers the map "
                         "wherever nothing is painted; the rest take mask channels "
                         "0,1,2 in order. Carrying the source map's layers is how a "
                         "Canyonlands map ended up rendering us_concrete_old.")
    ap.add_argument("--keep-painting", action="store_true",
                    help="carry the source map's painted rasters as-is. Off by "
                         "default: a painted raster is registered to the landform "
                         "it was painted on, so on new terrain it is noise.")
    ap.add_argument("--apply", action="store_true", help="actually write. Default is a dry run.")
    a = ap.parse_args()

    if a.name != a.name.lower():
        return print(f"refusing: '{a.name}' has uppercase; see --help") or 2

    src = project.PREBUILD / f"level_{a.source}"
    dst = project.PREBUILD / f"level_{a.name}"
    src_xml = next((p for p in project.PREBUILD.glob("level_*.xml")
                    if p.stem.lower() == f"level_{a.source}".lower()), None)
    if not src.is_dir():
        return print(f"no such map folder: {src}") or 2
    if src_xml is None:
        return print(f"no descriptor for level_{a.source}") or 2
    if dst.exists():
        return print(f"{dst} already exists; refusing to overwrite a map project") or 2

    take, drop = plan(src)
    mb = sum((src / r).stat().st_size for r in take) / 1e6
    dropped_mb = sum((src / r).stat().st_size for r, _ in drop) / 1e6

    print(f"source      level_{a.source}")
    print(f"new map     level_{a.name}")
    print(f"copying     {len(take):5d} files  {mb:9.1f} MB")
    print(f"skipping    {len(drop):5d} files  {dropped_mb:9.1f} MB")
    by_reason: dict[str, int] = {}
    for _, why in drop:
        by_reason[why] = by_reason.get(why, 0) + 1
    for why, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        print(f"              {n:5d}  {why}")

    # descriptor
    xml = src_xml.read_text(encoding="utf-8")
    if a.max_height is not None:
        xml = re.sub(r'MaxHeight="[\d.]+"', f'MaxHeight="{a.max_height}"', xml, count=1)
    if a.terrain_id:
        xml = re.sub(r'TerrainId="[^"]*"', f'TerrainId="{a.terrain_id}"', xml, count=1)
    if a.description:
        if 'TerrainDescription="' in xml:
            xml = re.sub(r'TerrainDescription="[^"]*"',
                         f'TerrainDescription="{a.description}"', xml, count=1)
        else:
            # Insert before the '>' that CLOSES the opening <Terrain ...> tag.
            # Anchoring on '<PbrMaterials>' instead put the attribute after the
            # tag had already closed, producing two '>' and invalid XML that the
            # editor would have rejected with no useful message.
            m = re.match(r'(<Terrain\b.*?)(\n?>)', xml, re.S)
            if not m:
                return print("could not find the opening <Terrain> tag to describe") or 2
            xml = (m.group(1).rstrip() + f'\n\tTerrainDescription="{a.description}"'
                   + m.group(2) + xml[m.end():])
    # The descriptor is edited with regex, so prove it still parses before it is
    # written. A malformed <Terrain> tag is exactly the failure the editor
    # reports uselessly, and it shipped once because nothing checked.
    try:
        ET.fromstring(xml)
    except ET.ParseError as e:
        print("REFUSING: the edited descriptor is not well-formed XML:")
        print(f"  {e}")
        for i, line in enumerate(xml.splitlines()[:12], 1):
            print(f"  {i:3d} | {line}")
        return 2

    if a.layers:
        spec = []
        for i, part in enumerate(a.layers.split(",")):
            tile, _, uv = part.strip().partition(":")
            spec.append(f'\t\t\t<Layer Tile="{tile}" UvTiling="{uv or 3}" '
                        f'Contrast="0.9" MaskIndex="{-1 if i == 0 else i - 1}" />')
        body = "\n".join(spec)
        new_xml, n = re.subn(r'(<PbrMaterial\b[^>]*>)(.*?)(</PbrMaterial>)',
                             lambda m: m.group(1) + "\n" + body + "\n\t\t" + m.group(3),
                             xml, count=1, flags=re.S)
        if n != 1:
            return print("could not find a <PbrMaterial> block to replace layers in") or 2
        xml = new_xml
        print(f"layers      {' | '.join(p.split(':')[0] for p in a.layers.split(','))}")

    mh = re.search(r'MaxHeight="([\d.]+)"', xml)
    bx = re.search(r'NumBlocksX="(\d+)"', xml)
    print(f"descriptor  {bx.group(1) if bx else '?'} blocks, MaxHeight {mh.group(1) if mh else '?'}")

    if not a.apply:
        print("\n(dry run -- pass --apply to write)")
        return 0

    for rel in take:
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, out)
    (dst / "subgroups").mkdir(exist_ok=True)   # editor expects it; empty is correct
    (project.PREBUILD / f"level_{a.name}.xml").write_text(xml, encoding="utf-8")

    if not a.keep_painting:
        reset = neutralise(dst)
        print(f"\nneutralised {len(reset)} painted rasters "
              f"(uniform ones left alone -- they carry no landform)")
        for name, how, ntiles in reset:
            print(f"    {name:<26} {ntiles:2d} tiles  <- {how}")

    info = project.describe(a.name)
    print(f"\nwritten     {dst}")
    print(f"tiles       {len(info.get('tiles', []))}")
    print(f"files       {info.get('file_count')}")
    print(f"visible in editor: {info['visible_in_editor']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
