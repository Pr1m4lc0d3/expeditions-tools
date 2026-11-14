#!/usr/bin/env python3
"""Tell me, in one screen, whether the pack just destroyed the map again.

The editor's pack step has twice written a skeleton `zone_settings.json`,
silently dropping every zone property on the map - loot, deploy, recovery and
the bridge construction zones. The file is still valid JSON and the map still
loads, so nothing announces it. The only way to know is to count.

    python check_pack.py level_Diamondback_Divide
    python check_pack.py level_Diamondback_Divide --restore-source

`--restore-source` copies a known-good copy of the level's source (pass it with
`--snapshot DIR`, or set CHECK_PACK_SNAPSHOT) back over `Media\\prebuild\\<level>`
so you can simply pack again.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sys
import zipfile

MEDIA = os.path.join(os.path.expanduser("~"), "Documents", "My Games", "Expeditions", "Media")
SNAP = os.environ.get("CHECK_PACK_SNAPSHOT", "")   # a known-good copy of the level source; --snapshot


def load_zones(raw: bytes):
    d = json.loads(raw.decode("utf-8-sig"))
    return d.get("SslValue", d).get("zoneDesc", [])


def summarise(zl):
    return {
        "zones": len(zl),
        "with_props": len([z for z in zl if z.get("props")]),
        "construction": sorted(z["name"] for z in zl
                               if "ZonePropertyObjectConstructionInZone" in z.get("props", {})),
        "storage": sorted(z["name"] for z in zl
                          if "ZonePropertyInventoryStorage" in z.get("props", {})),
        "spaced_names": sorted(z["name"] for z in zl if " " in z["name"]),
    }


def report(level: str) -> int:
    pre = os.path.join(MEDIA, "prebuild", level.lower())
    pak = os.path.join(MEDIA, "Mods", f"{level}.pak")
    rows = []

    src = os.path.join(pre, "zone_settings.json")
    if os.path.isfile(src):
        rows.append(("prebuild (source)", summarise(load_zones(open(src, "rb").read()))))
    if os.path.isfile(pak):
        z = zipfile.ZipFile(pak)
        rows.append(("packed map", summarise(load_zones(z.read("zone_settings.json")))))
        obj = z.read("objective_settings.json").decode("utf-8-sig", errors="replace")
        zon = z.read("zone_settings.json").decode("utf-8-sig", errors="replace")
        names = {x["name"] for x in load_zones(z.read("zone_settings.json"))}
        # BOTH files. Diamondback's three dangling refs lived in zone_settings
        # -- fobModulesZones, mainBaseModuleInteractionZone,
        # supplyShopModuleInteractionZone -- so scanning objective_settings
        # alone reported OK on a map whose Deploy screen was crashing.
        refs = set(re.findall(r"\|\|\s*([^\"]+)\"", obj + zon))
        dangling = sorted(r.strip() for r in refs if r.strip() not in names)
        # A free-roam stage on THIS map leaves freeRoamInfo.map empty. Naming
        # the map's own sentinel is a self-reference and the Expedition never
        # appears in the Mods Region, even though the map installs and lists.
        # An EMPTY freeRoamInfo.map is the defect: it means "no map", and the
        # game loads a DIFFERENT level -- Shafer Trail opened into Diamondback
        # Divide. Diamondback and Ozark, the two that load their own terrain,
        # both name themselves here.
        blankmap = obj.count('"freeRoamInfo"') and re.findall(
            r'"freeRoamInfo"\s*:\s*\{[^}]*?"map"\s*:\s*""', obj)
        freeroams = len(re.findall(r'"freeRoamInfo"\s*:\s*\{', obj))
        z.close()
    else:
        dangling, blankmap, freeroams = [], [], 0

    # Every zone name must exist in the baked scene. The deploy view resolves
    # each deploy zone against [scene]/data_for_ssl.bin, which is written at pack
    # time and cannot be edited. A name that is not there gives an empty Deploy
    # screen and a hang: no popup, no log line, no crash dump. Renaming zone ids
    # inside an already-packed map caused exactly this on 2026-09-27, and without
    # this check it went unnoticed for a week.
    scene_miss, scene_miss_deploy = [], []
    if os.path.isfile(pak):
        z = zipfile.ZipFile(pak)
        if "[scene]/data_for_ssl.bin" in z.namelist():
            blob = z.read("[scene]/data_for_ssl.bin")
            zd = load_zones(z.read("zone_settings.json"))
            dep = {x["name"] for x in zd
                   if "ZonePropertyDeploy" in (x.get("props") or {})}
            for x in zd:
                n = x["name"]
                if not (blob.count(n.encode("utf-8"))
                        or blob.count(n.encode("utf-16-le"))):
                    scene_miss.append(n)
                    if n in dep:
                        scene_miss_deploy.append(n)
        z.close()

    for tag, s in rows:
        print(f"\n{tag}")
        print(f"   zones {s['zones']}   with properties {s['with_props']}")
        print(f"   construction ({len(s['construction'])}): {s['construction']}")
        print(f"   storage      ({len(s['storage'])})")
        if s["spaced_names"]:
            print(f"   !! ids containing a space (cannot be looted): {s['spaced_names']}")

    ok = True
    if len(rows) == 2:
        src_s, pak_s = rows[0][1], rows[1][1]
        lost = src_s["with_props"] - pak_s["with_props"]
        if lost > 0:
            print(f"\n!! THE PACK DROPPED {lost} ZONES' PROPERTIES "
                  f"({src_s['with_props']} -> {pak_s['with_props']})")
            print("   run again with --restore-source, then pack once more")
            ok = False
        # Only a defect if the SOURCE had construction zones to lose. A map with
        # no bridges was being reported NOT OK for having no bridges.
        if src_s["construction"] and not pak_s["construction"]:
            print("\n!! NO CONSTRUCTION ZONES IN THE PACKED MAP - no bridge can be built")
            ok = False
    if blankmap:
        print(f"\n!! {len(blankmap)} free-roam stage(s) have an EMPTY freeRoamInfo.map")
        print("   Empty means NO map, not THIS map: the game loads a different")
        print("   level. Shafer Trail opened into Diamondback Divide this way.")
        print("   Set it to this map's own ~#@%$`*level_<name>~#@%$`* token.")
        ok = False
    if freeroams > 1:
        print(f"\n?  {freeroams} free-roam contracts. Every working map here has"
              " exactly one;")
        print("   use visitAllZones stages for the other expeditions.")
    if scene_miss:
        print(f"\n!! {len(scene_miss)} zone name(s) are NOT in the baked scene:")
        print(f"   {scene_miss}")
        print("   the pack bakes zone ids into [scene]/data_for_ssl.bin and that")
        print("   file cannot be edited. zone_settings.json must match it exactly.")
        if scene_miss_deploy:
            print(f"   !! {len(scene_miss_deploy)} of them are DEPLOY zones: {scene_miss_deploy}")
            print("      the Deploy screen builds empty and the game hangs:")
            print("      no popup, no log line, no crash dump")
        print("   fix: python sync_zone_names.py <Level>")
        ok = False

    if dangling:
        print(f"\n!! zone references that point at nothing: {dangling}")
        print("   this is what makes the deploy screen hang")
        for d in dangling:
            alt = d.replace("_", " ")
            print(f"      {d!r} -> the pack re-spaced it; try {alt!r}")
        ok = False
    print("\nOK - the pack preserved everything" if ok else "\nNOT OK - see above")
    return 0 if ok else 1


def restore(level: str) -> int:
    pre = os.path.join(MEDIA, "prebuild", level.lower())
    if not SNAP or not os.path.isdir(SNAP):
        return print(f"no snapshot at {SNAP!r}; pass --snapshot DIR") or 1
    n = 0
    for f in ("zone_settings.json", "objective_settings.json", "_zones.xml"):
        s = os.path.join(SNAP, f)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(pre, f)); n += 1
    for s in glob.glob(os.path.join(SNAP, "subgroups", "Zone_*.xml")):
        shutil.copy2(s, os.path.join(pre, "subgroups", os.path.basename(s))); n += 1
    print(f"restored {n} source file(s) into {pre}\nnow pack again")
    return 0



def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level")
    ap.add_argument("--restore-source", action="store_true")
    ap.add_argument("--snapshot", help="known-good copy of the level source, for --restore-source")
    a = ap.parse_args()
    if a.snapshot:
        global SNAP
        SNAP = a.snapshot
    return restore(a.level) if a.restore_source else report(a.level)


if __name__ == "__main__":
    raise SystemExit(main())
