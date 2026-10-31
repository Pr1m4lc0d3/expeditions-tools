#!/usr/bin/env python3
"""Make custom Media models load in a packed map: rename their scene brands.

THE ENGINE DEFECT (measured live 2026-10-28)

A model placed from Media is stored in the compiled scene as `<model>\\Media`.
At load the game registers the model's class from the map pak under one key and
looks it up under another, so the lookup always fails:

    registered   my_oil_pump_01_level_objectives.pak     (sub_140C64410, model branch:
                                                         archive cut at its first "_")
    looked up    my_oil_pump_01\\mod_level_objectives.pak (scene brand, "\\Media" ->
                                                         "\\<archive>")

The name compare treats "/" and "\\" alike but never "_" and "\\", so the two can
never match. The game logs `Terrain| Cant load Model Brand '...'` to a retail log
that is discarded and drops back to the main menu. No Media layout, class
attribute, texture or landmark changes either key.

THE FIX. A brand with no backslash is looked up verbatim, as every stock brand
is. So the scene brand is rewritten to the key the game registers:

    my_oil_pump_01\\Media  ->  my_oil_pump_01_level_objectives.pak

VERIFIED in game 2026-10-28 20:16 (Objectives, Saber's sample renamed): lookup
found, brand loaded, model rendered textured.

FORMAT. `[scene]/data_for_ssl.bin` holds strings as <u16 length incl NUL><text><NUL>,
and its header carries the file's own total size at offset 8. That size field is
the only value found that points past a brand (scan of every u32 in the file), so
the rewrite fixes the prefix and the header and nothing else. The pak member is
replaced with pak_splice.splice, which keeps every other byte of the archive.

ONLY classes the pak itself ships are renamed: a `<x>\\Media` brand is rewritten
only when `classes/models/<x>.xml` is a member. Landmark references
(`landmarks/<x>_lmk\\Media`) are mesh references, not brands, and are left alone.

⚠ UNVERIFIED: a map installed FROM MOD.IO. Locally the game names the archive
`mod_<pak name>`; for a mod.io install it may be the mod's numeric id, as it is
for truck mods (`e_maz_3798318`). If so the key changes and this rewrite misses.
--archive lets the name be given once it is known.

A Pack terrain rebuilds the pak and erases this. after_pack.py reapplies it.

    python fix_map_model_brands.py <level>            report only
    python fix_map_model_brands.py <level> --apply    rewrite (game must be closed)
    python fix_map_model_brands.py <level> --archive <name>
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import struct
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "fob-modules"))   # the shared saber package
from pak_splice import splice  # noqa: E402
from saber import gamepaths  # noqa: E402

MODS = str(gamepaths.media_root("expeditions") / "Mods")
SCENE = "[scene]/data_for_ssl.bin"


def model_key(stem: str, archive: str) -> str:
    """The key sub_140C64410 registers a MODEL class under."""
    cut = archive.find("_")
    return f"{stem}_{archive[cut + 1:] if cut >= 0 else archive}"


def pak_path(level: str) -> str:
    if os.path.isfile(level):
        return level
    name = level if level.startswith("level_") else f"level_{level}"
    return os.path.join(MODS, f"{name}.pak")


def plan(scene: bytes, stems: list[str], archive: str):
    """(old record, new record, count) for every Media brand of a shipped class."""
    out = []
    for stem in stems:
        old = f"{stem}\\Media".encode() + b"\0"
        new = model_key(stem, archive).encode() + b"\0"
        rec = struct.pack("<H", len(old)) + old
        n = scene.count(rec)
        done = scene.count(struct.pack("<H", len(new)) + new)
        out.append((stem, rec, struct.pack("<H", len(new)) + new, n, done))
    return out


def rewrite(scene: bytes, rows) -> bytes:
    if struct.unpack_from("<I", scene, 8)[0] != len(scene):
        raise SystemExit("scene header size does not match its length; format not as measured, refusing")
    d = bytes(scene)
    for _, old, new, n, _ in rows:
        if n:
            d = d.replace(old, new)
    d = bytearray(d)
    struct.pack_into("<I", d, 8, len(d))
    return bytes(d)


_found = gamepaths.game_dir("expeditions")
GAME = str(_found) if _found else ""   # or pass --game; SABER_GAME_DIR and toolkit.json work too


def texture_report(pak: str, stems: list[str], game: str = GAME) -> int:
    """Every texture each custom mesh names, and whether the game can find it.

    The converted mesh embeds its mesh XML verbatim, so the texture list is read
    from the shipped bytes, not guessed from the model's name (the old check in
    register_custom_models.py guessed, and flagged Saber's own sample UNTEXTURED
    while it rendered textured in game). A texture is fine if the map's _pc.pak
    carries it or the game's own texture paks already do (landmark__d, stock).
    Returns the number of textures nobody carries.
    """
    pc = pak[:-4] + "_pc.pak"
    shipped = set()
    if os.path.isfile(pc) and os.path.getsize(pc):
        with zipfile.ZipFile(pc) as z:
            shipped = {n.replace("\\", "/").rsplit("/", 1)[-1].lower() for n in z.namelist()}
    stock = None
    missing = 0
    with zipfile.ZipFile(pak) as z:
        members = {n.replace("\\", "/"): n for n in z.namelist()}
        for stem in stems:
            mesh = members.get(f"prebuild/meshes/models_{stem}")
            if not mesh:
                print(f"   {stem}: ⛔ no converted mesh in the pak")
                missing += 1
                continue
            head = z.read(mesh)[:65536].decode("latin-1")
            maps = sorted(set(re.findall(r'\w+Map="([^"]+)\.tga"', head)))
            before = missing
            for m in maps:
                pct = m.replace("/", "_").lower() + ".pct"
                if pct in shipped:
                    continue
                if stock is None:
                    stock = set()
                    paks = os.path.join(game, "preload", "paks", "client")
                    if not os.path.isdir(paks):
                        print(f"   (game not found at {game}; pass --game <install folder> to check stock textures)")
                    else:
                        for p in os.listdir(paks):
                            if "textures" in p and p.endswith(".pak"):
                                with zipfile.ZipFile(os.path.join(paks, p)) as s:
                                    stock |= {n.rsplit("/", 1)[-1].lower() for n in s.namelist()}
                if pct in stock:
                    continue
                print(f"   {stem}: ⛔ texture {m}.tga is in neither the map's _pc.pak nor the game -- it will render untextured")
                missing += 1
            if not maps:
                print(f"   {stem}: ⛔ no texture names found in the mesh")
                missing += 1
            elif missing == before:
                print(f"   {stem}: {len(maps)} texture(s), all carried")
    return missing


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # warnings carry ⛔; cp1252 consoles crashed on it
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--archive", help="archive name the game mounts the map under (default mod_<pak name>)")
    ap.add_argument("--game", default=GAME, help="game install folder, for the stock-texture check")
    a = ap.parse_args()

    pak = pak_path(a.level)
    if not os.path.isfile(pak):
        raise SystemExit(f"no such pak: {pak}")
    archive = a.archive or "mod_" + os.path.basename(pak)
    with zipfile.ZipFile(pak) as z:
        names = z.namelist()
        if SCENE not in names:
            raise SystemExit(f"{os.path.basename(pak)}: no {SCENE}")
        scene = z.read(SCENE)
    stems = sorted({m.group(1) for n in names
                    if (m := re.match(r"classes/models[\\/]([^\\/]+)\.xml$", n))})
    print(f"{os.path.basename(pak)}: {len(stems)} custom model class(es), archive '{archive}'")
    if not stems:
        print("   nothing to do")
        return 0
    if texture_report(pak, stems, a.game):
        print("   *** a custom model is missing textures - do not upload this map ***")
    rows = plan(scene, stems, archive)
    todo = 0
    for stem, _, new, n, done in rows:
        key = new[2:-1].decode()
        if n:
            print(f"   will rename {stem}\\Media -> {key}  ({n} in scene)")
            todo += n
        elif done:
            print(f"   {stem}: already {key}")
        else:
            print(f"   {stem}: class shipped but not placed in the scene")
    if not todo:
        print("   nothing to do")
        return 0
    if not a.apply:
        print("   report only; pass --apply to write")
        return 0

    patched = rewrite(scene, rows)
    bak = pak + ".before-brands"
    shutil.copy2(pak, bak)
    splice(pak, SCENE, patched)
    with zipfile.ZipFile(pak) as z:
        back = z.read(SCENE)
    left = sum(back.count(old) for _, old, _, _, _ in rows)
    if back != patched or left:
        shutil.copy2(bak, pak)
        raise SystemExit(f"readback failed ({left} old brands left); pak restored from {bak}")
    print(f"   wrote {todo} brand(s); backup {os.path.basename(bak)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
