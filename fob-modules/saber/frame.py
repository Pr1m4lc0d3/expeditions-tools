"""frame.py -- the ONE place that converts the real world into map coordinates.

Every map built from real data has to answer the same question twice: where does
a latitude and longitude land in map metres, and which way round does a raster of
real elevation get written. Those two answers were living in two different files
with two different pieces of code, so a change to one silently unregistered the
other, and the roads walked off the terrain.

They are the same question and this module is the only place either is answered.

    to_map / to_world      lat,lon  <->  map metres
    orient / unorient      a north-up raster <-> a map-frame raster

THE HANDEDNESS FLAG

    EAST_IS_POSITIVE_X = False

A raster fetched from USGS is north-up and west-left, and in that form it matches
the real place exactly -- confirmed by rendering the built height map beside USGS
shaded relief for the same coordinates, where the fins, the road and the town all
land in the same corners. Yet the terrain reads east-west mirrored when it is
DISPLAYED. The flip is between the raster's column order and the engine's X axis,
not in the data, which is why it survived every measurement taken against files.

So it is compensated exactly once, here, at build time. Both the raster and the
coordinates pass through this module, which is what keeps them in register: flip
only the raster and the roads sit on mirrored ground in their un-mirrored places,
which is the state that made the trails show up where there is no trail.

NEVER negate a coordinate to correct a flip after the fact. That is the repair
that has failed every time it was attempted. Change the flag, rebuild the map.

DIAGNOSING A FLIP

    python look_at_map.py level_NAME <lat> <lon>

renders the real aerial, real shaded relief, and the map's terrain in a row. Do
not reach for a correlation coefficient: a map whose left half is correct scores
+0.816 against reality and is still visibly wrong. Look at the pictures.

And render the BUILT height map, never the source tiles -- R16F quantisation puts
12.5 cm stair-steps into every slope reading and hillshade.
"""
from __future__ import annotations

import math

import numpy as np

# The engine draws raster column 0 on the EAST. Set True if that ever changes;
# every raster and every coordinate follows, together, because both go through
# the functions below.
EAST_IS_POSITIVE_X = False

M_PER_DEG_LAT = 111320.0


def _sx() -> float:
    return 1.0 if EAST_IS_POSITIVE_X else -1.0


# --- UTM, which is the frame the elevation service actually renders into ------
#
# The DEM is requested with a GEOGRAPHIC bbox and a UTM imageSR. A lat/lon
# rectangle is a trapezoid in UTM, and the service fits that trapezoid to the
# square pixel grid it was asked for, which stretches the north-south axis by an
# amount that grows with distance from the map centre. Measured against the road
# benches in the lidar: 6 m of offset in the north half of the map and 18 m in
# the south, same sign -- a scale error, not a shift.
#
# An equirectangular to_map cannot match that, and a previous attempt to move
# ONLY the trails to UTM made things worse (9.66 degrees mean slope against
# 5.70) because the terrain was left in the hybrid frame and the two disagreed.
# Both sides have to use the same frame. This is it.

WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
K0 = 0.9996


def utm_zone(lon: float) -> int:
    return int((lon + 180.0) // 6) + 1


def central_meridian(lon: float) -> float:
    return utm_zone(lon) * 6 - 183


def utm(lat: float, lon: float, lon0: float | None = None) -> tuple[float, float]:
    """WGS84 lat/lon -> UTM easting, northing (metres), no false easting."""
    if lon0 is None:
        lon0 = central_meridian(lon)
    e2 = WGS84_F * (2 - WGS84_F)
    ep2 = e2 / (1 - e2)
    lr = math.radians(lat)
    dl = math.radians(lon - lon0)
    N = WGS84_A / math.sqrt(1 - e2 * math.sin(lr) ** 2)
    T = math.tan(lr) ** 2
    C = ep2 * math.cos(lr) ** 2
    A = math.cos(lr) * dl
    M = WGS84_A * ((1 - e2/4 - 3*e2**2/64 - 5*e2**3/256) * lr
                   - (3*e2/8 + 3*e2**2/32 + 45*e2**3/1024) * math.sin(2*lr)
                   + (15*e2**2/256 + 45*e2**3/1024) * math.sin(4*lr)
                   - (35*e2**3/3072) * math.sin(6*lr))
    east = K0 * N * (A + (1 - T + C) * A**3 / 6
                     + (5 - 18*T + T*T + 72*C - 58*ep2) * A**5 / 120)
    north = K0 * (M + N * math.tan(lr)
                  * (A*A/2 + (5 - T + 9*C + 4*C*C) * A**4 / 24
                     + (61 - 58*T + T*T + 600*C - 330*ep2) * A**6 / 720))
    return east, north


def inv_utm(east: float, north: float, lon0: float) -> tuple[float, float]:
    """UTM easting/northing (no false easting) -> WGS84 lat/lon."""
    e2 = WGS84_F * (2 - WGS84_F)
    ep2 = e2 / (1 - e2)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    M = north / K0
    mu = M / (WGS84_A * (1 - e2/4 - 3*e2**2/64 - 5*e2**3/256))
    p1 = (mu + (3*e1/2 - 27*e1**3/32) * math.sin(2*mu)
          + (21*e1**2/16 - 55*e1**4/32) * math.sin(4*mu)
          + (151*e1**3/96) * math.sin(6*mu))
    C1 = ep2 * math.cos(p1) ** 2
    T1 = math.tan(p1) ** 2
    N1 = WGS84_A / math.sqrt(1 - e2 * math.sin(p1) ** 2)
    R1 = WGS84_A * (1 - e2) / (1 - e2 * math.sin(p1) ** 2) ** 1.5
    D = east / (N1 * K0)
    lat = p1 - (N1 * math.tan(p1) / R1) * (
        D*D/2 - (5 + 3*T1 + 10*C1 - 4*C1*C1 - 9*ep2) * D**4 / 24
        + (61 + 90*T1 + 298*C1 + 45*T1*T1 - 252*ep2 - 3*C1*C1) * D**6 / 720)
    lon = lon0 + math.degrees(
        (D - (1 + 2*T1 + C1) * D**3 / 6
         + (5 - 2*C1 + 28*T1 - 3*C1*C1 + 8*ep2 + 24*T1*T1) * D**5 / 120)
        / math.cos(p1))
    return math.degrees(lat), lon


def to_map(lat: float, lon: float, clat: float, clon: float) -> tuple[float, float]:
    """lat,lon -> map metres, origin at the map centre, +z south."""
    lon0 = central_meridian(clon)
    e, nth = utm(lat, lon, lon0)
    ce, cn = utm(clat, clon, lon0)
    return (e - ce) * _sx(), -(nth - cn)


def to_world(x: float, z: float, clat: float, clon: float) -> tuple[float, float]:
    """map metres -> lat,lon. The exact inverse of to_map."""
    lon0 = central_meridian(clon)
    ce, cn = utm(clat, clon, lon0)
    return inv_utm(ce + x * _sx(), cn - z, lon0)


def orient(a: np.ndarray) -> np.ndarray:
    """A north-up, west-left raster -> the map's frame.

    Applied to EVERY raster derived from real data: heights, materials, plant
    density, water. Applying it to some and not others is the same failure as
    flipping the terrain and leaving the roads.
    """
    return a if EAST_IS_POSITIVE_X else a[:, ::-1]


def unorient(a: np.ndarray) -> np.ndarray:
    """The map's frame -> north-up, west-left, for comparing against reality."""
    return orient(a)          # its own inverse; kept separate so intent reads


def describe() -> str:
    d = "east" if EAST_IS_POSITIVE_X else "west"
    return (f"map frame: +x is {d}, +z is south, "
            f"rasters {'kept' if EAST_IS_POSITIVE_X else 'flipped'} left-right")
