"""Letter a blank sign and install it as a new model: no Blender, no converter.

    python make_sign.py list                                     every blank and what it takes
    python make_sign.py speed_limit_blank 40                     -> signs_custom_speed_limit_40
    python make_sign.py street_blank_ave "Lincoln" --name signs_custom_lincoln_ave
    python make_sign.py street_blank_st Maple Oak --corner        4-way corner: two blades at right angles
    python make_sign.py street_blank Elm --control stop           a stop sign under the blade (or yield)
    python make_sign.py height_limit_blank 13 6                  two values: feet, inches
    python make_sign.py brown_blank_wide_a "BEAR CREEK|CAMPGROUND"   | breaks a line
    python make_sign.py preview out.jpg                          every blank lettered with its sample

A blank and a lettered sign differ only in the face picture: same plate, same
mesh, same material name. So a new sign is a copy of the blank's FBX, a new face
texture, and the blank's XML pointing at it; the editor converts it on first
load like any model. Text is set in Bahnschrift (Windows 10/11) or DejaVu Sans
Condensed Bold, fitted to the blank's text area (sign_blanks.json), toned by the
plate's own weathering so it wears with it, and printed marks a value replaces
(the fill-in line on WEIGHT LIMIT, "__ FT __ IN") are painted out first.

Needs Python 3 with Pillow, numpy and opencv-python.
"""
from __future__ import annotations

import argparse
from itertools import combinations
import json
import os
import re
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
LAYOUTS = json.loads((HERE / "sign_blanks.json").read_text(encoding="utf8"))
COLORS = {"black": (28, 27, 26), "white": (238, 235, 228)}
FONTS = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "bahnschrift.ttf",
         Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "DejaVuSansCondensed-Bold.ttf",
         HERE / "DejaVuSansCondensed-Bold.ttf"]
SS = 4   # supersampling for the text mask


def font(size: int) -> ImageFont.FreeTypeFont:
    for f in FONTS:
        if f.is_file():
            ft = ImageFont.truetype(str(f), size)
            if f.name == "bahnschrift.ttf":
                ft.set_variation_by_name("Bold Condensed")
            return ft
    raise SystemExit("no sign font: needs Bahnschrift (Windows 10/11) or DejaVuSansCondensed-Bold.ttf beside this script")


GAP = 0.18


def fit(lines: list[str], W: int, H: int) -> int:
    """The largest font size at which the lines fit W x H."""
    gap = GAP
    lo, hi = 4, H
    while lo < hi:   # largest size whose block fits
        mid = (lo + hi + 1) // 2
        f = font(mid)
        boxes = [f.getbbox(t) for t in lines]
        bw = max(b[2] - b[0] for b in boxes)
        bh = mid * (len(lines) + gap * (len(lines) - 1))
        if bw <= W and bh <= H:
            lo = mid
        else:
            hi = mid - 1
    return lo


def wraps(text: str, most: int) -> list[list[str]]:
    """Every way to break text at its spaces into 1..most lines."""
    words = text.split()
    out = [[" ".join(words)]]
    for k in range(2, min(most, len(words)) + 1):
        for cut in combinations(range(1, len(words)), k - 1):
            b = (0, *cut, len(words))
            out.append([" ".join(words[b[i]:b[i + 1]]) for i in range(k)])
    return out


def text_mask(lines: list[str], w: int, h: int) -> np.ndarray:
    """Coverage 0..1 of the lines, as large as fits w x h, centred."""
    W, H = w * SS, h * SS
    gap = GAP
    lo = fit(lines, W, H)
    f = font(lo)
    im = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(im)
    block = lo * (len(lines) + gap * (len(lines) - 1))
    asc, desc = f.getmetrics()
    y = (H - block) / 2
    for t in lines:
        b = f.getbbox(t)
        # centre on the caps: the glyph box of capitals, not the line's full ascent
        d.text(((W - (b[2] - b[0])) / 2 - b[0], y - b[1] + (lo - (b[3] - b[1])) / 2), t, fill=255, font=f)
        y += lo * (1 + gap)
    return np.asarray(im.resize((w, h), Image.LANCZOS), np.float32) / 255


def px(rect, w, h):
    return round(rect[0] * w), round(rect[1] * h), round(rect[2] * w), round(rect[3] * h)


def letter(face: Image.Image, blank: str, values: list[str]) -> Image.Image:
    lay = LAYOUTS[blank]
    img = np.array(face.convert("RGB"))
    h, w = img.shape[:2]
    if "clear" in lay:
        # paint out printed marks: pixels far from the plate's own colour in that band
        x0, y0, x1, y1 = px(lay["clear"], w, h)
        band = img[y0:y1, x0:x1].astype(np.float32)
        plate = np.median(band.reshape(-1, 3), 0)
        marks = (np.abs(band - plate).sum(2) > 90).astype(np.uint8)
        marks = cv2.dilate(marks, np.ones((7, 7), np.uint8))
        # the printed line carries a pale halo a few pixels wide; left in, the fill
        # borrowed it and painted a white strip where the line was (2026-10-30)
        near = cv2.dilate(marks, np.ones((15, 11), np.uint8)).astype(bool)
        marks = cv2.dilate((marks.astype(bool) | (near & ((band - plate).sum(2) > 10))).astype(np.uint8),
                           np.ones((3, 3), np.uint8)).astype(bool)
        # Fill each mark pixel with a real plate pixel from the same row, outside the
        # marks: the plate's own grain, no smear. Inpainting left a smooth smear, and
        # borrowing grain from the band above copied the edges of the lettering there
        # ("LIMIT", "LOW CLEARANCE") into the cleared strip as pale ghosts.
        rng = np.random.default_rng(0)
        band = img[y0:y1, x0:x1].copy()
        # A mark that spans the whole band (WEIGHT LIMIT's fill-in line) leaves its
        # rows no plate pixel to borrow. Skipping them left the line printed under
        # the number; filling them from one row left a flat, grainless strip
        # (2026-10-30). Those rows take a real strip of plate from just above the
        # line instead, shifted by the line's height, so the grain carries on.
        wide = [(~marks[r]).sum() < marks.shape[1] // 10 for r in range(band.shape[0])]
        r = 0
        while r < len(wide):
            if not wide[r]:
                r += 1
                continue
            end = r
            while end < len(wide) and wide[end]:
                end += 1
            shift = end - r + 2
            for q in range(r, end):
                src = y0 + q - shift if y0 + q - shift >= 0 else y0 + q + shift
                band[q, marks[q]] = img[src, x0:x1][marks[q]]
            r = end
        part = [not wd for wd in wide]
        for r in range(band.shape[0]):
            hole = np.nonzero(marks[r])[0]
            free = np.nonzero(~marks[r])[0]
            if part[r] and len(hole) and len(free):
                band[r, hole] = band[r, rng.choice(free, len(hole))]
        soft = cv2.GaussianBlur(band, (0, 0), 0.7)
        band = np.where((marks & np.array(part)[:, None])[..., None], soft, band)
        img[y0:y1, x0:x1] = band
    text = lay["format"].format(*values) if "format" in lay else " ".join(values)
    lines = [t.strip().upper() for t in text.split("|") if t.strip()][:lay.get("lines", 1)]
    x0, y0, x1, y1 = px(lay["rect"], w, h)
    # the texture is stretched to a power of two; draw at the plate's true
    # proportions and stretch the text with it, or each plate distorts it differently
    stretch = lay["aspect"] / (w / h)
    tw = max(4, round((x1 - x0) * stretch))
    if "|" not in text and lay.get("lines", 1) > 1:
        # no line breaks given: break where the letters come out biggest
        # ("TRUCKS ENTERING" on one line of a diamond was tiny, 2026-10-30)
        lines = max(wraps(lines[0], lay["lines"]), key=lambda ls: fit(ls, tw * SS, (y1 - y0) * SS))
    cov = np.asarray(Image.fromarray((text_mask(lines, tw, y1 - y0) * 255).astype(np.uint8))
                     .resize((x1 - x0, y1 - y0), Image.LANCZOS), np.float32) / 255
    region = img[y0:y1, x0:x1].astype(np.float32)
    # wear: the plate's light/dark variation under the text, softened, carried into the ink
    lum = region.mean(2)
    wear = np.clip((lum / max(np.median(lum), 1)) ** 0.5, 0.75, 1.15)[..., None]
    ink = np.array(COLORS[lay["color"]], np.float32) * wear
    cov = cv2.GaussianBlur(cov, (0, 0), 0.5)[..., None]
    img[y0:y1, x0:x1] = np.clip(region * (1 - cov) + ink * cov, 0, 255).astype(np.uint8)
    return Image.fromarray(img)


def media_root(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    return Path(os.environ.get("USERPROFILE", "~")).expanduser() / "Documents" / "My Games" / "Expeditions" / "Media"


# Every blank is built on a steel post; sign_posts.py adds the same plate on a
# wood post as signs_custom_<blank>_woodpost, so a sign can be lettered on either.
POSTS = ("steel", "wood")
TWIN = {"steel": "", "wood": "_woodpost"}


def find_blank(media: Path, blank: str, post: str = "steel", control: str = "none") -> str:
    """The installed brand of a blank on the chosen post: signs_custom_<blank>[_<stop|yield>][_woodpost]."""
    brand = f"signs_custom_{blank}{ctl(control)}{TWIN[post]}"
    if not (media / "meshes" / "models" / f"{brand}.xml").is_file():
        raise SystemExit(f"{blank} on a {post} post is not installed in {media}. "
                         "Install (or reinstall) Expeditions Sign Maker.")
    return brand


# The sign package each blank belongs with. A lettered sign is named
# signs_<category>_custom_..., so the editor's model search finds it by its
# category ("warning" lists the Warning pack AND your lettered diamonds) and by
# "custom" (every lettered sign). The search matches anywhere in the name.
CATEGORY = {
    "warning": ("blank_diamond_yellow", "blank_diamond_orange"),
    "regulatory": ("speed_limit_blank", "weight_limit_blank", "height_limit_blank", "low_clearance_blank",
                   "white_blank_square", "white_blank_plate"),
    "park": ("brown_blank_wide_a", "brown_blank_wide_b", "brown_blank_plate_wide", "brown_blank_square",
             "brown_blank_plate"),
    "route": ("us_route_blank", "interstate_blank", "state_route_blank", "county_route_blank",
              "green_route_blank", "mile_blank", "montana_blank", "route_blank", "county_blank",
              "state_outline_blank", "green_blank_plate", "green_blank_plate_tall", "street_blank",
              "street_blank_st", "street_blank_ave", "street_blank_rd"),
}


def category(blank: str) -> str:
    for cat, blanks in CATEGORY.items():
        if blank in blanks:
            return cat
    raise SystemExit(f"{blank} has no category in make_sign.CATEGORY")


def default_name(blank: str, values: list[str], post: str = "steel") -> str:
    """The model name a lettered sign gets unless one is given:
    speed_limit_blank + 40 -> signs_regulatory_custom_speed_limit_40, and _wood on
    the end when it stands on a wood post."""
    src = f"signs_custom_{blank}"
    slug = re.sub(r"[^a-z0-9]+", "_", "_".join(values).lower()).strip("_")
    base = src.rsplit('_blank', 1)[0].replace("signs_custom", f"signs_{category(blank)}_custom", 1)
    tail = src.split('_blank', 1)[1] if '_blank_' in src else ''
    return f"{base}_{slug}{tail}" + ("_wood" if post == "wood" else "")


def searchable(blank: str, name: str) -> str:
    """A name the user typed, made findable: it keeps signs_<category>_custom_ in front."""
    head = f"signs_{category(blank)}_custom_"
    return name if name.startswith(head) else head + re.sub(r"^signs_(?:\w+?_)?custom_", "", name)


def blank_face(media: Path, blank: str, post: str = "steel", control: str = "none") -> tuple[str, str, Image.Image]:
    """(installed brand, face texture path, face picture) of an installed blank."""
    src = find_blank(media, blank, post, control)
    mesh_xml = (media / "meshes/models" / f"{src}.xml").read_text(encoding="utf8")
    face_rel = re.search(rf'Name="face_{re.escape(blank)}"[^>]*AlbedoMap="([^"]+)"', mesh_xml).group(1)
    return src, face_rel, Image.open(media / "textures" / face_rel)


def install(media: Path, blank: str, values: list[str], name: str | None, post: str = "steel",
            control: str = "none") -> str:
    if control != "none" and blank not in STREETS:
        raise SystemExit(f"--control is for the street blanks only: {', '.join(STREETS)}")
    src, face_rel, face = blank_face(media, blank, post, control)
    name = (searchable(blank, name) if name else
            default_name(blank, values) + ctl(control) + ("_wood" if post == "wood" else "") if control != "none"
            else default_name(blank, values, post))
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise SystemExit("--name may hold lowercase letters, digits and _ only")
    mesh_xml = (media / "meshes/models" / f"{src}.xml").read_text(encoding="utf8")
    out_rel = f"models/{name}_face__d.tga"
    letter(face, blank, values).save(media / "textures" / out_rel)
    (media / "meshes/models" / f"{name}.xml").write_text(mesh_xml.replace(face_rel, out_rel), encoding="utf8")
    shutil.copyfile(media / "meshes/models" / f"{src}.fbx", media / "meshes/models" / f"{name}.fbx")
    shutil.copyfile(media / "classes/models" / f"{src}.xml", media / "classes/models" / f"{name}.xml")
    return name



# 4-way corner (owner 11-09): two street blades at right angles on one post, built by sign_corner.py as
# signs_custom_<street blank>_corner[_woodpost]. Its second blade's face is face_<blank>_cross.
CORNER = "_corner"
STREETS = ("street_blank", "street_blank_st", "street_blank_ave", "street_blank_rd")
# controlled crossings (owner 11-12): the blades with a stop or yield sign under them on the same post,
# built by sign_control.py as signs_custom_<street blank>[_corner]_<stop|yield>[_woodpost]
CONTROLS = ("none", "stop", "yield")


def ctl(control: str) -> str:
    """The name part for a stop or yield sign under the blades: "" for none."""
    if control not in CONTROLS:
        raise SystemExit(f"--control is one of {', '.join(CONTROLS)}")
    return "" if control == "none" else f"_{control}"


def install_corner(media: Path, blank: str, values: list[str], name: str | None, post: str = "steel",
                   control: str = "none") -> str:
    """Letter a corner blank: values[0] on the first blade, values[1] on the one turned across it."""
    if blank not in STREETS:
        raise SystemExit(f"--corner is for the street blanks only: {', '.join(STREETS)}")
    src = f"signs_custom_{blank}{CORNER}{ctl(control)}{TWIN[post]}"
    if not (media / "meshes" / "models" / f"{src}.xml").is_file():
        raise SystemExit(f"the 4-way corner {blank} on a {post} post is not installed in {media}. "
                         "Install (or reinstall) Expeditions Sign Maker.")
    name = searchable(blank, name) if name else default_name(blank, values) + CORNER + ctl(control) + ("_wood" if post == "wood" else "")
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise SystemExit("--name may hold lowercase letters, digits and _ only")
    mesh_xml = (media / "meshes/models" / f"{src}.xml").read_text(encoding="utf8")
    # both faces may share one texture file, so each Material element is rewritten on its own
    for i, (mat, value) in enumerate(((f"face_{blank}", values[0]), (f"face_{blank}_cross", values[1])), 1):
        el = re.search(rf'<Material [^>]*Name="{re.escape(mat)}"[^>]*/>', mesh_xml).group(0)
        face_rel = re.search(r'AlbedoMap="([^"]+)"', el).group(1)
        out_rel = f"models/{name}_face{i}__d.tga"
        letter(Image.open(media / "textures" / face_rel), blank, [value]).save(media / "textures" / out_rel)
        mesh_xml = mesh_xml.replace(el, el.replace(f'AlbedoMap="{face_rel}"', f'AlbedoMap="{out_rel}"'))
    (media / "meshes/models" / f"{name}.xml").write_text(mesh_xml, encoding="utf8")
    shutil.copyfile(media / "meshes/models" / f"{src}.fbx", media / "meshes/models" / f"{name}.fbx")
    shutil.copyfile(media / "classes/models" / f"{src}.xml", media / "classes/models" / f"{name}.xml")
    return name


def lettered(media: Path) -> list[str]:
    """The signs lettered from the blanks: signs_<category>_custom_*, and the older
    signs_custom_* names. Never a blank itself."""
    blanks = {f"signs_custom_{k}{t}" for k in LAYOUTS if not k.startswith("_") for t in TWIN.values()}
    blanks |= {f"signs_custom_{k}{c}{ctl(x)}{t}" for k in STREETS for c in ("", CORNER) for x in CONTROLS
               for t in TWIN.values()}
    found = {x.stem for x in (media / "classes/models").glob("signs_*custom_*.xml")}
    return sorted(n for n in found if n not in blanks and re.fullmatch(r"signs_(?:[a-z]+_)?custom_\w+", n))


def remove(media: Path, name: str) -> None:
    """Delete a lettered sign and the editor's converted copy of it. Never a blank."""
    if name not in lettered(media):
        raise SystemExit(f"{name} is not a lettered sign")
    gone = [media / "meshes/models" / f"{name}.fbx", media / "meshes/models" / f"{name}.xml",
            media / "classes/models" / f"{name}.xml", *(media / "textures/models").glob(f"{name}_face*__d.tga"),
            media / "RebuildLog" / f"models_{name}.log",
            *(media / "prebuild/meshes").glob(f"models_{name}"), *(media / "prebuild/meshes").glob(f"models_{name}.*"),
            *(media / "prebuild/textures/dds").glob(f"models_{name}_face*__d.dds")]
    for f in gone:
        if f.is_dir():
            shutil.rmtree(f)
        elif f.exists():
            f.unlink()

def preview(faces: Path, out: Path) -> None:
    signs = {s["name"]: s for s in json.loads((faces.parent / "signs.json").read_text())}
    C, cols = 300, 6
    names = [k for k in LAYOUTS if not k.startswith("_")]
    rows = (len(names) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * C, rows * (C + 16)), (70, 70, 70))
    d = ImageDraw.Draw(sheet)
    for i, k in enumerate(names):
        im = letter(Image.open(faces / f"{k}.png"), k, LAYOUTS[k]["sample"].split("|")
                    if "format" in LAYOUTS[k] else [LAYOUTS[k]["sample"]])
        s = signs[k]
        sc = (C - 10) / max(s["w"], s["h"])
        im = im.resize((round(s["w"] * sc), round(s["h"] * sc)), Image.LANCZOS)
        x, y = i % cols * C, i // cols * (C + 16)
        sheet.paste(im, (x + 5, y + 5))
        d.text((x + 3, y + C + 1), k, fill=(255, 255, 255))
    sheet.save(out, quality=90)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("blank", help="a blank's name, or 'list', or 'preview'")
    ap.add_argument("values", nargs="*")
    ap.add_argument("--name", help="brand name for the new sign (default: made from the text)")
    ap.add_argument("--post", choices=POSTS, default="steel", help="the post it stands on (default steel)")
    ap.add_argument("--corner", action="store_true",
                    help="street blanks: a 4-way corner, two blades at right angles; give both street names")
    ap.add_argument("--control", choices=CONTROLS, default="none",
                    help="street blanks: a stop or yield sign on the post under the blades (default none)")
    ap.add_argument("--media", help="the Media folder (default: Documents\\My Games\\Expeditions\\Media)")
    ap.add_argument("--faces", default=str(HERE / "faces"), help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.blank == "list":
        for k, v in LAYOUTS.items():
            if not k.startswith("_"):
                n = v["format"].count("{") if "format" in v else 1
                print(f"  {k:24s} {n} value{'s' if n > 1 else ''}, e.g. {v['sample'].replace('|', ' ')}")
        return 0
    if a.blank == "preview":
        preview(Path(a.faces), Path(a.values[0] if a.values else "blanks_preview.jpg"))
        return 0
    if a.blank not in LAYOUTS:
        raise SystemExit(f"no blank called {a.blank}; run: python make_sign.py list")
    if a.corner:
        if len(a.values) != 2:
            raise SystemExit("--corner takes two street names: the first blade's, then the one across it")
        print("installed", install_corner(media_root(a.media), a.blank, a.values, a.name, a.post, a.control))
        return 0
    need = LAYOUTS[a.blank]["format"].count("{") if "format" in LAYOUTS[a.blank] else 1
    if len(a.values) != need:
        raise SystemExit(f"{a.blank} takes {need} value(s)")
    print("installed", install(media_root(a.media), a.blank, a.values, a.name, a.post, a.control))
    return 0


if __name__ == "__main__":
    sys.exit(main())
