#!/usr/bin/env python3
"""Run this after EVERY "Pack terrain" on a map that has your own models on it.

    python after_pack.py                     fix every level pak in Media\\Mods
    python after_pack.py --check             report only, write nothing
    python after_pack.py level_my_map        just this map

WHY. A custom Media model placed on a map drops the map back to the main menu:
the game registers the model under one name and looks it up under another, so it
never finds it. fix_map_model_brands.py renames the model in the packed scene to
the name the game registers (verified in game 2026-10-28). Packing rebuilds the
pak and erases the fix, so it has to run again after every pack.

It is safe to run when it is not needed. A pak with no custom models, or one that
is already fixed, is reported and left alone. Close the game first: a running game
keeps the old pak open and the write would not be what you test.

Only the PC master pak (level_<map>.pak) is touched. The console variants the
editor writes beside it (_pc, _nx64, _xbox_..., _playstation_...) are left alone.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".." / "fob-modules"))
from saber import gamepaths  # noqa: E402

VARIANT = re.compile(r"_(nx64|pc|playstation_\d|xbox_\w+|stg_nx64)\.pak$")


def level_paks(mods: Path, only: list[str]) -> list[Path]:
    """The master level paks in Mods, newest first, optionally filtered by name."""
    want = {o if o.startswith("level_") else f"level_{o}" for o in only}
    paks = [p for p in mods.glob("level_*.pak")
            if p.stat().st_size > 0 and not VARIANT.search(p.name)
            and (not want or p.stem in want)]
    return sorted(paks, key=lambda p: p.stat().st_mtime, reverse=True)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("levels", nargs="*", help="level names; default every level pak in Mods")
    ap.add_argument("--check", action="store_true", help="report only; write nothing")
    ap.add_argument("--mods", type=Path, default=None, help="the Mods folder, if it is not found")
    a = ap.parse_args()

    mods = a.mods or gamepaths.media_root("expeditions") / "Mods"
    paks = level_paks(mods, a.levels)
    if not paks:
        print(f"no level paks found in {mods}")
        return 1

    print(f"checking {len(paks)} level pak(s) in {mods}")
    fixed = failed = unsafe = 0
    for p in paks:
        print(f"\n--- {p.name}")
        if getattr(sys, "frozen", False):   # the packaged Map Fix exe runs the fix through itself
            cmd = [sys.executable, "--brands", str(p)]
        else:
            cmd = [sys.executable, str(HERE / "fix_map_model_brands.py"), str(p)]
        if not a.check:
            cmd.append("--apply")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = (r.stdout + r.stderr).strip()
        print("\n".join("   " + line.strip() for line in out.splitlines()))
        if r.returncode:
            failed += 1
        if "do not upload" in out:
            unsafe += 1
        if "wrote " in out:
            fixed += 1

    print()
    if unsafe:
        print(f"*** {unsafe} map(s) have a custom model whose textures are not shipped: do not upload them ***")
    if failed:
        print(f"{failed} pak(s) failed; read the lines above")
    print("dry run; nothing written" if a.check else f"done: {fixed} pak(s) rewritten")
    return 1 if failed or unsafe else 0


if __name__ == "__main__":
    sys.exit(main())
