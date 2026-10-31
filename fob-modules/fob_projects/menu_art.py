"""Compose a module's three menu images from one transparent model render.

Follows the Guide's module-art template (Guide/assets/downloads/
expeditions-module-art-template/README.txt): one image per slot, each with its own
fit, each a transparent PNG carrying only the model. The game
draws the row, card and bar behind it.

    bg      380 x 92   fills the height, anchored LEFT like the vanilla rows (owner, 2026-10-24)
    bgRight 760 x 90   fills the height, aligned RIGHT; icon and name cover the left end
    bgSmall 124 x 52   the whole model fitted inside the chip, never cropped (vanilla fills 41-68%)

No popup image: the game loads mod PNGs only for these three fields
(see platts_gate_works.py), so popupModuleBg cannot take a custom file.

The render comes from render_cutout.py. pack_fob_module.py ships
ui/textures/*.png, and the descriptor names each by its bare basename.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

SIZES = {"bg": (380, 92), "bgRight": (760, 90), "bgSmall": (124, 52)}
SATURATION = 1.4        # Meshy skins are dull (sat ~50); vanilla module art sits near 105
GAMMA = 0.8             # lifts mid-tones toward vanilla art's brightness (mean value ~150)


def _trim(img: Image.Image) -> Image.Image:
    return img.crop(img.getchannel("A").getbbox())


def _grade(img: Image.Image) -> Image.Image:
    """Stretch contrast over the opaque pixels and lift colour toward vanilla art."""
    alpha = img.getchannel("A")
    # preserve_tone: stretch on brightness, not per channel, or warm dark models turn blue
    rgb = ImageOps.autocontrast(img.convert("RGB"), cutoff=1, preserve_tone=True,
                                mask=alpha.point(lambda a: 255 if a > 200 else 0))
    rgb = rgb.point(lambda v: round(255 * (v / 255) ** GAMMA))
    rgb = ImageEnhance.Color(rgb).enhance(SATURATION)
    rgb.putalpha(alpha)
    return rgb


def _scaled(img: Image.Image, scale: float) -> Image.Image:
    return img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)


def _fill_height(model: Image.Image, size: tuple[int, int], anchor: str) -> Image.Image:
    w, h = size
    m = _scaled(model, h / model.height)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    x = {"right": w - m.width, "left": 0}.get(anchor, (w - m.width) // 2)
    canvas.alpha_composite(m, (x, 0)) if x >= 0 else canvas.alpha_composite(m.crop((-x, 0, m.width, h)), (0, 0))
    return canvas


def _contain(model: Image.Image, size: tuple[int, int]) -> Image.Image:
    """The whole model, fitted inside the chip, centred and standing on its bottom edge.

    Vanilla chips show the whole module and fill 41-68% of the chip. Scaling a
    compact building to touch every edge filled 74-87% and read as far too big in
    the HUD (owner, 2026-10-25, Tuck's Rover Works); a 1.6x zoom before that showed
    only one corner of Doc's base. Fit it, never crop it.
    """
    w, h = size
    m = _scaled(model, min(w / model.width, h / model.height))
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(m, ((w - m.width) // 2, h - m.height))
    return canvas


def make_menu_art(cutout: Path, mod_dir: Path, prefix: str) -> list[Path]:
    model = _grade(_trim(Image.open(cutout).convert("RGBA")))
    images = {
        "bg": _fill_height(model, SIZES["bg"], "left"),
        "bgRight": _fill_height(model, SIZES["bgRight"], "right"),
        "bgSmall": _contain(model, SIZES["bgSmall"]),
    }
    out_dir = mod_dir / "ui" / "textures"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for key, img in images.items():
        p = out_dir / f"{prefix}_{key}.png"
        img.save(p)
        written.append(p)
    return written


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cutout", type=Path, help="transparent render from render_cutout.py")
    ap.add_argument("mod", type=Path)
    ap.add_argument("prefix")
    a = ap.parse_args()
    for p in make_menu_art(a.cutout, a.mod, a.prefix):
        print(f"  {p}")
