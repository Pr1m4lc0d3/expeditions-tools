"""finish_map.py -- write the settings a map needs before it will pack.

`build_map.py` strips the donor's content, and it stripped too much: deleting
`zone_settings.json` and `objective_settings.json` leaves a map that opens, builds
and drives in the editor and then will not pack. Every map on disk that packs has
both, and a cloned map keeps the donor's `level_desc.json` previews pointing at
the wrong images.

So this writes the three files that make a map a MAP rather than a terrain:

    zone_settings.json       what each zone in _zones.xml actually does
    objective_settings.json  at least one contract, or there is nothing to pack
    level_desc.json          its own preview images, not the donor's

A deploy zone needs `maxTrucksNum` above zero or the deploy button never lights,
and one zone must carry `defaultDeployPoint` or there is nowhere to start.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from saber import project

PREBUILD = project.PREBUILD


def zone_settings(level, zones, template="level_Diamondback_Divide"):
    """Clone a WORKING deploy property rather than writing one from memory.

    Hand-writing this failed four times. The property is cloned verbatim from a
    map whose deploy zones work, and only the name and truck count are changed.

    The reference matters: Ozark's source omits `needToShow`,
    `isAffectedByZoneSettings` and `isShown`, Diamondback's has all three, and
    Diamondback is the map that works. Diffing against the wrong example is how
    those fields got added, then removed again, then found to be needed.
    """
    import copy
    src = PREBUILD / template / "zone_settings.json"
    proto = None
    if src.is_file():
        d = json.loads(src.read_text(encoding="utf8"))
        for z in d["SslValue"]["zoneDesc"]:
            if "ZonePropertyDeploy" in z.get("props", {}):
                proto = z
                break
    if proto is None:
        raise RuntimeError(f"no deploy zone to clone from {template}")

    desc = []
    for name, default, trucks in zones:
        z = copy.deepcopy(proto)
        z["name"] = name
        p = z["props"]["ZonePropertyDeploy"]
        p["defaultDeployPoint"] = bool(default)
        p["maxTrucksNum"] = int(trucks)
        desc.append(z)
    return {"SslType": "MapWithZonesDesc",
            "SslValue": {"isNeedToBeCloacked": True,
                         "name": f"~#@%$`*{level}~#@%$`*",
                         "zoneDesc": desc}}


def objective_settings(title, description, deploy,
                       template="level_Diamondback_Divide"):
    """Clone a working contract too, for the same reason."""
    import copy
    src = PREBUILD / template / "objective_settings.json"
    d = json.loads(src.read_text(encoding="utf8"))
    proto = None
    for c in d["SslValue"]["contractsSettings"]:
        if c.get("contractType") == 1 and c.get("stages"):
            proto = c
            break
    if proto is None:
        raise RuntimeError(f"no type-1 contract with stages in {template}")
    c = copy.deepcopy(proto)
    c["name"] = title
    c["description"] = description
    c["uiDesc"] = description
    # Diamondback leaves this empty on a working map; naming the zone here is an
    # untested deviation and this has cost enough attempts already.
    c["recommendedDeployZone"] = ""
    return {"ExternalData": d.get("ExternalData", []),
            "SslType": "ObjectiveSettings",
            "SslValue": {"contractsSettings": [c], "employer": [],
                         "name": "objectiveSettings", "tasksSettings": []}}


def level_desc(preview, save_preview):
    return {"SslType": "LevelDesc",
            "SslValue": {"globalMapPreview": preview,
                         "saveSlotMapPreview": save_preview},
            "popupSettings": {"objectives": [], "uiIcon": "", "uiText": "",
                              "uiTitle": ""}}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level", help="folder name, e.g. level_hells_revenge")
    ap.add_argument("--title", default=None)
    ap.add_argument("--description", default="")
    ap.add_argument("--trucks", type=int, default=4)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    folder = PREBUILD / args.level
    if not folder.is_dir():
        print(f"  !! {folder} not found")
        return 2
    title = args.title or args.level.replace("level_", "").replace("_", " ").title()

    zx = folder / "_zones.xml"
    names = re.findall(r'\bId="([^"]+)"', zx.read_text(encoding="utf8")) if zx.exists() else []
    if not names:
        print("  !! no zones in _zones.xml -- a map with no deploy zone cannot be entered")
        return 2
    print(f"zones found: {', '.join(names)}")
    zones = [(n, i == 0, args.trucks) for i, n in enumerate(names)]

    plan = {
        "zone_settings.json": zone_settings(args.level, zones),
        "objective_settings.json": objective_settings(
            title, args.description or f"Open exploration of {title}.", names[0]),
        "level_desc.json": level_desc(f"{args.level}_preview",
                                      f"{args.level}_save"),
    }
    for fn, data in plan.items():
        p = folder / fn
        state = "overwrite" if p.exists() else "create"
        print(f"  {state:<9} {fn}")
        if args.apply:
            p.write_text(json.dumps(data, indent=4), encoding="utf8")

    # a leftover source heightmap from the donor would import the WRONG terrain
    for stray in folder.glob("_height_source.png*"):
        print(f"  {'remove':<9} {stray.name}  (donor leftover; would import the "
              f"wrong terrain via Copy Source Heightmap)")
        if args.apply:
            stray.unlink()

    print(f"\ndeploy zone '{names[0]}' is the default, maxTrucksNum {args.trucks}")
    if not args.apply:
        print("(dry run -- pass --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
