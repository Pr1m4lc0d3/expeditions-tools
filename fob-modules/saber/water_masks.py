"""Read and paint the engine's tiled water masks.

Two textures decide how a river reads on screen, and neither is a single file:

    data/<col>_<row>/_water.tga        G = foam, B = speed
    data/<col>_<row>/_water_mud.tga    blend toward the river's Muddy colour

There is no master in ``fullTexture`` for either of them, unlike the distribution
maps. The tiles ARE the source, so they are assembled here, painted as one image,
and split back. The tile grid is 4x4; the last tile in each direction carries the
shared +1 edge, so a base of 651 gives a 2605 master and a base of 63 gives 253.
Folder names are col_row, proven by which tiles carry the extra row or column.

Channel meanings come from the official guide, ``geometry_brushes_for_terrain/water``:
foam in the green channel, speed in the blue, ``Value = 1`` being maximum water
velocity. The red channel is NOT the water extent -- it holds a uniform grid that
is present in identical quantity on every tile including tiles with no water at
all, so it must never be used to decide where the river is. The river spline is
the only authority on that.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import numpy as np

from . import tga

GRIDN = 4
SPAN = 2016.0


def tile_shapes(data_dir, name):
    """{(col, row): (h, w)} for one mask across the tile grid."""
    data_dir = Path(data_dir)
    out = {}
    for col in range(GRIDN):
        for row in range(GRIDN):
            p = data_dir / f"{col}_{row}" / name
            if p.exists():
                h = tga.read_header(p)
                out[(col, row)] = (h["height"], h["width"])
    if not out:
        raise FileNotFoundError(f"no {name} under {data_dir}")
    return out


def base_and_master(shapes):
    """Base tile edge and the assembled master edge."""
    base = min(min(s) for s in shapes.values())
    return base, base * GRIDN + 1


def assemble(data_dir, name):
    """Stitch the 4x4 tiles into one master image."""
    data_dir = Path(data_dir)
    shapes = tile_shapes(data_dir, name)
    base, master_n = base_and_master(shapes)
    master = None
    for (col, row) in shapes:
        img = tga.read(data_dir / f"{col}_{row}" / name)
        if master is None:
            master = np.zeros((master_n, master_n, img.shape[2]), np.uint8)
        h, w = img.shape[:2]
        master[row * base:row * base + h, col * base:col * base + w] = img
    return master, base


def split(data_dir, name, master, base, backup=True):
    """Write a master back into its tiles. Returns the number of tiles changed."""
    data_dir = Path(data_dir)
    written = 0
    for (col, row) in tile_shapes(data_dir, name):
        p = data_dir / f"{col}_{row}" / name
        cur = tga.read(p)
        h, w = cur.shape[:2]
        crop = master[row * base:row * base + h, col * base:col * base + w]
        if crop.shape != cur.shape:
            raise ValueError(f"{p}: crop {crop.shape} != tile {cur.shape}")
        if np.array_equal(crop, cur):
            continue
        if backup:
            # ONE backup, fixed name, overwritten. A timestamp in a backup
            # filename cannot overwrite, so every run leaks another copy --
            # 32 files and 27.3 MB after a single run here, inside data/,
            # which is a folder the editor scans.
            bak = p.with_name(p.name + ".prev")
            if not bak.exists():
                shutil.copy2(p, bak)
        tga.write(p, crop)
        written += 1
    return written


def world_to_px(x, z, master_n, span=SPAN):
    """World metres -> (row, col) in an assembled master. row tracks z, col tracks x.

    Verified against a painted reach: the median painted pixel sits 7.6 m from the
    spline on a channel of 28.7 m half-width, i.e. inside the water.
    """
    mpp = span / (master_n - 1)
    return (z + span / 2) / mpp, (x + span / 2) / mpp


def px_per_m(master_n, span=SPAN):
    return (master_n - 1) / span


def stamp(target, rows, cols, values, mode="max"):
    """Scatter values into an image, keeping the strongest write per pixel.

    ``max`` is the right default for foam and speed: two overlapping sources of
    turbulence do not cancel, and a later low value must not erase an earlier
    high one.
    """
    h, w = target.shape[:2]
    keep = (rows >= 0) & (rows < h) & (cols >= 0) & (cols < w)
    r, c, v = rows[keep], cols[keep], values[keep]
    if mode == "max":
        np.maximum.at(target, (r, c), v)
    else:
        target[r, c] = v
    return target
