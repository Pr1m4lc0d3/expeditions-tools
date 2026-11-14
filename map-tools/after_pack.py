#!/usr/bin/env python3
"""Run this after EVERY "Pack terrain". It does everything a freshly packed map needs.

    python after_pack.py                     every level pak in Media\\Mods
    python after_pack.py --check             report only, write nothing
    python after_pack.py level_my_map        just this map

1. Custom models and road overlays: ../map-models/after_pack.py (a map with your own
   models on it otherwise drops back to the main menu).
2. Gateways: fix_gateway_links.py. Packing writes a link to another map as
   `level_<name> || <zone>`, a level that does not exist once the map is installed as
   `mod_level_<name>`; Let's go then drops the player to the main menu. It also fixes the
   same name in objectives that point at the other map, and stops on what crashes a
   crossing silently (no ground layers, a partner zone with no gateway, a zero cameraDir).
3. Regions: build_region.py refresh --all. A region keeps its own copy of each map's zones
   and contracts, so it goes stale with every pack. Hand-set values are kept.

Each step is a no-op on a map that does not need it. Close the game first: a running game
keeps the old pak open, and the tools refuse to write while it runs.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from saber import gamepaths  # noqa: E402

VARIANT = re.compile(r"_(nx64|pc|playstation_\d|xbox_\w+|stg_nx64)\.pak$")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if out:
        print("\n".join("   " + line.rstrip() for line in out.splitlines()))
    return r.returncode


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("levels", nargs="*", help="level names; default every level pak in Mods")
    ap.add_argument("--check", action="store_true", help="report only; write nothing")
    ap.add_argument("--mods", type=Path, default=None, help="the Mods folder, if it is not found")
    a = ap.parse_args()
    mods = a.mods or gamepaths.media_root("expeditions") / "Mods"
    apply = [] if a.check else ["--apply"]
    failed = 0

    print("== models and road overlays")
    models = HERE.parent / "map-models" / "after_pack.py"
    if models.is_file():
        failed += bool(run([sys.executable, str(models), *a.levels,
                            *(["--check"] if a.check else []), "--mods", str(mods)]))
    else:
        print(f"   {models} not found; keep map-tools next to map-models")
        failed += 1

    print("\n== gateway links")
    want = {o if o.startswith("level_") else f"level_{o}" for o in a.levels}
    paks = [p for p in sorted(mods.glob("level_*.pak"))
            if not VARIANT.search(p.name) and (not want or p.stem in want)]
    for p in paks:
        print(f"-- {p.name}")
        failed += bool(run([sys.executable, str(HERE / "fix_gateway_links.py"), str(p), *apply]))

    print("\n== regions")
    failed += bool(run([sys.executable, str(HERE / "build_region.py"), "refresh", "--all", *apply]))

    print("\ndry run; nothing written" if a.check else "\ndone")
    if failed:
        print(f"{failed} step(s) reported a problem; read the lines above")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
