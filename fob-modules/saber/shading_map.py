"""The engine's ``__sh_d`` shading-map convention: measure, judge, repair.

A glTF packs occlusion / roughness / metalness into one image, and the tools
here used to pass that straight through as ``__sh_d``. It is not the same
convention, and when the source leaves a channel empty the result is an image
the engine never ships.

WHAT WAS MEASURED, 2026-10-11
-----------------------------
500 shipped ``*__sh_d.dds`` sampled out of the 1,453 in ``editor.pak``
(``D:\\Games\\ExpeditionsAMudRunnerGame\\...\\editor.pak``), per-image channel
means:

======  ======  ======  ======  ======  ======
 chan    mean     min     max     p01     p50
======  ======  ======  ======  ======  ======
  R       19.6     0.0   255.0     0.0     0.3
  G      171.0     1.2   255.0    62.6   174.2
  B      207.6    12.4   255.0    64.4   220.2
======  ======  ======  ======  ======  ======

  images with R mean < 5 :  291 / 500
  images with G mean < 5 :    1 / 500
  images with B mean < 5 :    0 / 500

⛔ **R = 0 IS NORMAL.** 291 of 500 shipped maps have an essentially empty red
channel. An earlier note recorded "R and B are entirely zero" as the anomaly in
a Meshy-derived map; half of that was a false lead, and chasing it would have
cost a build. Only the blue channel is the outlier.

🧭 Blue is never dead. The lowest of all 500 is ``models_desert_watchtower_
interior__sh_d`` at 12.4, and that it is an *interior* is consistent with blue
carrying an occlusion-like term -- but ⚠ **that reading is NOT proven** and must
not be written down as the cause of anything. What is established is the
envelope, not the meaning.

WHY A DEAD CHANNEL IS TREATED AS AN ERROR AND NOT A STYLE
---------------------------------------------------------
A Meshy ``__sh_d`` passed through from glTF metallic-roughness measured
``R 0.0  G 204.0  B 0.0``. R 0.0 is ordinary; B 0.0 is outside 500 of 500
shipped assets. The model was rendering black at the time.

🚨 **AND THE BLUE CHANNEL IS NOT WHAT FIXED IT. MEASURED 2026-10-11.**
``pebblegas_station_bakedmaterial__sh_d.tga`` -- the texture on the model that
is CONFIRMED RENDERING CORRECTLY IN THE GAME, photographed on the terrain,
textured and lit -- still measures ``R 0.0  G 204.0  B 0.0`` today. An empty
blue channel renders fine. Whatever cured the black render was one of the other
things that changed in that build (the TGA re-encode from Blender's type-10 RLE
to the engine's type-2 uncompressed, the grounding, the rotation, the repack),
and ⛔ the blue channel must not be written down as the texture fix in any
guide, commit message or tool.

⇒ :func:`repair` therefore exists but is **NOT** applied by default anywhere.
``glb_to_fbx.py`` reports the deviation and leaves the pixels alone; rewriting
them would alter the only asset ever confirmed working in this game in exchange
for a hypothesis its own texture already refutes. ``--fix-shading`` is there for
running that experiment deliberately, as a single-variable test, which is the
only way the question gets answered.

THE REPAIR
----------
A channel counts as *dead* only when it is globally empty -- ``max <= 8`` -- not
merely dark. That distinction matters: the darkest shipped map has a blue mean
of 12.4 but a live channel underneath, and a mean-only test would rewrite it.
A dead blue channel is filled with 255, the neutral end of the observed range,
because the source carried no information there and a neutral fill invents less
than any fitted value would.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

#: Images sampled for the constants above.
SAMPLE_N = 500

#: Per-channel mean of the shipped sample, in ``R G B`` order.
SHIPPED_MEAN = (19.6, 171.0, 207.6)

#: Lowest per-image channel mean seen in the shipped sample.
SHIPPED_MIN_MEAN = (0.0, 1.2, 12.4)

#: A channel whose maximum is at or below this is empty, not dark.
DEAD_MAX = 8

#: Value written into a dead blue channel. Neutral end of the observed range.
NEUTRAL_BLUE = 255

#: Channels that must never be empty, measured. Red legitimately is.
REQUIRED = ("B",)

_CHAN = {"R": 0, "G": 1, "B": 2}


def is_shading_map(name: str) -> bool:
    """True for a filename the engine treats as a shading map."""
    stem = str(name).rsplit(".", 1)[0]
    return stem.endswith("__sh_d")


def channel_stats(img: Image.Image) -> dict:
    """Per-channel ``mean`` and ``max`` of *img*, keyed ``R`` / ``G`` / ``B``."""
    a = np.asarray(img.convert("RGB"), dtype=np.float32).reshape(-1, 3)
    return {
        ch: {"mean": float(a[:, i].mean()), "max": float(a[:, i].max())}
        for ch, i in _CHAN.items()
    }


def dead_channels(img: Image.Image) -> list:
    """Channels that are empty rather than dark, worst-first by index."""
    st = channel_stats(img)
    return [ch for ch in ("R", "G", "B") if st[ch]["max"] <= DEAD_MAX]


def verdict(img: Image.Image):
    """``(ok, reasons)`` -- a shading map the engine's own data would vouch for.

    Only :data:`REQUIRED` channels can fail. A dead red channel is reported as
    informational because 291 of 500 shipped maps have one.
    """
    dead = dead_channels(img)
    reasons = []
    ok = True
    for ch in dead:
        if ch in REQUIRED:
            ok = False
            reasons.append(
                "%s channel is empty (max <= %d); 0 of %d shipped __sh_d "
                "textures have one" % (ch, DEAD_MAX, SAMPLE_N)
            )
        else:
            reasons.append(
                "%s channel is empty -- normal, %d of %d shipped maps are too"
                % (ch, 291, SAMPLE_N)
            )
    return ok, reasons


def from_gltf_orm(img: Image.Image):
    """Reorder a glTF occlusion/roughness/metalness image into the engine's layout.

    Saber's material reference (expeditions-guides.saber.games, CombineXMesh >
    Material > ShadingMap): "R - metalness", "G - roughness", "B - ambient
    occlusion mask". glTF packs R occlusion, G roughness, B metalness, so a
    straight copy swaps metal and occlusion. Measured 2026-10-24 on a Meshy
    Smart Topology model: R 254 G 120 B 5 passed through, which the engine reads
    as fully metal and fully occluded, and the model rendered black.

    An empty glTF occlusion channel means "no occlusion data", so it becomes 255
    (not occluded). That keeps the older Meshy remesh maps, (0, 204, 0) in glTF
    order, coming out as the (0, 204, 255) PebbleGas ships. Returns
    ``(image, note)``.
    """
    a = np.asarray(img.convert("RGB"), dtype=np.uint8)
    occ, rough, metal = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    if occ.max() <= DEAD_MAX:
        occ = np.full_like(occ, NEUTRAL_BLUE)
    out = np.dstack([metal, rough, occ])
    note = ("__sh_d reordered from glTF (occlusion, roughness, metalness) to "
            "the engine's (metalness, roughness, occlusion)")
    return Image.fromarray(out, "RGB"), note


def repair(img: Image.Image):
    """Fill empty required channels. Returns ``(image, notes)``.

    Channels that carry data are never touched, and an image that already
    passes :func:`verdict` is returned unchanged.
    """
    ok, _ = verdict(img)
    if ok:
        return img, []

    rgb = img.convert("RGB")
    a = np.asarray(rgb, dtype=np.uint8).copy()
    notes = []
    for ch in dead_channels(rgb):
        if ch not in REQUIRED:
            continue
        a[:, :, _CHAN[ch]] = NEUTRAL_BLUE if ch == "B" else NEUTRAL_BLUE
        notes.append(
            "__sh_d %s channel was empty -> filled %d (neutral); no shipped "
            "map has an empty %s" % (ch, NEUTRAL_BLUE, ch)
        )
    return Image.fromarray(a, "RGB"), notes
