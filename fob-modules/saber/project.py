"""Create and describe Expeditions map projects.

A map is a **pair**, and missing either half is silent:

    Media/prebuild/level_NAME.xml     the descriptor
    Media/prebuild/level_NAME/        the data folder

With only the folder the map does not appear anywhere in the editor and nothing
reports why. With only the descriptor it appears and fails to open.

``MaxHeight`` in the descriptor is the whole height calibration: tile heights
are normalized 0..1 and multiplied by it. Diamondback Divide uses 512, so its
0.248..1.000 terrain spans 127 m to 512 m. ``NumBlocksX/Z`` is blocks per tile
at 12 px each, so a 216-px tile is 18 blocks and a 1008-px tile is 84.
"""
from __future__ import annotations

import shutil
from pathlib import Path

# Resolved, never hardcoded. These tools ship publicly and target BOTH
# SnowRunner and Expeditions, so an absolute path with a username in it works
# on exactly one computer. See gamepaths: SABER_GAME / SABER_MEDIA / toolkit.json
# override, otherwise whichever supported game is installed.
from . import gamepaths

MEDIA = gamepaths.media_root()
PREBUILD = MEDIA / "prebuild"

DESCRIPTOR = """<Terrain NumBlocksX="{blocks}" NumBlocksZ="{blocks}" MaxHeight="{max_height}" TerrainId="{terrain_id}">
\t<PbrMaterials>
\t\t<PbrMaterial Name="" RoughnessWetnessMult="0.5" AlbedoWetnessMult="2" MaskIndex="0">
{layers}
\t\t</PbrMaterial>
\t</PbrMaterials>
\t<Distributions/>
\t<Daytimes>
\t\t<Night/><NightToDay/><DayVariants/><DayEarlyVariants/>
\t\t<DayMidVariants/><DayLateVariants/><DayToNight/><ForceDayTime/><NightFactor/>
\t</Daytimes>
</Terrain>
"""

LAYER = '\t\t\t<Layer Tile="{tile}" UvTiling="{uv}" Contrast="0.9" MaskIndex="{mask}" />'


def write_descriptor(name: str, blocks: int, max_height: float,
                     layers: list[tuple[str, int]] | None = None,
                     terrain_id: str | None = None,
                     prebuild: Path = PREBUILD) -> Path:
    """Write level_NAME.xml.

    ``layers`` is [(tile_name, uv_tiling)] in mask order; the first gets
    MaskIndex -1 (base) and the rest 0,1,2... matching the blend map channels.
    Diamondback uses arizona_sand, arizona_gravel, grass_wild_01, arizona_mud.
    """
    layers = layers or [("arizona_sand", 3)]
    rendered = "\n".join(
        LAYER.format(tile=t, uv=uv, mask=(-1 if i == 0 else i - 1))
        for i, (t, uv) in enumerate(layers)
    )
    path = Path(prebuild) / f"level_{name}.xml"
    path.write_text(DESCRIPTOR.format(blocks=blocks, max_height=max_height,
                                      terrain_id=terrain_id or name,
                                      layers=rendered), encoding="utf-8")
    return path


def clone_skeleton(new_name: str, source: str = "level_model_test",
                   prebuild: Path = PREBUILD) -> Path:
    """Copy a known-good minimal project as the data folder for a new map.

    Building the 180-file data folder from scratch is possible and pointless:
    cloning guarantees every supporting layer (tints, water, snow, mud masks,
    material layout) is valid, leaving only the heightmaps to author.
    """
    src = Path(prebuild) / source
    dst = Path(prebuild) / f"level_{new_name}"
    if not src.is_dir():
        raise FileNotFoundError(f"skeleton source not found: {src}")
    if dst.exists():
        raise FileExistsError(f"{dst} already exists; refusing to overwrite a map project")
    shutil.copytree(src, dst)
    return dst


def describe(name: str, prebuild: Path = PREBUILD) -> dict:
    """Report both halves of a map pair and whether the editor will see it."""
    xml = Path(prebuild) / f"level_{name}.xml"
    folder = Path(prebuild) / f"level_{name}"
    info = {"descriptor": xml, "folder": folder,
            "descriptor_exists": xml.is_file(), "folder_exists": folder.is_dir()}
    if info["folder_exists"]:
        data = folder / "data"
        info["tiles"] = sorted(p.name for p in data.iterdir()) if data.is_dir() else []
        info["file_count"] = sum(1 for _ in folder.rglob("*") if _.is_file())
    info["visible_in_editor"] = info["descriptor_exists"] and info["folder_exists"]
    return info

