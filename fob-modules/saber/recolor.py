"""Recolour the amber FOB paint in a Saber albedo map.

WHY A HUE ROTATION AND NOT A REPAINT
------------------------------------
Saber shipped their own recolour of a FOB texture, and it is the calibration for
everything here. ``models_fob_modular_floor_mat__d`` (amber) and
``models_fob_modular_floor_red_mat__d`` (red) are the same image, measured
2026-10-16 over all 1024x1024 pixels:

    hue delta on chromatic pixels   median -25.7 deg   p10 -27.7   p90 -22.8
    achromatic pixels (grey/metal)  mean |dRGB| = 0.77 / 255   <- untouched
    alpha                           byte-identical

So their method is: rotate hue by a near-constant angle on a saturation-masked
selection, leave everything unsaturated alone, do not touch alpha. That is what
``apply`` does. The +-2.5 deg spread and the 0.77/255 drift are DXT block noise,
not deliberate variation, and they are the tolerance any output here is graded
against (see ``grade``).

THE AMBER BAND IS NOT "YELLOW"
------------------------------
Across the 43 amber-dominated albedo maps used by the 48 FOB module meshes, the
chromatic pixels sit in ONE tight band with a median hue of 15-41 deg -- amber,
not yellow (yellow is 45-70). Ninety-six percent or more of every map's chroma
falls in 12-50 deg. ``BAND`` is that measured envelope. A mask built on it
cannot catch the greys, the metal, the dirt or the blue-grey shadows.

⛔ Do not widen BAND to "catch more". Widening it past 50 deg starts eating the
sand and dry-grass tones that are supposed to stay where they are.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Measured envelope of the FOB amber across all 43 maps. See module docstring.
BAND = (12.0, 50.0)

# Mask floors. Below these a pixel is grey/black as far as the paint is
# concerned, and Saber's own recolour left such pixels alone.
S_FLOOR = 0.25
V_FLOOR = 0.15

# Width of the smooth roll-off at each mask edge. A hard mask leaves stair-step
# seams along the paint boundary that survive DXT compression and read as
# dirty edges in game.
S_SOFT = 0.10
H_SOFT = 8.0


# --------------------------------------------------------------- colour space

def to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """float RGB in 0..1 -> (hue in degrees, saturation, value)."""
    mx = rgb.max(-1)
    mn = rgb.min(-1)
    d = mx - mn
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    h = np.zeros_like(mx)
    safe = np.maximum(d, 1e-9)
    m = (mx == r) & (d > 0)
    h[m] = (((g - b) / safe)[m]) % 6.0
    m = (mx == g) & (d > 0)
    h[m] = (((b - r) / safe) + 2.0)[m]
    m = (mx == b) & (d > 0)
    h[m] = (((r - g) / safe) + 4.0)[m]
    s = np.where(mx > 0, d / np.maximum(mx, 1e-9), 0.0)
    return h * 60.0, s, mx


def to_rgb(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    """(hue in degrees, saturation, value) -> float RGB in 0..1."""
    h = np.mod(h, 360.0) / 60.0
    i = np.floor(h).astype(np.int32)
    f = h - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    i = i % 6
    out = np.empty(h.shape + (3,), dtype=np.float32)
    for k, (rr, gg, bb) in enumerate(
        [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)]
    ):
        m = i == k
        out[m] = np.stack([rr, gg, bb], axis=-1)[m]
    return out


# ---------------------------------------------------------------------- mask

def paint_weight(
    h: np.ndarray, s: np.ndarray, v: np.ndarray, alpha: np.ndarray | None = None
) -> np.ndarray:
    """0..1 weight per pixel: how much of this pixel is FOB paint.

    Smooth at every edge. A binary mask is cheaper and leaves visible seams.
    """
    lo, hi = BAND
    w = np.clip((s - S_FLOOR) / S_SOFT, 0.0, 1.0)
    w *= np.clip((v - V_FLOOR) / 0.05, 0.0, 1.0)
    w *= np.clip((h - (lo - H_SOFT)) / H_SOFT, 0.0, 1.0)
    w *= np.clip(((hi + H_SOFT) - h) / H_SOFT, 0.0, 1.0)
    if alpha is not None:
        w *= np.clip(alpha * 4.0, 0.0, 1.0)
    return w.astype(np.float32)


# -------------------------------------------------------------------- targets

@dataclass(frozen=True)
class Target:
    """One finished colour.

    ``hue`` rotates the paint to that hue, Saber's own method, keeping
    saturation and value. ``neutral`` instead desaturates and remaps value,
    which is the only way to reach black / white / grey -- no rotation can.

    ``sat`` and ``val`` are multipliers applied after the move. They exist
    because a faithful rotation puts blue and purple at a value that reads too
    bright for those hues; amber at V 0.8 is believable, blue at V 0.8 is not.
    Both default to 1.0, which is exactly what Saber did.
    """

    name: str
    hue: float | None = None
    neutral: tuple[float, float] | None = None   # (gain, bias) on value
    sat: float = 1.0
    val: float = 1.0


# 🚨 A FAITHFUL HUE ROTATION IS NOT WHAT SABER DID, AND THE DIFFERENCE IS VISIBLE.
#
# Measured on their red variant, restricted to the PAINTED pixels (the earlier
# whole-image figure of |dV| 0.033 was an average over the 82% of the image that
# is grey, and it hid this completely):
#
#     source amber   hue  31.9   S 0.651   V 0.883   L* 69.6
#     SABER red      hue   6.2   S 0.559   V 0.703   L* 48.9
#     ratio                      S x0.86   V x0.80
#
# So their recolour is a rotation PLUS a darken and a slight desaturate. It is
# not luma-preserving either (L* 69.6 -> 48.9; a faithful rotation lands at
# 55.9). There is no rule to derive from one sample -- it is an art call, and
# the call they made was "go darker".
#
# The reason it is not arbitrary: amber is an intrinsically high-value hue, so
# every other hue carried at V 0.88 reads fluorescent rather than painted. Red
# below is seeded with Saber's MEASURED ratios, which reproduces their variant
# to within 2 deg of hue. The other four carry trims in the same family,
# stronger as the hue moves further from amber.
#
# ⛔ The four non-red hues are NOT measured from anything. Grade them off the
# contact sheet by eye and tune here. ⛔ Do not report them as calibrated.
TARGETS: dict[str, Target] = {
    "red":    Target("red", hue=4.0, sat=0.86, val=0.80),      # Saber's own
    "orange": Target("orange", hue=17.0, sat=1.00, val=0.92),
    "green":  Target("green", hue=104.0, sat=0.88, val=0.78),
    "blue":   Target("blue", hue=214.0, sat=0.90, val=0.76),
    "purple": Target("purple", hue=277.0, sat=0.88, val=0.74),
    "black":  Target("black", neutral=(0.30, 0.02)),
    "white":  Target("white", neutral=(0.26, 0.70)),
    "grey":   Target("grey", neutral=(0.45, 0.22)),
    # eye calls, 2026-10-30, for The Crow's Nest
    "carbon": Target("carbon", neutral=(0.30, 0.12)),            # between black and grey
    "olive":  Target("olive", hue=70.0, sat=0.65, val=0.58),
    "hunter": Target("hunter", hue=22.0, sat=1.00, val=1.00),    # blaze orange
}


# ------------------------------------------------------- what counts as "base"

# 🚨 THE AMBER IS THE WHOLE FOB ART DIRECTION, NOT A BASE COLOUR.
#
# Measured 2026-10-16: 43 of the 92 albedo maps used by the 48 FOB module meshes
# are amber-dominated (>5% chromatic, >80% of that chroma inside BAND). That set
# includes the cardboard boxes, the pallets, the folding chairs, the bare wood,
# the barrels and the toolboxes. Recolouring all 43 gives you blue cardboard and
# purple firewood, and costs 89.2 MB of source per colour.
#
# ⛔ THIS SPLIT IS A PROPOSAL, NOT A MEASUREMENT. Which maps are "the base" is an
# art judgment and it is the owner's call. The numbers above are measured; the
# two lists below are my reading of them and nothing more.

SHELL = {
    # structure, painted panels, awnings, decking -- the parts that read as the
    # module's own bodywork
    "fob_cabin__d_a", "fob_gas_station__d_a", "fob_stock_tent__d",
    "fob_modular_floor_mat__d", "fob_overpass__d_a", "fob_storage__d",
    "fob_base_station__d", "fob_marquee_main__d", "fob_marquee_main_2__d",
    "fob_marquee_details__d", "fob_generator__d", "stripe_01__d",
    "fob_module_repair_zone__d", "fob_extra_storage__d_a", "expedition_tent__d",
    "fob_tent_box__d", "fob_tent_ground_01__d", "fob_tent_ground_02__d",
    "fob_exp_containers__d", "metal_painted_08__d", "girder_02__d",
    "bollards__d",
}

CLUTTER = {
    # cargo, tools, furniture and consumables sitting ON the module. Amber in
    # the vanilla art, but recolouring them is what makes a recolour look like a
    # filter rather than a different model.
    "fob_box_small__d", "fob_big_box__d_a", "box_cardboard_01__d",
    "box_drone_cardboard_drone_01__d", "fob_barrels__d", "barrels_tent__d",
    "fob_toolbox__d", "fob_workbench__d_a", "fob_wheel_02__d", "pallet_01__d",
    "folding_table_dark_01__d", "folding_chair_03__d", "wood02__d",
    "fob_exp_boxes__d", "firefighter_mat__d", "firefighter_decal__d_a",
    "heli_1__d", "heli_2__d", "geodesic_tool_mat__d",
    "generator_module_mat__d", "eg_decals_01_dec01__d_a",
}


# ----------------------------------------------------------------- the recolour

def apply(rgba: np.ndarray, target: Target) -> np.ndarray:
    """Recolour the FOB paint in an RGBA uint8 image. Alpha is never touched."""
    if rgba.dtype != np.uint8:
        raise TypeError(f"expected uint8 RGBA, got {rgba.dtype}")
    has_alpha = rgba.shape[-1] == 4
    rgb = rgba[..., :3].astype(np.float32) / 255.0
    alpha = rgba[..., 3].astype(np.float32) / 255.0 if has_alpha else None

    h, s, v = to_hsv(rgb)
    w = paint_weight(h, s, v, alpha)

    if target.neutral is not None:
        gain, bias = target.neutral
        h2, s2, v2 = h, s * 0.04, v * gain + bias
    elif target.hue is not None:
        # One rotation for the whole map, set so the painted MEDIAN lands on the
        # target. Per-pixel absolute assignment would flatten the variation the
        # artist put in; Saber rotated by a constant and so do we.
        strong = w > 0.5
        src = float(np.median(h[strong])) if strong.any() else float(np.median(BAND))
        h2, s2, v2 = h + (target.hue - src), s, v
    else:
        raise ValueError(f"target {target.name!r} sets neither hue nor neutral")

    s2 = np.clip(s2 * target.sat, 0.0, 1.0)
    v2 = np.clip(v2 * target.val, 0.0, 1.0)

    moved = to_rgb(h2, s2, v2)
    out = rgb * (1.0 - w[..., None]) + moved * w[..., None]
    out = np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)
    if has_alpha:
        return np.concatenate([out, rgba[..., 3:4]], axis=-1)
    return out


# ------------------------------------------------------------------- the gate

def grade(before: np.ndarray, after: np.ndarray) -> dict:
    """Is this recolour as clean as Saber's own? Tolerances from their red.

    ``achromatic_drift`` is the one that matters. Saber's measured 0.77/255.
    Anything much above that means the mask is leaking into the greys, the
    metal or the dirt, and the module will look repainted rather than
    manufactured in another colour.
    """
    rgb_a = before[..., :3].astype(np.float32) / 255.0
    h, s, v = to_hsv(rgb_a)
    alpha = before[..., 3].astype(np.float32) / 255.0 if before.shape[-1] == 4 else None
    w = paint_weight(h, s, v, alpha)
    off = w < 0.01
    d = np.abs(before[..., :3].astype(np.float32) - after[..., :3].astype(np.float32))
    painted = w > 0.5
    _, _, v_after = to_hsv(after[..., :3].astype(np.float32) / 255.0)
    return {
        "painted_fraction": float(painted.mean()),
        "achromatic_drift": float(d[off].mean()) if off.any() else 0.0,
        "painted_change": float(d[painted].mean()) if painted.any() else 0.0,
        "value_drift": float(np.abs(v - v_after)[painted].mean()) if painted.any() else 0.0,
        "alpha_intact": bool(
            before.shape[-1] < 4 or np.array_equal(before[..., 3], after[..., 3])
        ),
    }


SABER_DRIFT = 0.77      # their achromatic mean |dRGB|, the bar to stay under
DRIFT_LIMIT = 2.0       # ours may be noisier; past this the mask is leaking


def verdict(stats: dict) -> tuple[bool, str]:
    if not stats["alpha_intact"]:
        return False, "alpha changed -- it must be carried through byte-identical"
    if stats["achromatic_drift"] > DRIFT_LIMIT:
        return False, (
            f"achromatic drift {stats['achromatic_drift']:.2f}/255 exceeds "
            f"{DRIFT_LIMIT} (Saber's own recolour: {SABER_DRIFT}) -- the mask is "
            f"leaking into greys/metal"
        )
    if stats["painted_fraction"] < 0.005:
        return False, "almost nothing was painted -- wrong map, or BAND missed it"
    return True, "clean"
