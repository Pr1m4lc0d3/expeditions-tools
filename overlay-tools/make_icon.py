#!/usr/bin/env python3
"""Make the editor-library picture for your own overlay or model.

The map editor shows Media\\icons\\<kind>_<name>.dds in its asset library: 128 x 128,
uncompressed A8R8G8B8 (byte for byte the header of the game's own icons). The library
ignores the alpha channel and shows the colour under it, and the game's own pictures are
light grey (200, 200, 200) where they are see-through. So this lays your picture over that
grey and keeps the alpha.

    python make_icon.py overlays fence_mine picture.png
    python make_icon.py overlays my_road my_road__d_a.tga --square
    python make_icon.py models my_shed render.png --media "D:\\somewhere\\Media"

kind     overlays or models
name     the class file's name, without .xml
picture  any image Pillow reads (png, tga, jpg, dds). --square takes the middle square,
         which is the right picture for a road: its own texture, as the game's roads use.

Writes <Media>\\icons\\<kind>_<name>.dds (default Media: Documents\\My Games\\Expeditions\\Media).
The editor only shows a custom piece's picture once editor_pictures_fix.py has been run.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from PIL import Image

SIZE = 128
BG = (200, 200, 200)
HEADER = bytes.fromhex(
    "444453207c000000071000008000000080000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000200000004100000000000000200000000000ff00"
    "00ff0000ff000000000000ff0210000000000000000000000000000000000000")


def to_dds(im: Image.Image, out: Path) -> None:
    im = im.convert("RGBA").resize((SIZE, SIZE), Image.LANCZOS)
    a = im.getchannel("A")
    flat = Image.new("RGB", im.size, BG)
    flat.paste(im.convert("RGB"), mask=a)
    r, g, b = flat.split()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(HEADER + Image.merge("RGBA", (b, g, r, a)).tobytes())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["overlays", "models"])
    ap.add_argument("name")
    ap.add_argument("picture", type=Path)
    ap.add_argument("--square", action="store_true", help="use the middle square of the picture")
    ap.add_argument("--media", type=Path,
                    default=Path(os.path.expanduser("~")) / "Documents" / "My Games" / "Expeditions" / "Media")
    a = ap.parse_args()
    im = Image.open(a.picture)
    if a.square:
        s = min(im.size)
        x, y = (im.width - s) // 2, (im.height - s) // 2
        # a road texture's alpha is its coverage, not a see-through background: drop it
        im = im.crop((x, y, x + s, y + s)).convert("RGB")
    out = a.media / "icons" / f"{a.kind}_{a.name}.dds"
    to_dds(im, out)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
