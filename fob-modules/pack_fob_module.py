#!/usr/bin/env python
"""Pack a custom FOB module source into the .pak the game reads.

Usage:
    python pack_fob_module.py <mod folder> [-o OUTDIR] [--dry-run]
    python pack_fob_module.py <tree> --all [-o OUTDIR]

The editor's File > Pack does this, but it needs the GUI and it zero-pads the
result to 4096 bytes.  That padding is cosmetic: the four mod paks installed in
``Media\\Mods`` that are confirmed working in game (Eagle Egg, Ozark Ginseng,
Remote Detonation Device, and the Gimbo specialist) are all plain unpadded zips.
So a plain zip is sufficient and this script produces one.

Two things this deliberately does NOT do:

  * It never copies ``ZipInfo.extra``.  ``zipfile`` writes the extra field into
    the local header as well as the central directory, and the game then fails
    to load that entry.  A 36-byte NTFS timestamp block is enough to break it.
  * It never writes into ``Media\\Mods`` unless you point ``-o`` there, because
    that folder is scanned by the editor and the game.

Run check_fob_module.py first.  This will refuse to pack a source with ERRORs
unless you pass --force.
"""

import argparse
import os
import shutil
import sys
import zipfile

import check_fob_module as chk

DESC_MEMBER = chk.DESC_MEMBER


def _ui_textures(mod_dir):
    """Custom art shipped with the mod, as [(pak member name, abs path)].

    The working mods in Media\\Mods establish the convention: the PNG lives at
    ``ui/textures/<basename>.png`` inside the pak, and the descriptor field holds
    the bare ``<basename>`` with no path and no extension (Gimbo's ``bg`` is
    ``gimbo_bg`` for member ``ui/textures/gimbo_bg.png``).
    """
    # WALK, DO NOT LIST. The engine's own layout is nested -- a mesh lives at
    # `meshes/models/<name>.fbx` and its textures at `textures/models/*.tga`,
    # mirroring the Media tree. A flat listdir of `meshes` finds nothing at all,
    # and the pak then ships a class naming a mesh that is not inside it.
    #
    # ⛔ SOURCE TGAs DO NOT GO IN THE PAK. Measured 2026-10-16 across both
    # working custom-model mods on disk: Gimbo's Resource Scanner (9 members)
    # and PebbleGas (24 members) contain ZERO .tga between them, while both
    # keep their `textures/models/*.tga` in the unpacked folder. Shipping them
    # added 11 MB of dead weight to a pak and matched nothing.
    #
    # 🚨 THE PAK CARRIES EACH ASSET UNDER **BOTH** PATH NAMESPACES. Those same
    # two mods each hold, for every model:
    #
    #     classes/models/<x>.xml      AND  [media]/classes/models/<x>.xml
    #     prebuild/meshes/models_<x>  AND  [meshes]/models_<x>
    #
    # `[meshes]/` is the namespace shared.pak itself uses. Emitting only the
    # first half of each pair produces a pak whose classes name meshes the
    # engine cannot resolve under the name it looks them up by.
    out = []
    for sub, exts in (("ui/textures", (".png",)),
                      ("classes", (".xml",)),
                      ("meshes", (".fbx", ".xml")),
                      ("prebuild/meshes", ())):
        root = os.path.join(mod_dir, *sub.split("/"))
        if not os.path.isdir(root):
            continue
        for cur, _dirs, files in os.walk(root):
            for f in sorted(files):
                if exts and not f.lower().endswith(exts):
                    continue
                p = os.path.join(cur, f)
                member = os.path.relpath(p, mod_dir).replace("\\", "/")
                out.append((member, p))
                mirror = _engine_namespace(member)
                if mirror:
                    out.append((mirror, p))
    return out


def _engine_namespace(member):
    """The `[media]/` or `[meshes]/` twin of a mod-relative member, or None.

    Derived from the member lists of Gimbo's Resource Scanner and PebbleGas,
    the only two custom-model module mods that work in game.
    """
    if member.startswith("classes/"):
        return "[media]/" + member
    if member.startswith("prebuild/meshes/"):
        return "[meshes]/" + member[len("prebuild/meshes/"):]
    return None


def pack(source_path, out_pak, dry_run=False):
    """Write a pak from a BaseUpgradeDesc source plus any ui/textures art."""
    payload = open(source_path, "rb").read()
    extras = _ui_textures(os.path.dirname(source_path))
    if dry_run:
        return len(payload)

    # Build the entry from scratch so no extra field can leak into the local
    # header.  Mirror the editor's entry metadata otherwise.
    info = zipfile.ZipInfo(DESC_MEMBER,
                           date_time=_mtime_tuple(source_path))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.external_attr = 0o100666 << 16

    os.makedirs(os.path.dirname(os.path.abspath(out_pak)), exist_ok=True)
    tmp = out_pak + ".tmp"
    with zipfile.ZipFile(tmp, "w") as z:
        # Art first, descriptor last, matching the member order of every working
        # mod pak in Media\Mods.
        for member, path in extras:
            art = zipfile.ZipInfo(member, date_time=_mtime_tuple(path))
            art.compress_type = zipfile.ZIP_DEFLATED
            art.create_system, art.create_version, art.extract_version = 3, 20, 20
            art.external_attr = 0o100666 << 16
            z.writestr(art, open(path, "rb").read())
        z.writestr(info, payload)

    _verify(tmp, payload, [m for m, _ in extras])
    os.replace(tmp, out_pak)

    _pack_textures(os.path.dirname(source_path), out_pak[:-4] + "_pc.pak")
    return os.path.getsize(out_pak)


def _pack_textures(mod_dir, companion):
    """Write the _pc.pak: the mod's runtime textures, `.pct` ONLY.

    The runtime reads a custom model's textures from here -- PebbleGas and
    Gimbo's carry `prebuild/textures/pct/*.pct`, deflated, nothing else. A mod
    with no custom textures still gets an empty companion, as before.

    ⛔ NEVER `.pct_header`. Confirmed in game 2026-10-24: with headers shipped
    beside the `.pct`, Doc Windsor's module drew only its smallest mips (a
    32-64 px blur). Any header still in the source folder is skipped here.
    """
    src = os.path.join(mod_dir, "prebuild", "textures", "pct")
    files = sorted(f for f in os.listdir(src) if f.endswith(".pct")) if os.path.isdir(src) else []
    if not files:
        if not os.path.exists(companion):
            open(companion, "wb").close()
        return
    tmp = companion + ".tmp"
    with zipfile.ZipFile(tmp, "w") as z:
        for f in files:
            p = os.path.join(src, f)
            zi = zipfile.ZipInfo("prebuild/textures/pct/" + f, date_time=_mtime_tuple(p))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system, zi.create_version, zi.extract_version = 3, 20, 20
            zi.external_attr = 0o100666 << 16
            z.writestr(zi, open(p, "rb").read())
    with zipfile.ZipFile(tmp) as z:
        if z.testzip() is not None or any(i.extra for i in z.infolist()):
            raise RuntimeError("_pc.pak failed its round-trip check")
    os.replace(tmp, companion)
    print("      _pc.pak: %d texture(s), .pct only" % len(files))


def _mtime_tuple(path):
    import time
    t = time.localtime(os.path.getmtime(path))
    return t[:6]


def _verify(pak, expected, extra_members=()):
    """Round-trip the pak and walk every local header, not just the directory."""
    want = list(extra_members) + [DESC_MEMBER]
    with zipfile.ZipFile(pak) as z:
        bad = z.testzip()
        if bad is not None:
            raise RuntimeError("pak failed testzip at %s" % bad)
        names = z.namelist()
        if names != want:
            raise RuntimeError("pak members are %r, expected %r" % (names, want))
        if z.read(DESC_MEMBER) != expected:
            raise RuntimeError("pak payload does not round-trip")
        for n in names:
            if z.getinfo(n).extra:
                raise RuntimeError("central directory extra field on %r" % n)

    # zipfile only ever shows the central directory.  An extra field in a local
    # header passes every check above and still breaks that entry in game, so
    # walk the local headers directly.  A 36-byte NTFS block is enough to do it.
    import struct
    with open(pak, "rb") as fh:
        for _ in want:
            head = fh.read(30)
            (sig, _, _, _, _, _, _, comp_size, _,
             name_len, extra_len) = struct.unpack("<IHHHHHIIIHH", head)
            if sig != 0x04034B50:
                raise RuntimeError("missing local file header signature")
            if extra_len:
                raise RuntimeError("local header carries a %d-byte extra field"
                                   % extra_len)
            name = fh.read(name_len).decode()
            if name not in want:
                raise RuntimeError("unexpected local header name %r" % name)
            fh.seek(comp_size, os.SEEK_CUR)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="a BaseUpgradeDesc_* mod folder, or a tree with --all")
    ap.add_argument("-o", "--outdir", default=None,
                    help="where to write the .pak (default: the mod folder's parent)")
    ap.add_argument("--all", action="store_true", help="pack every mod under path")
    ap.add_argument("--force", action="store_true",
                    help="pack even when check_fob_module reports an ERROR")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    mods = list(chk.find_mods(args.path))
    if not args.all:
        mods = [m for m in mods if m[1]["source"]][:1]
    mods = [m for m in mods if m[1]["source"]]
    if not mods:
        print("no packable mod found under %s" % args.path)
        return 2

    failed = 0
    for ident, info in sorted(mods):
        raw = open(info["source"], "rb").read().decode("utf-8-sig")
        errors = [f for f in chk.check_descriptor(raw, "source", ident, info["dir"])
                  if f[0] == "ERROR"]
        if errors and not args.force:
            print("SKIP  %s -- source has %d ERROR; fix it or pass --force"
                  % (ident, len(errors)))
            for _, rule, msg in errors:
                print("        %-22s %s" % (rule, msg))
            failed += 1
            continue

        outdir = args.outdir or os.path.dirname(info["dir"])
        out_pak = os.path.join(outdir, "%s_%s.pak" % (DESC_MEMBER, ident))
        try:
            size = pack(info["source"], out_pak, dry_run=args.dry_run)
        except Exception as exc:
            print("FAIL  %s -- %s" % (ident, exc))
            failed += 1
            continue
        verb = "would write" if args.dry_run else "wrote"
        print("OK    %s -- %s %s (%d B)" % (ident, verb, out_pak, size))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
