# test_menu_art.py
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent / "fob_projects"))
import menu_art


def _cutout(tmp_path):
    """A transparent render: a 600 x 300 opaque model in a 1000 x 800 frame."""
    img = Image.new("RGBA", (1000, 800), (0, 0, 0, 0))
    img.paste((120, 100, 80, 255), (200, 250, 800, 550))
    src = tmp_path / "cut.png"
    img.save(src)
    return src


def _art(tmp_path):
    out = menu_art.make_menu_art(_cutout(tmp_path), tmp_path / "mod", "plattgate")
    return {p.stem.split("_", 1)[1]: Image.open(p) for p in out}, out


def test_three_transparent_images_at_the_template_sizes(tmp_path):
    art, out = _art(tmp_path)
    assert {k: im.size for k, im in art.items()} == {
        "bg": (380, 92), "bgRight": (760, 90), "bgSmall": (124, 52)}
    assert all(im.mode == "RGBA" for im in art.values())
    assert all(p.parent == tmp_path / "mod" / "ui" / "textures" for p in out)


def test_each_slot_has_its_own_fit(tmp_path):
    art, out = _art(tmp_path)
    box = {k: im.getchannel("A").getbbox() for k, im in art.items()}
    assert box["bg"][0] == 0 and box["bg"][1] == 0 and box["bg"][3] == 92  # fills height, anchored left
    assert box["bgRight"][2:] == (760, 90) and box["bgRight"][0] > 380  # right end only
    assert box["bgSmall"] == (10, 0, 114, 52)                         # whole model, centred, on the bottom
    assert len({p.read_bytes() for p in out}) == 3                    # three images, not one reused


def test_small_chip_shows_the_whole_model_no_bigger_than_vanilla(tmp_path):
    # Vanilla chips show the whole module and fill 41-68% of the chip (measured on all
    # twelve, 2026-10-25). A compact building scaled to touch every edge filled 74-87%
    # and read as far too big in the HUD (owner, Tuck's Rover Works). So: fit the whole
    # model inside the chip, never crop it.
    img = Image.new("RGBA", (1000, 800), (0, 0, 0, 0))
    img.paste((120, 100, 80, 255), (200, 200, 800, 600))    # a solid 3:2 block
    img.paste((255, 0, 0, 255), (200, 200, 260, 600))       # left end red
    img.paste((0, 0, 255, 255), (740, 200, 800, 600))       # right end blue
    img.paste((0, 255, 0, 255), (260, 200, 740, 230))       # top edge green
    src = tmp_path / "block.png"
    img.save(src)
    out = menu_art.make_menu_art(src, tmp_path / "mod", "t")
    chip = Image.open(next(p for p in out if p.stem.endswith("bgSmall"))).convert("RGBA")
    px = [chip.getpixel((x, y)) for x in range(chip.width) for y in range(chip.height)]
    seen = lambda rgb: any(p[3] > 200 and all(abs(p[i] - rgb[i]) < 90 for i in range(3)) for p in px)
    assert seen((255, 0, 0)) and seen((0, 0, 255)) and seen((0, 255, 0)), "model was cropped"
    cover = sum(p[3] > 128 for p in px) / len(px)
    assert cover <= 0.70, cover


def test_grading_keeps_a_warm_model_warm(tmp_path):
    # Per-channel autocontrast stretched the narrow blue range of warm, dark models
    # and turned a dark green garage blue and a black shack purple (2026-10-25).
    img = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    img.paste((84, 78, 62, 255), (50, 50, 350, 250))       # Hank's garage, mean colour
    img.paste((40, 37, 28, 255), (50, 200, 350, 250))      # its shadowed base
    src = tmp_path / "warm.png"
    img.save(src)
    out = menu_art.make_menu_art(src, tmp_path / "mod", "w")
    chip = Image.open(next(p for p in out if p.stem.endswith("bgRight"))).convert("RGBA")
    px = [p for p in chip.getdata() if p[3] > 200]
    r = sum(p[0] for p in px) / len(px); b = sum(p[2] for p in px) / len(px)
    assert r > b, (r, b)
