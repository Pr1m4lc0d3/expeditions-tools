"""sync_tiles.py -- a distribution mask lives in TWO places; both must be written.

    fullTexture/dstr_<name>.tga     4033x4033 master
    data/<r>_<c>/dstr_<name>.tga    4x4 grid of 1008x1008 tiles

The editor reads the TILES. Editing only the master changes nothing you can see,
which is exactly what happened on the first roadway-clearing pass: 16 masters were
zeroed along the road and the trees were all still there in game.

Tile convention, verified by byte comparison: tile "r_c" is master[r*1008:(r+1)*1008,
c*1008:(c+1)*1008]. The 4x4 grid covers 4032 px; the master's final row and column
are not represented in any tile.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import numpy as np

from saber import tga

TILE = 1008
GRIDN = 4


def tile_paths(data_dir):
    """Folder names are COL_ROW, i.e. "x_y" -- not row_col.

    Proven by shape: tile "3_0" reads 1008 rows x 1009 cols (an extra COLUMN, so it
    is the right-hand column of tiles), while "0_3" reads 1009 x 1008 (an extra ROW,
    so it is the bottom row). The final tile in each direction carries the master's
    shared +1 edge, which is why 4 x 1008 tiles cover a 4033 px master.
    """
    data_dir = Path(data_dir)
    return {(col, row): data_dir / f"{col}_{row}"
            for col in range(GRIDN) for row in range(GRIDN)}


def crop_for(master, col, row, shape):
    h, w = shape[0], shape[1]
    return master[row * TILE:row * TILE + h, col * TILE:col * TILE + w]


def verify_tiles(master_path, data_dir):
    """Confirm every tile equals its master crop. Returns {tile: meanAbsDiff}."""
    master = tga.read(master_path)
    name = Path(master_path).name
    out = {}
    for (col, row), d in tile_paths(data_dir).items():
        p = d / name
        if not p.exists():
            continue
        cur = tga.read(p)
        crop = crop_for(master, col, row, cur.shape)
        if crop.shape != cur.shape:
            out[f"{col}_{row}"] = f"shape {crop.shape} vs {cur.shape}"
            continue
        out[f"{col}_{row}"] = float(np.mean(np.abs(cur.astype(np.int32) -
                                                  crop.astype(np.int32))))
    return out


def push_master_to_tiles(master_path, data_dir, backup=True):
    """Split a 4033x4033 master into its 16 tiles and overwrite them."""
    master = tga.read(master_path)
    name = Path(master_path).name
    written = 0
    for (col, row), d in tile_paths(data_dir).items():
        p = d / name
        if not p.exists():
            continue
        existing = tga.read(p)
        crop = crop_for(master, col, row, existing.shape)
        if crop.shape != existing.shape:
            raise ValueError(f"{p}: crop {crop.shape} != tile {existing.shape}")
        if np.array_equal(crop, existing):
            continue
        if backup and not (p.parent / (p.name + '.orig-bak')).exists():
            shutil.copy2(p, str(p) + '.orig-bak')
        tga.write(p, crop)
        written += 1
    return written


def clear_and_sync(full_texture_dir, data_dir, mask, restore_from_backup=True):
    """Zero every dstr master inside `mask`, then push each into its tiles."""
    full_texture_dir = Path(full_texture_dir)
    report = {}
    for master in sorted(full_texture_dir.glob('dstr_*.tga')):
        if restore_from_backup:
            baks = sorted(full_texture_dir.glob(master.name + '.pre-clear-*-bak'))
            if baks:
                shutil.copy2(baks[0], master)      # start from the untouched original
        img = tga.read(master)
        if img.shape[:2] != mask.shape:
            report[master.name] = 'shape mismatch %s' % (img.shape[:2],)
            continue
        cleared = int((img[..., 0] > 0)[mask].sum())
        img[mask] = 0
        tga.write(master, img)
        n = push_master_to_tiles(master, data_dir, backup=True)
        report[master.name] = (cleared, n)
    return report
