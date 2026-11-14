"""clear_roadway.py -- take the trees, rocks and scatter off a map's roads.

Two independent sources put objects on a road, and both have to be handled:

1. Distributions paint from a density map, `fullTexture/dstr_<Map>.tga`. Zeroing the
   corridor in that map stops anything spawning there. The `Distribution` entries in
   `subgroups/*.xml` also carry `IgnoreOverlays`, which the docs describe as the
   switch that keeps trees off roads; it is set as well, belt and braces.
2. Hand-placed `<Model>` blocks in `subgroups/Model_*.xml`, each with a world-metre
   `Position Org="(x; y; z)"`. Those are removed outright when they sit on the road.

COORDINATES -- the trap that invalidated earlier road work:
  * `network.npz` centrelines are PIXELS in the 4033 working grid (0.5 m/px),
    NOT world metres.  world = px * 0.5 - 1008
  * `dstr_*.tga` are also 4033x4033, so the mask is built directly in that space.
  * `Model` Org values ARE world metres, so they need the conversion.
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

import numpy as np

from saber import tga

SPAN = 2016.0
GRID = 4033


def backup(path, tag='pre-clear'):
    """Copy a file aside, NEVER into the folder it came from.

    The editor enumerates everything in `subgroups\\` as a SubGroup. Backups
    written next to the original were picked up as 17 extra subgroups, and on the
    next save the editor wrote each one back out as an empty `<SubGroup/>` stub.
    In the sidebar they appear as entries with no models, no distributions and
    nothing selectable - which is exactly what a corrupted map looks like.
    """
    path = Path(path)
    dest = path.parent.parent / '_backups' / path.parent.name
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / (path.name + '.' + tag + '-' + time.strftime('%Y%m%d-%H%M%S'))
    shutil.copy2(path, out)
    return out


def road_mask_grid(network_npz, width_m, grid=GRID, span=SPAN):
    """Boolean HxW mask of the road corridor, in working-grid pixel space."""
    net = np.load(network_npz)
    mpp = span / (grid - 1)
    mask = np.zeros((grid, grid), dtype=bool)
    rad = int(np.ceil(width_m / 2.0 / mpp))
    yy, xx = np.mgrid[-rad:rad + 1, -rad:rad + 1] * mpp
    disc = np.hypot(xx, yy) <= width_m / 2.0
    for k in net.files:
        if not k.endswith('_x'):
            continue
        px = net[k].astype(float)
        py = net[k[:-2] + '_y'].astype(float)
        # densify so the corridor is continuous between centreline points
        seg = np.hypot(np.diff(px), np.diff(py))
        s = np.concatenate([[0], np.cumsum(seg)])
        if s[-1] <= 0:
            continue
        t = np.arange(0, s[-1], 1.0)          # every working-grid pixel
        px = np.interp(t, s, px)
        py = np.interp(t, s, py)
        for c0, r0 in zip(np.round(px).astype(int), np.round(py).astype(int)):
            r1, r2 = max(0, r0 - rad), min(grid, r0 + rad + 1)
            c1, c2 = max(0, c0 - rad), min(grid, c0 + rad + 1)
            if r1 >= r2 or c1 >= c2:
                continue
            mask[r1:r2, c1:c2] |= disc[(r1 - r0 + rad):(r2 - r0 + rad),
                                       (c1 - c0 + rad):(c2 - c0 + rad)]
    return mask


def clear_density_maps(full_texture_dir, mask, backup=True):
    """Zero every dstr_*.tga inside the corridor. Returns per-file pixel counts."""
    out = {}
    for p in sorted(Path(full_texture_dir).glob('dstr_*.tga')):
        img = tga.read(p)
        if img.shape[:2] != mask.shape:
            out[p.name] = ('SKIPPED, shape %s vs mask %s' % (img.shape[:2], mask.shape))
            continue
        before = int((img[..., 0] > 0)[mask].sum())
        if before == 0:
            out[p.name] = 0
            continue
        if backup:
            backup(p)
        img[mask] = 0
        tga.write(p, img)
        out[p.name] = before
    return out


def world_to_grid(x, z, grid=GRID, span=SPAN):
    mpp = span / (grid - 1)
    return int(round((z + span / 2) / mpp)), int(round((x + span / 2) / mpp))


# Structures are never cleared off a road. A bridge IS the road; camps, the
# homestead and wrecks are placed beside it deliberately. A first pass without this
# list deleted the only bridge on the map and the entire gravel bar camp.
STRUCTURE_FILES = {
    'Model_bridges.xml', 'Model_camp.xml', 'Model_gravel bar camp.xml',
    'Model_homestead.xml', 'Model_refuse.xml', 'Model_wrecks.xml',
    'Model_eagle nest.xml',
}


def clear_models(subgroups_dir, mask, backup=True, grid=GRID, span=SPAN,
                 skip=STRUCTURE_FILES):
    """Delete <Model> blocks whose Org lands on the road. Returns {file: (kept, removed)}."""
    org_re = re.compile(r'Org="\(([-\d.eE]+);\s*([-\d.eE]+);\s*([-\d.eE]+)\)"')
    result = {}
    for p in sorted(Path(subgroups_dir).glob('Model_*.xml')):
        if p.name in skip:
            result[p.name] = ('PROTECTED', 0)
            continue
        text = p.read_text(encoding='utf8')
        blocks = re.findall(r'\t<Model\b.*?\n\t</Model>\n|\t<Model\b(?:(?!</?Model).)*?/>\n',
                            text, re.S)
        if not blocks:
            continue
        kept_text = text
        removed = 0
        for b in blocks:
            m = org_re.search(b)
            if not m:
                continue
            x, _y, z = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
            r, c = world_to_grid(x, z, grid, span)
            if 0 <= r < grid and 0 <= c < grid and mask[r, c]:
                kept_text = kept_text.replace(b, '', 1)
                removed += 1
        if removed:
            if backup:
                backup(p)
            p.write_text(kept_text, encoding='utf8')
        result[p.name] = (len(blocks) - removed, removed)
    return result


def set_ignore_overlays(subgroups_dir, value='true', backup=True):
    """Flip IgnoreOverlays on every Distribution -- the engine's own keep-off-roads flag."""
    changed = {}
    for p in sorted(Path(subgroups_dir).glob('Distribution_*.xml')):
        text = p.read_text(encoding='utf8')
        new, n = re.subn(r'IgnoreOverlays="(?:true|false)"',
                         'IgnoreOverlays="%s"' % value, text)
        if n and new != text:
            if backup:
                backup(p)
            p.write_text(new, encoding='utf8')
        changed[p.name] = n
    return changed
