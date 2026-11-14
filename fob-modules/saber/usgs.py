"""Fetch real terrain from USGS 3DEP, at the resolution an Expeditions map wants.

Why this rather than the global tile set in ``dem.py``: 3DEP is 1 m bare-earth
lidar over the United States, and at that resolution **roads are already in the
data**. A road is a physical cut-and-fill bench, so it is part of the ground
surface the lidar measured.

Verified, not assumed. Pulling the Moki Dugway (37.2736, -109.9382), a graded
switchback road, and detecting flat ribbons hemmed in by steep ground finds
24,390 m^2 of bench - 4,065 m of road at a 6 m running width, against 4,800 m
documented - spanning 311 m of climb against 366 m documented. No road vector was
involved; that is purely the shape of the ground.

The practical consequence is that gradient, switchback placement, cut depth and
cross-slope benching do not have to be solved. A real road was surveyed by people
who solved them, and the answer is recorded in the terrain.

WATER IS NOT IN HERE, and that is deliberate. USGS hydro-flattening levels every
waterbody of 2 acres or more to a flat plane by specification, and infrared lidar
does not see through water anyway. Measured on the Buffalo River at Ponca: the
valley is captured at 142 m deep with both bluffs, while the water surface varies
by 0.092 m across a 35 x 36 m patch - a plane, not a channel. River and lake beds
come from inland bathymetry, state agency lake surveys or USACE hydrographic
surveys, none of which are in this service.

Outside the United States this returns nothing; use ``dem.fetch_area``.
"""
from __future__ import annotations

import io
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass

import numpy as np

SERVICE = ("https://elevation.nationalmap.gov/arcgis/rest/services/"
           "3DEPElevation/ImageServer")
_UA = {"User-Agent": "expeditions-toolkit/1.0 (map authoring)"}
# The service accepts a single large export; ArcGIS ImageServer's default ceiling
# is 4100 px a side. Chunking BELOW that is actively harmful: each chunk is
# reprojected from its own geographic bbox, so the joins misregister. A 2016 px
# fetch split 2x2 put a seam through the exact centre of a map with a mean height
# step of 1.55 m against 0.11 m for normal adjacent pixels, and a 30.9 m worst case.
MAX_REQUEST_PX = 4000


def utm_epsg(lat: float, lon: float) -> int:
    zone = int((lon + 180.0) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _export(bbox, out_sr, size_px, timeout=180, bbox_sr=4326) -> np.ndarray:
    q = {
        "bbox": ",".join(f"{v:.8f}" for v in bbox),
        "bboxSR": bbox_sr,
        "size": f"{size_px[0]},{size_px[1]}",
        "imageSR": out_sr,
        "format": "tiff",
        "pixelType": "F32",
        "interpolation": "RSP_BilinearInterpolation",
        "f": "image",
    }
    url = SERVICE + "/exportImage?" + urllib.parse.urlencode(q)
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=_UA),
                                 timeout=timeout).read()
    if raw[:4] not in (b"II*\x00", b"MM\x00*"):
        raise RuntimeError(f"service did not return a GeoTIFF "
                           f"({raw[:200]!r})")
    from PIL import Image
    a = np.asarray(Image.open(io.BytesIO(raw)), dtype=np.float64)
    return np.where(np.isfinite(a) & (a > -1e5), a, np.nan)


@dataclass
class Terrain:
    elevation: np.ndarray        # metres, north-up, NaN where no coverage
    lat: float
    lon: float
    span_m: float
    metres_per_px: float

    @property
    def relief(self) -> float:
        return float(np.nanmax(self.elevation) - np.nanmin(self.elevation))

    @property
    def coverage(self) -> float:
        return float(np.isfinite(self.elevation).mean())

    def describe(self) -> str:
        e = self.elevation
        return (f"{self.lat:.5f},{self.lon:.5f}  {e.shape[0]}x{e.shape[1]} px  "
                f"{self.span_m:.0f} m  {self.metres_per_px:.2f} m/px  "
                f"elev {np.nanmin(e):.1f}..{np.nanmax(e):.1f} m  "
                f"relief {self.relief:.1f} m  coverage {100*self.coverage:.1f}%")


def fetch(lat: float, lon: float, span_m: float, resolution_m: float = 1.0) -> Terrain:
    """Square area centred on lat/lon, in metres of elevation.

    Requests are mosaicked because the service refuses a single very large one.
    Tiles are cut in geographic degrees and stitched; over a few kilometres the
    projection difference across the mosaic is far below the vertical precision
    anything downstream cares about.
    """
    want = max(1, int(round(span_m / resolution_m)))
    out_sr = utm_epsg(lat, lon)

    # The bbox is cut in UTM METRES, not in degrees.
    #
    # It used to be cut in degrees with imageSR set to UTM, which asks the
    # service to fit a lat/lon trapezoid into a square pixel grid. That stretches
    # the north-south axis by an amount that GROWS with distance from the centre,
    # and it is measurable: against the road benches in the 1 m lidar, the roads
    # were 6 m out in the north half of the map and 18 m out in the south, same
    # sign. A constant offset would have been the same in both.
    #
    # Cutting in metres makes the raster exactly span_m across on the ground, and
    # frame.to_map converts coordinates through the same projection, so roads and
    # terrain land in one another's frame by construction rather than by luck.
    #
    # frame.utm returns coordinates WITHOUT the false easting, which is right for
    # measuring offsets and wrong for talking to the service: a bbox 500 km west
    # of where it should be silently returns real elevation for the wrong place.
    # It came back with 1737-2086 m of terrain for a 1247-1479 m map and nothing
    # errored, because that is a perfectly good square of Utah.
    from . import frame as _frame
    lon0 = _frame.central_meridian(lon)
    ce, cn = _frame.utm(lat, lon, lon0)
    ce += 500000.0
    if lat < 0:
        cn += 10000000.0
    half = span_m / 2.0
    e0, e1 = ce - half, ce + half
    n0, n1 = cn - half, cn + half

    n_chunks = max(1, math.ceil(want / MAX_REQUEST_PX))
    px = [want // n_chunks] * n_chunks
    for i in range(want - sum(px)):
        px[i] += 1

    rows = []
    for j in range(n_chunks):                      # north -> south
        y1 = n1 - (n1 - n0) * sum(px[:j]) / want
        y0 = n1 - (n1 - n0) * sum(px[:j + 1]) / want
        cols = []
        for i in range(n_chunks):                  # west -> east
            x0 = e0 + (e1 - e0) * sum(px[:i]) / want
            x1 = e0 + (e1 - e0) * sum(px[:i + 1]) / want
            cols.append(_export((x0, y0, x1, y1), out_sr, (px[i], px[j]),
                                bbox_sr=out_sr))
        rows.append(np.hstack(cols))
    grid = np.vstack(rows)
    return Terrain(grid, lat, lon, span_m, span_m / grid.shape[0])


def resample(a: np.ndarray, size: int) -> np.ndarray:
    """Bilinear resample to a square grid, NaN-aware."""
    h, w = a.shape
    yi = np.linspace(0, h - 1, size)
    xi = np.linspace(0, w - 1, size)
    y0 = np.floor(yi).astype(int); y1 = np.minimum(y0 + 1, h - 1)
    x0 = np.floor(xi).astype(int); x1 = np.minimum(x0 + 1, w - 1)
    wy = (yi - y0)[:, None]; wx = (xi - x0)[None, :]
    filled = np.where(np.isfinite(a), a, np.nanmean(a))
    top = filled[np.ix_(y0, x0)] * (1 - wx) + filled[np.ix_(y0, x1)] * wx
    bot = filled[np.ix_(y1, x0)] * (1 - wx) + filled[np.ix_(y1, x1)] * wx
    return top * (1 - wy) + bot * wy


def to_map(terrain: Terrain, tile_px: int, max_height_m: float | None = None,
           floor_m: float | None = None, vertical_scale: float = 1.0):
    """Normalise real elevation onto the 0..1 the engine stores.

    `tile_px` is the map's tile size, because `_height_source` is written at the
    TILE resolution, a quarter of the master's linear size: a 4-block map with
    48 px tiles has a 193 px master and a 48 px source; an 84-block map with 1008
    px tiles has a 4033 px master and a 1008 px source. Writing it at master size
    makes the editor import a quarter of the map stretched over all of it.

    Returns (normalised, chosen_max_height, report).
    """
    z = resample(terrain.elevation, tile_px)
    lo = float(np.nanmin(z)) if floor_m is None else floor_m
    rel = (z - lo) * vertical_scale
    need = float(np.nanmax(rel))
    mh = max_height_m or float(math.ceil(need * 1.05))
    if need > mh:
        raise ValueError(f"relief {need:.1f} m exceeds MaxHeight {mh}; "
                         f"raise it or lower vertical_scale")
    norm = np.clip(rel / mh, 0.0, 1.0).astype(np.float32)
    # the engine stores R16F, whose step is relative: elevation / 2048
    step_cm = 100.0 * (need / 2048.0)
    report = (f"{tile_px}x{tile_px} source, {terrain.span_m/tile_px:.2f} m/px\n"
              f"  real elevation {lo:.1f}..{lo+need/vertical_scale:.1f} m, "
              f"relief {need/vertical_scale:.1f} m"
              + (f" x{vertical_scale:g} -> {need:.1f} m" if vertical_scale != 1 else "")
              + f"\n  MaxHeight {mh:.0f}\n"
              f"  R16F height step at the top of this map: {step_cm:.1f} cm")
    return norm, mh, report
