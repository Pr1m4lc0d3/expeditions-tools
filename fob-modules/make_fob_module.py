#!/usr/bin/env python3
r"""Author a custom FOB module for ANY of the 14 types, from Saber's own schema.

THE POINT
---------
The editor offers all fourteen FOB modules for creation, so a correct template
exists. Saber documented it badly, but `Sources\BinEditor\meta_desc_ssl.ps`
states, per type, every member that exists, its type, and its `__defaultValue`.
That schema IS the template.

Everything built in this project before today was made by copying an existing
descriptor and editing it. That is why a confound got into the Generator work:
the "fixed" build inherited 22 unchanged fields from a 2025 file, so when both
broke there was no way to tell a shared inherited fault from the property type.
⛔ Stop copying descriptors. Author from the schema.

    python make_fob_module.py --list
    python make_fob_module.py GENERATOR "Nom Noms Overunity Machine" \
        --uiname "Nom Nom's Over-Unity Machine" --desc "..." --cost 35000
    python make_fob_module.py --all-stubs      # emit one of every type, validate

WHAT IT GUARANTEES
------------------
* every emitted member exists in the schema for that type (chain-resolved)
* every value is the schema's documented default unless explicitly overridden
* the moduleId <-> zoneProperty pairing comes from the shipped docs table
* `isInteractive` comes from the shipped docs table, not from preference
* it REFUSES to emit a field the schema does not declare

⛔ WHAT IT CANNOT PROMISE. A schema-valid module is not a working module.
Measured 2026-10-16: a Generator whose zoneProperty matched Saber's live
GENERATOR on every inventory field STILL killed the base-building store, dying in
`InitZoneInventory` at instr 36 while `PopupBaseBuildingUiController.CreateListData`
built the shop list. So `GENERATOR` is flagged below. The flags are observations,
⛔ not schema facts, and each names its evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# moduleId -> zoneProperty $type. Source: shipped docs table, FOB_MODULE_SPEC.md.
PAIRING = {
    "BASE_MODULE":     "ZonePropertyFobModuleStorage",
    "SUPPLY_SHOP":     "ZonePropertySupplyShop",
    "GAS_STATION":     "ZonePropertyFobModuleStorage",
    "EXTRA_STORAGE":   "ZonePropertyFobModuleStorage",
    "REPAIR_ZONE":     "ZonePropertyFobModuleStorage",
    "TOW_TRUCK":       "ZonePropertyTowTruck",
    "DOUBLE_STOCK":    "ZonePropertyFobModuleStorage",
    "PARKING":         "ZonePropertyParking",
    "WORKSHOP":        "ZonePropertyWorkshop",
    "SERVICE_STATION": "ZonePropertyServiceStation",
    "DRONE_BASE":      "ZonePropertyDroneDelivery",
    "HELICOPTER":      "ZonePropertyHelicopter",
    "GENERATOR":       "ZonePropertyGenerator",
    "ANTENNA":         "ZonePropertyFobModuleDefault",
}

# isInteractive per property type. Source: shipped docs table. NOT a preference.
INTERACTIVE = {
    "ZonePropertyFobModuleStorage": True,
    "ZonePropertySupplyShop":       True,
    "ZonePropertyParking":          True,
    "ZonePropertyWorkshop":         True,
    "ZonePropertyDroneDelivery":    True,
    "ZonePropertyTowTruck":         False,
    "ZonePropertyServiceStation":   False,
    "ZonePropertyFobModuleDefault": False,
    "ZonePropertyGenerator":        True,
    "ZonePropertyHelicopter":       False,
}

# Non-default values a type genuinely requires, each with its reason.
REQUIRED = {
    "ZonePropertyFobModuleStorage": {"isAtDeployZone": True},   # spec: required
    "ZonePropertyGenerator":        {"isAtDeployZone": True},   # inherits storage
    "ZonePropertyDroneDelivery":    {"isAtDeployZone": True},
}

# Observed-in-game status. ⛔ observations, not schema facts.
STATUS = {
    "ZonePropertyFobModuleStorage": ("OK",
        "PebbleGas works in game (confirmed in game)"),
    "ZonePropertyDroneDelivery": ("OK",
        "Nom Noms works in game (confirmed in game 2026-10-15)"),
    "ZonePropertyGenerator": ("BREAKS",
        "kills the base-building store: InitZoneInventory instr 36 during "
        "PopupBaseBuildingUiController.CreateListData. 4 installs, passing "
        "no-pak control, and its zoneProperty matched Saber's live GENERATOR on "
        "every inventory field"),
    "ZonePropertyFobModuleDefault": ("INERT",
        "antenna installs and deploys, ability never fires: "
        "UpdateAntennasForLevel walks deployZonesWithUpgrade, mod entry empty"),
    "ZonePropertyHelicopter": ("OK",
        "Helipad Test works in game (confirmed in game 2026-10-25)"),
    "ZonePropertyServiceStation": ("UNREPRODUCED", "worked 03-2025, absent since"),
    "ZonePropertyParking": ("OK",
        "Parking Test works in game (confirmed in game 2026-10-25)"),
    "ZonePropertyTowTruck": ("OK",
        "Tow Truck Test works in game (confirmed in game 2026-10-25)"),
    "ZonePropertyWorkshop": ("OK",
        "Platt's Gate Works works in game (confirmed in game 2026-10-24)"),
}


def _load_art():
    p = os.path.join(HERE, "vanilla_fob_art.json")
    if not os.path.isfile(p):
        return {}
    return json.load(open(p, encoding="utf-8")).get("modules", {})


VANILLA_ART = _load_art()


def schema(type_name):
    r = subprocess.run([sys.executable, os.path.join(HERE, "schema_members.py"),
                        type_name, "--json"], capture_output=True, text=True,
                       encoding="utf-8")
    if r.returncode or not r.stdout.strip().startswith("{"):
        return None
    return json.loads(r.stdout)


def coerce(spec):
    """The schema's documented default, as a JSON value."""
    t = spec.get("type", "")
    if "default" in spec:
        d = spec["default"].strip().rstrip(",")
        if d in ("True", "true"):
            return True
        if d in ("False", "false"):
            return False
        if re.fullmatch(r"-?\d+", d):
            return int(d)
        if re.fullmatch(r"-?\d*\.\d+", d):
            return float(d)
        m = re.fullmatch(r'"(.*)"', d)
        if m:
            return m.group(1)
        return d
    if spec.get("nullable") or spec.get("ptr"):
        return None
    if t == "bool":
        return False
    if t == "int":
        return 0
    if t == "float":
        return 0.0
    if t == "string":
        return ""
    return None


def build_zone_property(prop_type):
    sch = schema(prop_type)
    if sch is None:
        return None, "type %r not in schema" % prop_type
    zp = {"$type": prop_type}
    for name, spec in sorted(sch["members"].items()):
        zp[name] = coerce(spec)
    for k, v in REQUIRED.get(prop_type, {}).items():
        if k not in zp:
            return None, "required override %r is not a schema member" % k
        zp[k] = v
    return zp, sch["chain"]


def build(module_id, identifier, ui_name=None, desc=None, cost=None):
    prop_type = PAIRING.get(module_id)
    if prop_type is None:
        return None, "unknown moduleId %r" % module_id
    zp, chain = build_zone_property(prop_type)
    if zp is None:
        return None, chain
    if "moduleId" in zp:
        zp["moduleId"] = module_id

    desc_sch = schema("BaseUpgradeDesc")
    v = {}
    for name, spec in sorted(desc_sch["members"].items()):
        v[name] = coerce(spec)
    v["name"] = "BaseUpgradeDesc"

    # Saber's own art symbols and model class names for this module, read from
    # the live object graph (vanilla_fob_art.json). The shipped docs list these
    # NOWHERE, and the schema cannot supply them -- they are plain strings whose
    # legal values are a closed set of UI symbols and model classes. Without
    # them a module emits `symbol.unknown` and `model.missing` and shows no art.
    # ⛔ Custom icon20/icon40/iconMap40/iconTrade are NOT supported for FOB
    # modules, so reusing Saber's symbols is the correct move, not a shortcut;
    # only bg / bgSmall / bgRight may be custom PNGs.
    art = VANILLA_ART.get(module_id, {})
    for k, val in art.items():
        if k in desc_sch["members"] and val not in (None, ""):
            v[k] = val
    v["zoneProperty"] = zp
    v["isInteractive"] = INTERACTIVE.get(prop_type, True)
    v["animationStagesProgress"] = {"stage_0": 0, "stage_1": 1}
    v["animationAdditionalStagesProgress"] = {"stage_0": 0, "stage_1": 1}
    v["uiName"] = ui_name or identifier
    v["uiDescTradeHeader"] = ui_name or identifier
    if desc:
        v["uiDesc"] = desc
        v["uiTradeDefaultDescText"] = desc
    if cost is not None:
        v["cost"] = int(cost)
        v["sellPrice"] = int(cost)

    # refuse any member the schema does not declare
    bad = [k for k in v if k != "name" and k not in desc_sch["members"]]
    if bad:
        return None, "would emit non-schema fields: %s" % bad
    badzp = [k for k in zp if k != "$type" and k not in schema(prop_type)["members"]]
    if badzp:
        return None, "zoneProperty would emit non-schema fields: %s" % badzp

    # The identifier lives at the TOP LEVEL, as a sibling of SslType/SslValue,
    # while SslValue.name is the literal class name. Confirmed 4/4 across every
    # installed mod: PebbleGas, Essentials Storage, Nom Noms, Gimbo's all carry
    # top-level name=<identifier> and SslValue.name="BaseUpgradeDesc".
    # ⛔ This is NOT in meta_desc_ssl.ps -- the schema lists no `name` member on
    # BaseUpgradeDesc at all, so it cannot be derived from the schema and is only
    # visible by reading a real pak. Omitting it costs you `name.missing`.
    return {"SslType": "BaseUpgradeDesc", "SslValue": v, "name": identifier}, chain


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("module_id", nargs="?")
    ap.add_argument("identifier", nargs="?")
    ap.add_argument("--uiname")
    ap.add_argument("--desc")
    ap.add_argument("--cost", type=int)
    ap.add_argument("--out", default="fob_build")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all-stubs", action="store_true")
    a = ap.parse_args()

    if a.list or (not a.module_id and not a.all_stubs):
        print("%-16s %-32s %-14s %s" % ("moduleId", "zoneProperty", "status", "evidence"))
        print("-" * 110)
        for mid in sorted(PAIRING):
            pt = PAIRING[mid]
            st, why = STATUS.get(pt, ("UNTESTED", "no custom module of this type has been run"))
            print("%-16s %-32s %-14s %s" % (mid, pt, st, why[:58]))
        return 0

    targets = sorted(PAIRING) if a.all_stubs else [a.module_id]
    ok = fail = 0
    for mid in targets:
        ident = a.identifier if not a.all_stubs else "SchemaStub %s" % mid
        src, info = build(mid, ident, a.uiname, a.desc, a.cost)
        if src is None:
            print("  FAIL %-16s %s" % (mid, info))
            fail += 1
            continue
        folder = os.path.join(a.out, "BaseUpgradeDesc_" + ident)
        os.makedirs(folder, exist_ok=True)
        blob = json.dumps(src, indent=4, ensure_ascii=False)
        with open(os.path.join(folder, "BaseUpgradeDesc"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(blob)
        zp = src["SslValue"]["zoneProperty"]
        st, _ = STATUS.get(zp["$type"], ("UNTESTED", ""))
        print("  OK   %-16s %-32s %2d zp fields  %-13s %s"
              % (mid, zp["$type"], len(zp) - 1, st, folder))
        ok += 1
    print("\n  %d built, %d refused" % (ok, fail))
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
