#!/usr/bin/env python3
"""Let the Expeditions map editor show library pictures for custom pieces.

THE DEFECT (measured 11-22-2026 with frida on the editor's own file-exists check, 0x140e6c270):
a custom piece's record name ends in "\\Media", so the library asks for

    icons/overlays_my_road\\Media.dds     -> not found, ever

while the editor's file system only splits paths on "/":

    icons/overlays_my_road/Media.dds     -> found
    icons/overlays_my_road.dds           -> found

So no custom picture can load. The editor draws its own instead: a road's texture, or for a
fence or guardrail with no texture, an empty grey square.

THE FIX: at the library's one call to the exists check (0x1404cac1c, the path a std::string at
[rbp+0x20], its length at [rbp+0x30]) a path ending in "\\Media.dds" is turned into ".dds" in
place, so Media\\icons\\<kind>_<name>.dds (what make_icon.py writes) is checked and loaded. Paths
without the backslash, every stock piece, are untouched. 41 bytes in two unused int3 gaps:

    A: mov rax,[rbp+30h]; cmp byte [rcx+rax-0Ah],'\\'; jne exists; jmp B
    B: mov dword [rcx+rax-0Ah],'.dds'; mov byte [rcx+rax-6],0; jmp exists
    call exists  ->  call A

    python editor_pictures_fix.py            check, ask once, apply (editor closed)
    python editor_pictures_fix.py --check    only say whether it is applied
    python editor_pictures_fix.py --undo     put the backup back
    add --exe <path to ExpeditionsEditor.exe> if the game is not found by itself

A backup is kept beside the exe (ExpeditionsEditor.exe.before_icons). It refuses any editor
whose bytes at those places are not the known stock ones. A game update replaces the editor:
run it again after one. Needs: pip install pefile
"""
from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

BASE = 0x140000000
CALL = 0x1404CAC1C
EXISTS = 0x140E6C270
CAVE_A = 0x140A9A62A      # 22 x int3
CAVE_B = 0x140AA8C4A      # 22 x int3


def rel(frm_next: int, to: int) -> bytes:
    return struct.pack("<i", to - frm_next)


def code() -> dict[int, bytes]:
    a = bytes.fromhex("488b4530") + bytes.fromhex("807c01f65c")
    a += b"\x0f\x85" + rel(CAVE_A + len(a) + 6, EXISTS)
    a += b"\xe9" + rel(CAVE_A + len(a) + 5, CAVE_B)
    b = bytes.fromhex("c74401f6") + b".dds" + bytes.fromhex("c64401fa00")
    b += b"\xe9" + rel(CAVE_B + len(b) + 5, EXISTS)
    return {CAVE_A: a, CAVE_B: b, CALL: b"\xe8" + rel(CALL + 5, CAVE_A)}


def offsets(data: bytes) -> dict[int, int]:
    import pefile
    pe = pefile.PE(data=data, fast_load=True)
    out = {}
    for va in code():
        r = va - BASE
        for s in pe.sections:
            if s.VirtualAddress <= r < s.VirtualAddress + s.Misc_VirtualSize:
                out[va] = r - s.VirtualAddress + s.PointerToRawData
    return out


def state(d: bytes, offs: dict[int, int]) -> str:
    if len(offs) != 3:
        return "unknown"
    if all(d[offs[k]:offs[k] + len(v)] == v for k, v in code().items()):
        return "applied"
    stock = b"\xe8" + rel(CALL + 5, EXISTS)
    if d[offs[CALL]:offs[CALL] + 5] == stock and all(
            set(d[offs[k]:offs[k] + 22]) == {0xCC} for k in (CAVE_A, CAVE_B)):
        return "stock"
    return "unknown"


def find_editor() -> Path:
    try:
        from game_paths import expeditions_dir
        return Path(expeditions_dir()) / "Sources" / "BinEditor" / "ExpeditionsEditor.exe"
    except Exception as e:
        raise SystemExit(f"Could not find Expeditions ({e}). Pass --exe <path to ExpeditionsEditor.exe>.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--undo", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    exe = a.exe or find_editor()
    if not exe.is_file():
        raise SystemExit(f"No editor at {exe}")
    bak = Path(str(exe) + ".before_icons")
    d = bytearray(exe.read_bytes())
    offs = offsets(bytes(d))
    st = state(d, offs)
    print(f"{exe}: {st}")
    if a.check:
        return 0
    running = "expeditionseditor.exe" in subprocess.run(
        ["tasklist"], capture_output=True, text=True).stdout.lower()
    if running:
        raise SystemExit("Close the map editor first.")
    if a.undo:
        if not bak.is_file():
            raise SystemExit("No backup: this fix never changed this editor.")
        shutil.copy2(bak, exe)
        print("Put back.")
        return 0
    if st == "applied":
        print("Already applied.")
        return 0
    if st != "stock":
        raise SystemExit("This editor build is not the one the fix knows; nothing changed.")
    if input("Change 41 bytes so custom pictures show in the library? [y/n] ").strip().lower() != "y":
        return 0
    shutil.copy2(exe, bak)
    for k, v in code().items():
        d[offs[k]:offs[k] + len(v)] = v
    tmp = exe.with_name(exe.name + ".tmp")
    tmp.write_bytes(d)
    os.replace(tmp, exe)
    print("Applied:", state(exe.read_bytes(), offs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
