"""fix_pak_names.py -- repair member names that Python's zipfile silently rewrote.

🚨 `zipfile` NORMALISES BACKSLASHES TO FORWARD SLASHES WHEN IT *READS* AN
ARCHIVE. Not when it writes -- when it reads. Positive control on an untouched
Diamondback pak, comparing `ZipFile.infolist()` against the raw central
directory bytes:

    members where zipfile differs from the raw bytes: 19 of 31

    raw  'classes/sounds\\music_presets.xml'
    lib  'classes/sounds/music_presets.xml'            <- rewritten
    raw  '[textures]\\pct\\level_..._blend_map__cmp.pct'
    lib  '[textures]/pct/level_..._blend_map__cmp.pct' <- rewritten
    raw  'ui/textures/grandpas_truck_big.png'
    lib  'ui/textures/grandpas_truck_big.png'          <- unchanged, already forward

So ANY pak rebuilt by copying `info.filename` through comes out with the wrong
names, the game stops finding those members, and the map drops out of the level
list with no error, no log line and no crash. Assigning `.filename` after
constructing a ZipInfo fixes the WRITE side only; the names were already wrong
before they got there.

⛔ The corollary is that `ZipFile.namelist()` is not evidence about a pak. Read
the central directory.

THE SPELLING IS MIXED, AND REPRODUCIBLE

Ground truth from the two shipped Diamondback paks, 39 names:

    [...]                 every separator a backslash      [scene]\\data.stg
    ui/...                every separator forward          ui/textures/x.png
    anything else         FIRST forward, the rest backslash
                                                           classes/sounds\\x.xml
                                                           sounds/music\\x.wav
    no separator          unchanged                        pak.load_list

`--verify` checks that rule against a pak that was never touched, which is the
only reason to trust it on one that was.

THE REPAIR IS BYTE-LOCAL. A forward slash and a backslash are both one byte, so
a corrected name is exactly as long as the broken one and nothing moves: no
offset, no header, no data. The name is stored twice -- in the local file header
and in the central directory -- and both are patched.
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path

EOCD = b"PK\x05\x06"
CEN = b"PK\x01\x02"
LOC = b"PK\x03\x04"


# 🚨 PREFIXES THIS RULE HAS NO EVIDENCE FOR. The rule below was derived from 49
# names across four shipped paks, none of which contained a `prebuild/` member.
# The editor in fact writes those two different ways in the same archive:
#
#     prebuild/meshes/plants_mullein_card_01            all forward
#     prebuild/textures/pct\plants_..._foliage__d_a.pct forward, forward, backslash
#
# No rule derived from the other names predicts both, so `prebuild/` is left
# alone rather than guessed at. ⛔ Applying the rule to these rewrites 15 correct
# names in a packed map and breaks its custom plants.
UNKNOWN_PREFIXES = ("prebuild/", "prebuild\\")


def correct(name: str) -> str:
    """The spelling the game expects, where there is evidence for it.

    ⛔ Returns the name UNCHANGED for any prefix the rule has not been validated
    against. A rule generalised past its evidence rewrites correct data with
    confidence, which is worse than having no rule at all.
    """
    flat = name.replace("\\", "/")
    if "/" not in flat:
        return flat
    if flat.startswith(UNKNOWN_PREFIXES) or name.startswith(UNKNOWN_PREFIXES):
        return name
    if flat.startswith("["):
        return flat.replace("/", "\\")
    if flat.startswith("ui/"):
        return flat
    head, _, tail = flat.partition("/")
    return head + "/" + tail.replace("/", "\\")


def entries(data: bytes):
    """Walk the central directory from the EOCD, not by scanning for signatures.

    A pak is zero-padded to 4096 after its EOCD, and `PK\\x01\\x02` can occur
    inside stored texture data, so scanning is not safe. Take the last EOCD.
    """
    i = data.rfind(EOCD)
    if i < 0:
        raise ValueError("no EOCD -- not a zip")
    count = struct.unpack("<H", data[i + 10:i + 12])[0]
    cd_off = struct.unpack("<I", data[i + 16:i + 20])[0]
    out = []
    o = cd_off
    for _ in range(count):
        if data[o:o + 4] != CEN:
            raise ValueError(f"central directory broken at {o}")
        nlen = struct.unpack("<H", data[o + 28:o + 30])[0]
        xlen = struct.unpack("<H", data[o + 30:o + 32])[0]
        clen = struct.unpack("<H", data[o + 32:o + 34])[0]
        lho = struct.unpack("<I", data[o + 42:o + 46])[0]
        out.append(dict(cen_name=o + 46, nlen=nlen, lho=lho,
                        name=data[o + 46:o + 46 + nlen].decode("utf-8", "replace")))
        o += 46 + nlen + xlen + clen
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pak", nargs="+")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="only check that correct() reproduces the pak's own "
                         "names; use on a pak that was never rebuilt")
    a = ap.parse_args()

    rc = 0
    for p in (Path(x) for x in a.pak):
        data = bytearray(Path(p).read_bytes())
        ents = entries(bytes(data))
        wrong = [e for e in ents if e["name"] != correct(e["name"])]

        if a.verify:
            print(f"{p.name}: {len(ents)} members, "
                  f"{len(wrong)} disagree with the rule")
            for e in wrong:
                print(f"   is   {e['name']!r}")
                print(f"   rule {correct(e['name'])!r}")
            if wrong:
                rc = 1
            continue

        print(f"{p.name}: {len(ents)} members, {len(wrong)} need repair")
        for e in wrong:
            fixed = correct(e["name"])
            assert len(fixed) == len(e["name"]) == e["nlen"], \
                f"length changed: {e['name']!r} -> {fixed!r}"
            print(f"   {e['name']!r}")
            print(f"     -> {fixed!r}")
            if not a.apply:
                continue
            enc = fixed.encode("utf-8")
            # central directory copy
            data[e["cen_name"]:e["cen_name"] + e["nlen"]] = enc
            # local file header copy
            lho = e["lho"]
            if bytes(data[lho:lho + 4]) != LOC:
                raise ValueError(f"no local header at {lho} for {e['name']!r}")
            lnl = struct.unpack("<H", bytes(data[lho + 26:lho + 28]))[0]
            if lnl != e["nlen"]:
                raise ValueError(f"local/central name length mismatch at {lho}")
            data[lho + 30:lho + 30 + lnl] = enc

        if a.apply and wrong:
            before = Path(p).stat().st_size
            Path(p).write_bytes(bytes(data))
            after = Path(p).stat().st_size
            print(f"   wrote {p.name}, {before:,} -> {after:,} B "
                  f"(must be equal: {'yes' if before == after else 'NO'})")
            left = [e for e in entries(Path(p).read_bytes())
                    if e["name"] != correct(e["name"])]
            print(f"   re-read: {len(left)} still wrong")
            if left:
                rc = 1
        elif wrong:
            print("   dry run. add --apply to write.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
