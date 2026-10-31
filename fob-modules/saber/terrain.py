"""Stitch, write and judge Expeditions terrain.

The tile grid is 4x4. Tiles are named ``{x}_{y}`` (column first). The last row
and column carry one extra pixel of seam, so a 216-px project stitches to 865
and a 1008-px project to 4033 - which is exactly the baked ``height_map.dds``
resolution, and the check that proves the stitch is right.

``measure_drivability`` exists because the interesting question about generated
terrain is never "does it look like terrain" but "can a truck use it". Judging
that by eye failed three times in a row; judging it by slope caught every fault
immediately. A map max slope of 70 degrees is not a defect - a canyon is meant
to have cliffs. The number that matters is the grade along the intended route.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .dds import read_r16f, write_r16f


def tile_names(grid: int = 4) -> list[str]:
    return [f"{x}_{y}" for x in range(grid) for y in range(grid)]


def stitch(data_dir: str | Path, grid: int = 4) -> np.ndarray:
    """Read the tile grid into one array, sized from the tiles themselves."""
    d = Path(data_dir)
    first = read_r16f(d / "0_0" / "_height.dds")
    tile = first.shape[0]
    n = grid * tile + 1
    out = np.zeros((n, n), np.float32)
    for x in range(grid):
        for y in range(grid):
            a = read_r16f(d / f"{x}_{y}" / "_height.dds")
            h, w = a.shape
            out[y * tile:y * tile + h, x * tile:x * tile + w] = a
    return out


def write_tiles(data_dir: str | Path, master: np.ndarray, grid: int = 4) -> int:
    """Slice a master array back into the tile grid. Byte lengths are preserved."""
    d = Path(data_dir)
    tile = (master.shape[0] - 1) // grid
    written = 0
    for x in range(grid):
        for y in range(grid):
            p = d / f"{x}_{y}" / "_height.dds"
            from .dds import read_header
            hdr = read_header(p)
            h, w = hdr["height"], hdr["width"]
            write_r16f(p, master[y * tile:y * tile + h, x * tile:x * tile + w])
            written += 1
    return written


def measure_drivability(master: np.ndarray, max_height_m: float,
                        metres_per_px: float,
                        route: tuple[np.ndarray, np.ndarray] | None = None) -> dict:
    """Slope statistics, and the route grade if a centreline is supplied.

    ``route`` is (xs, ys) in pixel coordinates. Without it you only learn about
    the landscape; with it you learn whether the thing you built is passable,
    which is the only question that decides whether a map works.
    """
    n = master.shape[0]
    gy, gx = np.gradient(master * max_height_m, metres_per_px)
    deg = np.degrees(np.arctan(np.hypot(gx, gy)))
    inner = deg[3:n - 3, 3:n - 3]          # border gradients are one-sided artefacts
    res = {
        "relief_m": (float(master.min() * max_height_m), float(master.max() * max_height_m)),
        "slope_p50": float(np.percentile(inner, 50)),
        "slope_p95": float(np.percentile(inner, 95)),
        "slope_p99": float(np.percentile(inner, 99)),
        "slope_max": float(inner.max()),
        "area_over_25deg_pct": float(100 * (inner > 25).mean()),
        "area_over_35deg_pct": float(100 * (inner > 35).mean()),
    }
    if route is not None:
        xs, ys = route
        ci = np.round(xs).astype(int).clip(0, n - 1)
        ri = np.round(ys).astype(int).clip(0, n - 1)
        r = deg[ri, ci]
        res["route_max_deg"] = float(r.max())
        res["route_mean_deg"] = float(r.mean())
        res["route_over_20deg_pct"] = float(100 * (r > 20).mean())
    return res


def carve_route(master: np.ndarray, xs: np.ndarray, ys: np.ndarray,
                max_cut: float, road_px: float, shoulder_px: float,
                smooth_window: int = 151) -> np.ndarray:
    """Lay a graded route without turning its edges into walls.

    The naive approach - assign the centreline height across the whole corridor
    width - produces a cut face wherever the corridor crosses a side slope,
    because ground uphill of the centreline is forced down to meet it. That
    reads as a clean road on a heightmap and as a cliff in the game.

    Instead the shoulder carries a *relative* shift (how far the road deviates
    from natural ground at the centreline) so the hillside keeps its own shape,
    and only the narrow driving surface is actually flattened.
    """
    n = master.shape[0]
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)

    def smoothstep(e0, e1, v):
        t = np.clip((v - e0) / (e1 - e0 + 1e-9), 0, 1)
        return t * t * (3 - 2 * t)

    ci = np.round(xs).astype(int).clip(0, n - 1)
    ri = np.round(ys).astype(int).clip(0, n - 1)
    znat = master[ri, ci]
    k = np.ones(smooth_window) / smooth_window
    pad = smooth_window // 2
    zc = np.convolve(np.pad(znat, (pad, pad), mode="edge"), k, mode="valid")[:len(znat)]
    zc = np.clip(zc, znat - max_cut, znat + max_cut)
    delta = zc - znat

    accW = np.zeros((n, n), np.float32); accD = np.zeros((n, n), np.float32)
    accRW = np.zeros((n, n), np.float32); accRZ = np.zeros((n, n), np.float32)
    for i in range(len(xs)):
        x0, x1 = int(max(0, xs[i] - shoulder_px)), int(min(n, xs[i] + shoulder_px))
        y0, y1 = int(max(0, ys[i] - shoulder_px)), int(min(n, ys[i] + shoulder_px))
        if x1 <= x0 or y1 <= y0:
            continue
        s = np.hypot(xx[y0:y1, x0:x1] - xs[i], yy[y0:y1, x0:x1] - ys[i])
        w = smoothstep(shoulder_px, 0.0, s)
        newer = w >= accW[y0:y1, x0:x1] - 1e-6
        accD[y0:y1, x0:x1] = np.where(newer, delta[i], accD[y0:y1, x0:x1])
        accW[y0:y1, x0:x1] = np.maximum(accW[y0:y1, x0:x1], w)
        rw = smoothstep(road_px * 2.2, road_px * 0.7, s)
        upd = rw > accRW[y0:y1, x0:x1]
        accRZ[y0:y1, x0:x1] = np.where(upd, zc[i], accRZ[y0:y1, x0:x1])
        accRW[y0:y1, x0:x1] = np.where(upd, rw, accRW[y0:y1, x0:x1])

    out = master + accD * accW
    out = out * (1 - accRW) + accRZ * accRW
    return np.clip(out, 0.02, 0.98)


def smooth(master: np.ndarray, passes: int = 4) -> np.ndarray:
    n = master.shape[0]
    h = master
    for _ in range(passes):
        p = np.pad(h, 1, mode="edge")
        h = ((p[0:n, 0:n] + p[0:n, 1:n+1] + p[0:n, 2:n+2] +
              p[1:n+1, 0:n] + p[1:n+1, 1:n+1] * 2 + p[1:n+1, 2:n+2] +
              p[2:n+2, 0:n] + p[2:n+2, 1:n+1] + p[2:n+2, 2:n+2]) / 10.0).astype(np.float32)
    return h
