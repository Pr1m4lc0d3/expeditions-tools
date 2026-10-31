#!/usr/bin/env python3
r"""Recolour vanilla FOB module paint, and build the mod folder that uses it.

    python recolor_fob.py sheet                       # contact sheet, all 8
    python recolor_fob.py sheet --map fob_cabin__d_a
    python recolor_fob.py maps  <module>               # what one module paints with
    python recolor_fob.py build <module> <colour> --out <dir>

A vanilla module mesh carries its material list as plain XML in the mesh
header, so recolouring one means: measure the paint in each amber albedo map,
work out the DiffuseMultiplier that turns that paint into the target colour,
and write it onto the materials that use the map. Saber's own textures are kept
and nothing ships but the edited meshes. The geometry is never touched -- it is
copied across byte-identical and ``join_mesh`` round-trips are checked.

🚨 WHY A TINT, NOT NEW TEXTURES (measured live 2026-10-31, The Crow's Nest,
RE\live\brand_tap.py). An edited vanilla mesh that names MOD textures logs
"Info| Failed to load texture textures/models/<name>.tga." for every one and
draws them BLACK, whatever their colour, name, size or companion maps. A
converter-built custom model (the Long Pull) loaded its textures in the same
session with no failures. Saber's own textures with a DiffuseMultiplier on the
material render correctly: the Crow's Nest went carbon grey and olive this way.

⚠ A tint multiplies, channel by channel, and cannot exceed 1.0, so it can only
darken a channel. Amber paint has little blue in it: red, orange, olive, green,
carbon and black are reachable; blue, purple and white are not. ``build``
measures each map and REFUSES a colour it cannot reach rather than ship a
muddy approximation. Run ``reach <module>`` to see what is possible.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from saber import recolor
from vehicle_pak import join_mesh, split_mesh, write_tga

from saber import gamepaths

# Resolved at import but only USED when a game file is read, so the tint maths
# can be imported and tested on a machine without the game.
_GAME_DIR = gamepaths.game_dir("expeditions") or Path("EXPEDITIONS-NOT-FOUND--set-SABER_GAME_DIR")
GAME = _GAME_DIR / "preload" / "paks" / "client"
SHARED, EDITOR, INITIAL = GAME / "shared.pak", GAME / "editor.pak", GAME / "initial.pak"
OUT = Path("out") / "recolor"


# ----------------------------------------------------------------------- reads

def mesh_names() -> list[str]:
    with zipfile.ZipFile(SHARED) as z:
        return sorted(
            n.split("/")[-1]
            for n in z.namelist()
            if n.startswith("[meshes]/models_fob_module_")
        )


def read_mesh(name: str) -> tuple[str, bytes]:
    with zipfile.ZipFile(SHARED) as z:
        return split_mesh(z.read(f"[meshes]/{name}"))


def read_class(brand: str) -> str | None:
    """The ModelBrand XML. Base game first, then the DLC trees."""
    with zipfile.ZipFile(INITIAL) as z:
        for n in z.namelist():
            if n.endswith(f"classes/models/{brand}.xml"):
                return z.read(n).decode("utf-8", "replace")
    return None


def read_albedo(key: str) -> np.ndarray | None:
    name = key if key.startswith("models_") else f"models_{key}"
    with zipfile.ZipFile(EDITOR) as z:
        try:
            blob = z.read(f"[textures]/dds/{name}.dds")
        except KeyError:
            return None
    im = Image.open(io.BytesIO(blob))
    im.load()
    return np.asarray(im.convert("RGBA"))


def albedo_maps(xml: str) -> list[str]:
    return sorted(set(re.findall(r'AlbedoMap="models/([^"]+)\.tga"', xml)))


def classify(maps: list[str]) -> tuple[list[str], list[str], list[str]]:
    """-> (shell, clutter, unclassified-but-amber)."""
    shell, clutter, unknown = [], [], []
    for m in maps:
        img = read_albedo(m)
        if img is None:
            continue
        st = recolor.grade(img, img)          # painted_fraction only
        if st["painted_fraction"] < 0.01:
            continue                           # carries no FOB amber
        (shell if m in recolor.SHELL else clutter if m in recolor.CLUTTER
         else unknown).append(m)
    return shell, clutter, unknown


# ------------------------------------------------------------------ the sheet

def paint_dense_crop(img: np.ndarray, size: int = 384) -> tuple[int, int]:
    """Top-left of the window with the most paint in it. The atlas is mostly
    unpainted, so a centre crop usually shows nothing worth judging."""
    h, s, v = recolor.to_hsv(img[..., :3].astype(np.float32) / 255.0)
    a = img[..., 3].astype(np.float32) / 255.0 if img.shape[-1] == 4 else None
    w = recolor.paint_weight(h, s, v, a)
    step = max(size // 4, 1)
    best, at = -1.0, (0, 0)
    for y in range(0, max(img.shape[0] - size, 1), step):
        for x in range(0, max(img.shape[1] - size, 1), step):
            score = float(w[y:y + size, x:x + size].mean())
            if score > best:
                best, at = score, (y, x)
    return at


def sheet(maps: list[str], out: Path, tile: int = 288) -> Path:
    """Each map as the game will draw it under every tint: texture x multiplier.
    A tint the map cannot reach is shown with a red bar under its tile."""
    from PIL import ImageDraw
    cols = ["source"] + list(TINTS)
    pad, label, head = 6, 22, 26
    w = len(cols) * (tile + pad) + pad
    h = len(maps) * (tile + pad + label) + pad + head
    sheetim = Image.new("RGB", (w, h), (28, 28, 30))
    d = ImageDraw.Draw(sheetim)
    for i, c in enumerate(cols):
        d.text((pad + i * (tile + pad) + 4, 7), c.upper(), fill=(235, 235, 235))
    for r, m in enumerate(maps):
        img = read_albedo(m)
        if img is None:
            continue
        y0, x0 = paint_dense_crop(img, min(tile * 2, img.shape[0]))
        side = min(tile * 2, img.shape[0])
        y = pad + head + r * (tile + pad + label)
        d.text((pad + 2, y - 14), m, fill=(160, 200, 160))
        paint = paint_colour(m)
        for i, c in enumerate(cols):
            crop = img[y0:y0 + side, x0:x0 + side, :3].astype(np.float32)
            if c != "source" and paint is not None:
                mult, miss = tint_for(paint, c)
                crop = crop * np.array(mult, dtype=np.float32) / 255.0
                if miss > REACH:
                    d.rectangle([pad + i * (tile + pad), y + tile, pad + i * (tile + pad) + tile, y + tile + 4],
                                fill=(200, 30, 30))
            pil = Image.fromarray(crop.clip(0, 255).astype(np.uint8)).resize((tile, tile), Image.LANCZOS)
            sheetim.paste(pil, (pad + i * (tile + pad), y))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheetim.save(out)
    return out


def saber_reference(out: Path, tile: int = 300) -> Path:
    """Our red tint beside Saber's own red texture, same crop. The only tile on
    any sheet with a ground truth behind it."""
    from PIL import ImageDraw
    src = read_albedo("fob_modular_floor_mat__d")
    ref = read_albedo("fob_modular_floor_red_mat__d")
    mult, _ = tint_for(paint_colour("fob_modular_floor_mat__d"), "red")
    mine = (src[..., :3].astype(np.float32) * np.array(mult) / 255.0).clip(0, 255).astype(np.uint8)
    y0, x0 = paint_dense_crop(src, 512)
    im = Image.new("RGB", (3 * (tile + 8) + 8, tile + 30), (28, 28, 30))
    d = ImageDraw.Draw(im)
    for i, (lab, a) in enumerate(
        [("vanilla amber", src), ("OURS red", mine), ("SABER's own red", ref)]
    ):
        d.text((8 + i * (tile + 8) + 4, 5), lab, fill=(235, 235, 235))
        crop = Image.fromarray(a[y0:y0 + 512, x0:x0 + 512, :3])
        im.paste(crop.resize((tile, tile), Image.LANCZOS), (8 + i * (tile + 8), 26))
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out


# ------------------------------------------------------------------- the build

# Colours a module can be tinted to. RGB the paint should end up as, judged on
# a plain albedo; carbon/olive are the values approved in game on the Crow's Nest.
TINTS = {
    "red":    (170, 32, 26),
    "orange": (205, 92, 20),
    "olive":  (85, 90, 42),
    "green":  (60, 100, 40),
    "carbon": (60, 62, 65),
    "black":  (30, 30, 30),
    "grey":   (110, 110, 110),
    "blue":   (35, 65, 150),
    "purple": (95, 45, 125),
    "white":  (225, 225, 225),
}
REACH = 0.12          # worst channel may miss the target by at most 12% of 255


def paint_colour(m: str) -> np.ndarray | None:
    """Mean RGB of the painted (amber) pixels of one albedo map."""
    img = read_albedo(m)
    if img is None:
        return None
    rgb = img[..., :3].astype(np.float32) / 255.0
    h, s_, v = recolor.to_hsv(rgb)
    a = img[..., 3].astype(np.float32) / 255.0 if img.shape[-1] == 4 else None
    w = recolor.paint_weight(h, s_, v, a)
    if w.sum() < 1:
        return None
    return (img[..., :3].reshape(-1, 3) * w.reshape(-1, 1)).sum(0) / w.sum()


def tint_for(paint: np.ndarray, colour: str) -> tuple[tuple[int, int, int], float]:
    """-> (DiffuseMultiplier as 0-255 ints, worst channel miss as a 0-1 fraction)."""
    want = np.array(TINTS[colour], dtype=np.float32)
    mult = np.clip(want / np.maximum(paint, 1.0), 0.0, 1.0)
    got = paint * mult
    miss = float(np.max(np.abs(got - want)) / 255.0)
    return tuple(int(round(x * 255)) for x in mult), miss


CAST = 0.12          # mean colour a tint may add to the map's unpainted pixels


def cast_of(m: str, mult: tuple[int, int, int]) -> float:
    """How much colour the tint adds to the parts of the map that are NOT paint.

    A multiplier hits every pixel of the texture. Cancelling amber needs far
    more blue than red, so greys, whites and decals on the same map turn blue
    under carbon or black (seen on heli_1__d and fob_modular_floor_mat__d).
    Returned as the rise in mean chroma (0-1) over the unpainted pixels,
    weighted by how much of the map they cover."""
    img = read_albedo(m)
    rgb = img[..., :3].astype(np.float32) / 255.0
    h, s_, v = recolor.to_hsv(rgb)
    a = img[..., 3].astype(np.float32) / 255.0 if img.shape[-1] == 4 else None
    w = recolor.paint_weight(h, s_, v, a)
    rest = (w < 0.2) & (v > 0.15)
    if not rest.any():
        return 0.0
    def chroma(x):
        return x.max(-1) - x.min(-1)
    tinted = rgb * (np.array(mult, dtype=np.float32) / 255.0)
    rise = float(np.mean(chroma(tinted[rest]) - chroma(rgb[rest])))
    return max(rise, 0.0) * float(rest.mean()) * 4.0     # x4: a quarter of the map = full weight


def apply_tints(xml: str, tints: dict[str, tuple[int, int, int]]) -> tuple[str, list[str]]:
    """Write DiffuseMultiplier onto every material whose AlbedoMap is tinted."""
    done = []
    def one(tag: re.Match) -> str:
        t = tag.group(0)
        m = re.search(r'AlbedoMap="models/([^"]+)\.tga"', t)
        if not m or m.group(1) not in tints:
            return t
        r, g, b = tints[m.group(1)]
        t = re.sub(r'\s*DiffuseMultiplier="[^"]*"', "", t)
        name = re.search(r'Name="([^"]+)"', t)
        done.append(name.group(1) if name else m.group(1))
        mult = f'DiffuseMultiplier="g({r}; {g}; {b}; 255)"'
        return t.replace("<Material", "<Material\n\t\t" + mult, 1)
    return re.sub(r"<Material\b[^>]*>", one, xml), done


# A module is TWO models, and the small one is the one you actually see.
# modelId is the medium model, used at a main base; modelIdAdditional is the
# small one, used at outposts, and ``defaultDeployPoint`` picks between them.
# Recolouring only the medium leaves every outpost deployment vanilla amber.
SMALL_OF = {
    "fob_module_com_center": "fob_module_small_com_center",
    "fob_module_double_stock": "fob_module_small_double_stock",
    "fob_module_drone_base": "fob_module_small_drone_base",
    "fob_module_ext_parking": "fob_module_small_ext_parking",
    "fob_module_extra_storage": "fob_module_small_extra_storage",
    "fob_module_fuel_station": "fob_module_small_fuel_station",
    "fob_module_generator_big": "fob_module_generator_small",
    "fob_module_helicopter_big": "fob_module_helicopter_small",
    "fob_module_repair_zone": "fob_module_small_repair_zone",
    "fob_module_service_station": "fob_module_small_service_station",
    "fob_module_tow_truck": "fob_module_small_tow_truck",
    "fob_module_workshop": "fob_module_small_workshop",
    "fob_module_storage": None,                       # has no small twin
}


def build_module(family: str, colour: str, out: Path, scope: str,
                 only: list[str] | None = None) -> dict:
    """A RENAMED copy of one vanilla module FAMILY in a new colour.

    Vanilla modules keep reading the vanilla texture names and stay amber.
    Only a descriptor pointing at these new brands comes out in the new
    colour, so a recoloured module and a stock one stand side by side at the
    same base and read as different modules.

    The mesh goes in as an already-converted binary at
    ``prebuild/meshes/models_<name>``, which is where a working mod's converted
    mesh sits (verified against PebbleGas and Gimbo's Scanner on disk). ⇒ no
    FBX is needed and the mesh never passes through ResourceConverter. The
    geometry is carried over byte-identical; only the material XML changes.

    Four brands come out, and a descriptor needs all four:
    medium, medium_objective, small, small_objective.
    """
    if scope != "identical" and colour not in TINTS:
        raise SystemExit(f"unknown colour {colour!r}; have {', '.join(TINTS)}")
    if family not in SMALL_OF:
        raise SystemExit(f"unknown module {family!r}; have {', '.join(sorted(SMALL_OF))}")

    have = set(mesh_names())
    tints: dict[str, tuple[int, int, int]] = {}
    unreachable: list[str] = []

    brands: list[str] = []
    for b in (family, SMALL_OF[family]):
        if b is None:
            continue
        brands.append(b)
        if f"models_{b}_objective" in have:
            brands.append(f"{b}_objective")

    wanted: set[str] = set()
    written: dict[str, str] = {}
    for p in ("classes/models", "meshes/models", "prebuild/meshes"):
        (out / p).mkdir(parents=True, exist_ok=True)

    for brand in brands:
        xml, geometry = read_mesh(f"models_{brand}")
        if scope == "identical":
            # THE PROBE. Rename the brand and deliver it from the mod, but
            # change NOTHING inside it -- same geometry, same material XML,
            # same vanilla albedo paths, no textures shipped.
            #
            # Owner, 2026-10-16: every VANILLA model sells and vanishes; every
            # MOD-DELIVERED model stays on the map, custom or recoloured. That
            # leaves three differences between the two, and this build removes
            # the only one the recolour introduced. What is left is the brand
            # NAME and the DELIVERY PATH.
            #
            #   sells + vanishes      -> the albedo repointing is implicated
            #   stays on the map      -> name/delivery is the cause, and no
            #                            amount of texture work can ever fix it
            chosen = []
        else:
            shell, clutter, unknown = classify(albedo_maps(xml))
            chosen = shell if scope == "shell" else shell + clutter + unknown
            if only:   # an explicit list overrides the shell/clutter guess
                chosen = [m for m in albedo_maps(xml) if m in only]
        wanted |= set(chosen)
        for m in chosen:
            if m in tints or m in unreachable:
                continue
            paint = paint_colour(m)
            if paint is None:
                continue
            mult, miss = tint_for(paint, colour)
            if miss > REACH or cast_of(m, mult) > CAST:
                unreachable.append(m)       # left at Saber's amber, and reported
            else:
                tints[m] = mult
        xml, _ = apply_tints(xml, tints)
        assert split_mesh(join_mesh(xml, geometry))[1] == geometry, "geometry changed"
        stem = brand.removesuffix("_objective")
        new = f"{stem}_{colour}" + ("_objective" if brand.endswith("_objective") else "")
        (out / "meshes" / "models" / f"{new}.xml").write_text(xml, encoding="utf-8")
        (out / "prebuild" / "meshes" / f"models_{new}").write_bytes(join_mesh(xml, geometry))
        written[brand] = new

        cls = read_class(brand)
        if cls:
            if "NeedPreload" not in cls:
                cls = cls.replace("<ModelBrand", '<ModelBrand NeedPreload="true"', 1)
            (out / "classes" / "models" / f"{new}.xml").write_text(cls, encoding="utf-8")

    if scope != "identical" and not tints:
        raise SystemExit(f"{colour} works as a tint on none of {family}'s paint maps. "
                         f"See: python recolor_fob.py reach {family}")
    return {"family": family, "colour": colour, "brands": written,
            "maps": sorted(wanted), "tints": tints, "left": sorted(unreachable), "out": str(out)}


# ----------------------------------------------------------- the descriptor

MODEL_FIELDS = {
    "modelId": "",                      # medium model, main base
    "modelIdAdditional": "small",       # small model, outposts -- what you see
    "modelAnimated": "objective",       # medium build animation
    "modelAdditionalAnimated": "small_objective",
}


# Saber's closed table of original module identifiers. Reusing one means the
# clone shares an identity with the stock module AND with any other mod that
# cloned the same descriptor.
VANILLA_MODULE_IDS = {
    "GAS_STATION", "BASE_MODULE", "EXTRA_STORAGE", "REPAIR_ZONE", "DOUBLE_STOCK",
    "SUPPLY_SHOP", "TOW_TRUCK", "PARKING", "WORKSHOP", "SERVICE_STATION",
    "DRONE_BASE", "HELICOPTER", "GENERATOR", "ANTENNA",
}


MODS_DIR = Path.home() / "Documents" / "My Games" / "Expeditions" / "Media" / "Mods"


def installed_module_ids(mods_dir: Path = MODS_DIR) -> dict[str, str]:
    """{moduleId: mod name} for every module mod currently installed.

    ⛔ Saber's table is not the whole hazard. The owner's store went down when a
    clone duplicated an id ALREADY HELD BY ANOTHER MOD, which no static list
    can know about. Read the live install.
    """
    found: dict[str, str] = {}
    if not mods_dir.is_dir():
        return found
    for pak in sorted(mods_dir.glob("BaseUpgradeDesc_*.pak")):
        if pak.name.endswith("_pc.pak"):
            continue
        try:
            with zipfile.ZipFile(pak) as z:
                member = next((n for n in z.namelist()
                               if n.split("/")[-1] == "BaseUpgradeDesc"), None)
                if not member:
                    continue
                doc = json.loads(z.read(member).decode("utf-8-sig"))
        except Exception:
            continue
        mid = ((doc.get("SslValue") or {}).get("zoneProperty") or {}).get("moduleId")
        if mid:
            found[mid] = pak.name[len("BaseUpgradeDesc_"):-len(".pak")]
    return found


def clone_hazards(v: dict) -> list[str]:
    """Why this descriptor is a bad thing to clone.

    🚨 WRITTEN AFTER BREAKING THE OWNER'S IN-GAME STORE, 2026-10-16. The
    `Plain Stock Probe` cloned Essentials Storage and changed only the four
    model fields and the UI strings. What it also inherited, unexamined:

        $type           ZonePropertyFobModuleStorage   <- a STORE module
        moduleId        DOUBLE_STOCK                   <- an on-table id
        alwaysSpawn     true                           <- spawns by itself
        isAtDeployZone  true                           <- at every deploy zone

    Installing it put FOUR always-spawning DOUBLE_STOCK store modules in the
    game at once: vanilla, Essentials Storage, Blue Stock Test and the probe.
    ⛔ "Clone a working descriptor and change only the models" is NOT safe when
    the descriptor auto-spawns and its identifier collides. The defects file
    had already recorded that DOUBLE_STOCK collides and that the vanilla
    descriptor answers.
    """
    zp = v.get("zoneProperty") or {}
    out = []
    mid = zp.get("moduleId")
    if mid:
        held = installed_module_ids()
        if mid in held:
            out.append(f"moduleId {mid!r} is ALREADY HELD by the installed mod "
                       f"{held[mid]!r} -- a mod-vs-mod duplicate is what emptied "
                       f"the Base modules list on 2026-10-16")
    if mid in VANILLA_MODULE_IDS:
        out.append(f"moduleId {mid!r} is one of Saber's 14 original identifiers, "
                   f"so this clone shares an identity with the stock module")
    if zp.get("alwaysSpawn"):
        out.append("alwaysSpawn is true -- this module places itself, you do not "
                   "get to choose where it appears, and you cannot sell it")

    # ⛔ A STORE ROLE IS NOT A HAZARD ON ITS OWN, AND TREATING IT AS ONE BROKE
    # THE TOOL. The first version of this guard refused every
    # ZonePropertyFobModuleStorage descriptor, which is the MOST COMMON kind of
    # FOB module -- it would have made the public tool unable to build a
    # storage module at all.
    #
    # PebbleGas is exactly this shape and it works in game:
    #     moduleId 'PebbleGas' (unique, off-table) + alwaysSpawn False
    #     + ZonePropertyFobModuleStorage
    #
    # The store role only matters as an AMPLIFIER: if the identity is already
    # wrong, a store module is where that goes most visibly wrong. So it is
    # reported only alongside a real problem, never alone.
    if out and ("Storage" in str(zp.get("$type")) or "SupplyShop" in str(zp.get("$type"))):
        out.append(f"...and zoneProperty is {zp.get('$type')}, a STORE role, "
                   f"which is where a broken identity shows up worst")
    return out



def apply_overrides(v: dict, settings: list[str]) -> list[str]:
    """`--set zoneProperty.alwaysSpawn=false` -> a reported, auditable edit.

    ⛔ The point is that every departure from the reference descriptor is
    NAMED on the command line and printed back. The store broke because the
    clone inherited `alwaysSpawn` and a colliding `moduleId` silently.
    """
    done = []
    for item in settings or []:
        key, _, raw = item.partition("=")
        val: object = raw
        if raw.lower() in ("true", "false"):
            val = raw.lower() == "true"
        elif raw.lower() in ("null", "none"):
            val = None
        else:
            try:
                val = int(raw)
            except ValueError:
                pass
        node = v
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        old = node.get(parts[-1], "<absent>")
        node[parts[-1]] = val
        done.append(f"{key}: {old!r} -> {val!r}")
    return done


def vanilla_art(family: str) -> dict[str, str]:
    """The vanilla module's own card-art symbols, read from initial.pak."""
    with zipfile.ZipFile(INITIAL) as z:
        b = z.read("initial.cache_block").decode("latin1")
    m = re.search(rf'modelId\s*=\s*"{re.escape(family)}"', b)
    if not m:
        return {}
    nxt = re.compile(r'\bmodelId\s*=').search(b, m.end())      # the next module, not modelIdAdditional
    block = b[m.start(): nxt.start() if nxt else m.start() + 3000]
    out = {}
    for k in ("bg", "bgRight", "bgSmall", "popupModuleBg"):
        f = re.search(rf'\b{k}\s*=\s*"([^"]*)"', block)
        if f:
            out[k] = f.group(1)
    return out


def descriptor(reference: Path, brands: dict[str, str], family: str,
               ui_name: str, out: Path, allow_hazard: bool = False,
               settings: list[str] | None = None) -> dict:
    """Clone a WORKING descriptor and repoint only its four model fields.

    ⛔ Nothing else is invented. Cost, capacity, icons, zoneProperty and the
    rest are carried over from the reference verbatim, because every one of
    them is a field some working mod already proved. The only edits are the
    four model brands and the display strings.
    """
    with zipfile.ZipFile(reference) as z:
        member = next(n for n in z.namelist() if n.split("/")[-1] == "BaseUpgradeDesc")
        doc = json.loads(z.read(member).decode("utf-8-sig"))
    v = doc["SslValue"]

    # ⛔ COLLISIONS ARE IMPOSSIBLE BY CONSTRUCTION, NOT BY CARE.
    #
    # A cloned descriptor inherits the reference's identity, and on 2026-10-16
    # that emptied the owner's Base modules panel: Essentials Storage was
    # cloned, DOUBLE_STOCK came with it, and two mods then held one id.
    #
    # EVIDENCE for the default below. PebbleGas is a mod module that functions
    # and collides with nothing, and its identity is simply its own mod name:
    #
    #     moduleId 'PebbleGas'   $type ZonePropertyFobModuleStorage
    #                            alwaysSpawn False
    #
    # Nom Nom's Drone Delivery is the other non-colliding one and carries no
    # moduleId at all. So two shapes are known to work: a UNIQUE off-table id,
    # or none. This takes the first, derived from the module's own name, which
    # cannot collide with Saber's 14 or with another mod unless two mods share
    # a name. ⛔ --set zoneProperty.moduleId=... still overrides deliberately.
    zp = v.setdefault("zoneProperty", {})
    if "moduleId" in zp:
        unique = re.sub(r"[^A-Za-z0-9_]", "", ui_name.title().replace(" ", "")) or "CustomModule"
        zp["moduleId"] = unique
        print(f"  identity  zoneProperty.moduleId -> {unique!r} (unique, so nothing collides)")
    if zp.get("alwaysSpawn"):
        zp["alwaysSpawn"] = False
        print("  identity  zoneProperty.alwaysSpawn -> False "
              "(a module that places itself cannot be chosen, or sold)")

    overrides = apply_overrides(v, settings or [])
    for line in overrides:
        print(f"  override  {line}")

    hazards = clone_hazards(v)
    if hazards and not allow_hazard:
        raise SystemExit(
            "refusing to clone this descriptor:\n  - "
            + "\n  - ".join(hazards)
            + "\n\nClone a descriptor with no moduleId and no store role instead "
              "(Nom Nom's Drone Delivery Service is one), or pass --allow-hazard "
              "if you have thought about it."
        )

    # The card art comes from the vanilla module, never from the descriptor we
    # cloned: a custom module's own art (treadwork_bg...) is not shipped here.
    for field, name in vanilla_art(family).items():
        if field in v:
            v[field] = name

    small = SMALL_OF[family]
    # Both animated fields stay EMPTY. Pointing them at the copied _objective
    # brands gave the Crow's Nest a ghost pad that turned nearby trucks ghostly
    # (2026-10-30); with both "" it built and rendered cleanly. The _objective
    # copies still ship: a save that already names them must find them.
    want = {
        "modelId": brands.get(family),
        "modelIdAdditional": brands.get(small) if small else None,
        "modelAnimated": "",
        "modelAdditionalAnimated": "",
    }
    changed = {}
    for field, new in want.items():
        if new is None:
            continue
        changed[field] = (v.get(field), new)
        v[field] = new
    for field in ("uiName", "uiDesc", "uiDescTradeHeader", "uiTradeDefaultDescText"):
        if field in v:
            v[field] = ui_name

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=4, ensure_ascii=False), encoding="utf-8")
    return changed


# -------------------------------------------------------------------- the CLI

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sheet", help="contact sheet for the eye gate")
    s.add_argument("--map", action="append", dest="maps")
    s.add_argument("--out", default=str(OUT / "contact_sheet.png"))

    m = sub.add_parser("maps", help="list what a module paints with")
    m.add_argument("module")

    r = sub.add_parser("reach", help="which colours a tint can give each paint map")
    r.add_argument("module")

    b = sub.add_parser("build", help="write a recoloured mod folder")
    b.add_argument("module", help=f"one of: {', '.join(sorted(SMALL_OF))}")
    b.add_argument("colour")
    b.add_argument("--out", required=True)
    b.add_argument("--scope", choices=["shell", "all", "identical"], default="shell",
               help="identical = rename+deliver the vanilla mesh UNCHANGED (the sell probe)")
    b.add_argument("--descriptor-from", metavar="PAK",
                   help="a WORKING module pak to clone the descriptor from")
    b.add_argument("--ui-name", default=None, help="display name for the module")
    b.add_argument("--set", action="append", dest="settings", metavar="KEY=VALUE",
                   help="override a descriptor field, e.g. zoneProperty.alwaysSpawn=false")
    b.add_argument("--allow-hazard", action="store_true",
                   help="clone a descriptor that auto-spawns or reuses a vanilla moduleId")
    b.add_argument("--maps", action="append", metavar="MAP",
                   help="recolour exactly these albedo maps (repeatable), e.g. heli_1__d")

    a = ap.parse_args()

    if a.cmd == "sheet":
        maps = a.maps or ["fob_cabin__d_a", "fob_gas_station__d_a",
                          "fob_stock_tent__d", "fob_modular_floor_mat__d"]
        p = sheet(maps, Path(a.out))
        r = saber_reference(Path(a.out).with_name("saber_reference.png"))
        print(f"contact sheet  {p}")
        print(f"ground truth   {r}")
        return 0

    if a.cmd == "reach":
        mod = a.module if a.module.startswith("models_") else f"models_{a.module}"
        xml, _ = read_mesh(mod)
        shell, clutter, unknown = classify(albedo_maps(xml))
        print(f"{'map':34s} " + " ".join(f"{c:>6s}" for c in TINTS))
        for m in shell + clutter + unknown:
            paint = paint_colour(m)
            if paint is None:
                continue
            row = []
            for c in TINTS:
                mult, miss = tint_for(paint, c)
                row.append("   -- " if miss > REACH else " cast " if cast_of(m, mult) > CAST else "   ok ")
            print(f"{m:34s} " + " ".join(row))
        print("ok = reachable;  cast = paint reachable but greys/whites on the map pick up a colour cast;"
              "  -- = a tint cannot get there")
        return 0

    if a.cmd == "maps":
        mod = a.module if a.module.startswith("models_") else f"models_{a.module}"
        xml, _ = read_mesh(mod)
        shell, clutter, unknown = classify(albedo_maps(xml))
        print(f"{mod}: {len(albedo_maps(xml))} albedo maps")
        for lab, group in [("SHELL", shell), ("clutter", clutter),
                           ("UNCLASSIFIED amber", unknown)]:
            print(f"  {lab} ({len(group)}): {', '.join(group) or '-'}")
        return 0

    out = Path(a.out)
    info = build_module(a.module, a.colour, out, a.scope, a.maps)

    print(f"family     {info['family']}  ->  {info['colour']}")
    for old, new in info["brands"].items():
        role = ("modelId" if old == info["family"] else
                "modelAnimated" if old == f"{info['family']}_objective" else
                "modelIdAdditional" if old == SMALL_OF[info["family"]] else
                "modelAdditionalAnimated")
        print(f"  {role:24s} {old}  ->  {new}")
    for m, mult in sorted(info["tints"].items()):
        print(f"  tint       {m:32s} DiffuseMultiplier g({mult[0]}; {mult[1]}; {mult[2]}; 255)")
    for m in info["left"]:
        print(f"  left amber {m:32s} (a tint can't reach {info['colour']} here, or would tint its greys)")
    print("textures   none shipped: Saber's own, tinted")

    if a.descriptor_from:
        ui = a.ui_name or f"{a.colour.title()} {a.module.removeprefix('fob_module_').replace('_', ' ').title()}"
        changed = descriptor(Path(a.descriptor_from), info["brands"], info["family"],
                             ui, out / "BaseUpgradeDesc", a.allow_hazard, a.settings)
        print(f"\ndescriptor cloned from {Path(a.descriptor_from).name}, "
              f"{len(changed)} model fields repointed, uiName = {ui!r}")
        for f, (old, new) in changed.items():
            print(f"  {f:26s} {old}  ->  {new}")

    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"\nsize       {size / 1024 / 1024:.0f} MB")
    print(f"out        {info['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
