#!/usr/bin/env python3
"""Validate a map's objectives against the official rules before the game does.

WHY THIS EXISTS

The objective layer fails silently. A contract with a dangling zone reference
hangs the Deploy screen with no popup. A scoutingInfo substage with a null
offset crashes outright. A reward of the wrong class simply never pays, and the
map still loads, still lists, and still plays - so nothing tells you.

Saber's own validator writes to `base\\logs\\ModMapError.txt` at PACK time, which
is 25 minutes too late and, on this install, has been empty every time it was
looked at. This runs in a second, against either the source or the pak.

    python check_objectives.py level_Diamondback_Divide
    python check_objectives.py level_Diamondback_Divide --pak
    python check_objectives.py --all

ERROR   = documented as breaking: a crash, a hang, or an unplayable map.
WARNING = documented as unsupported or removal-bound: it will not do what the
          author intended, but the map still runs.

Every rule below cites the page that establishes it. Nothing here is inferred
from how the shipped files happen to look; where the shipped files disagree with
the documentation, the documentation wins and the file is reported.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile

MEDIA = os.path.join(os.path.expanduser("~"), "Documents", "My Games", "Expeditions", "Media")

CONTRACT_TYPE = {0: "COMMON (Contract)", 1: "EXPEDITION"}
AFTER_STAGE = {0: "REWARD", 1: "DETACH", 2: "DESTROY", 3: "DO_NOTHING"}

# the eight Expeditions substage sections, per stages_in_expeditions.md
SUBSTAGES = ("objectConstruction", "takeInventory", "truckDelivery", "visitAllZones",
             "minigameInfo", "scoutingInfo", "seismographSettings", "freeRoamInfo")

# classes the docs mark "Not used in Expeditions; To be deleted"
DEAD_REWARDS = ("ContestResultRewards", "ObjectveRewardAddItemsToZone",
                "ObjectiveRewardsUpgradeGarage", "ObjectiveSingleRewardUnlockOnce")


class Report:
    def __init__(self):
        self.errors, self.warnings, self.standards = [], [], []

    def standard(self, where, msg, cite):
        """His build standard, not a Saber rule. Kept in its own bucket so the
        provenance of a finding is never ambiguous."""
        self.standards.append((where, msg, cite))

    def err(self, where, msg, cite):
        self.errors.append((where, msg, cite))

    def warn(self, where, msg, cite):
        self.warnings.append((where, msg, cite))


def zone_names(zone_json):
    try:
        return {z["name"] for z in zone_json["SslValue"]["zoneDesc"]}
    except Exception:
        return set()


def short(gid):
    """'~#@%$`*level_X~#@%$`* || Zone' -> 'Zone'. Returns '' for an unset ref."""
    if not isinstance(gid, dict):
        return ""
    s = gid.get("globalZoneId") or ""
    return s.split("||")[-1].strip() if "||" in s else ""


def zref(rec):
    """The JSON is inconsistent: takeInventory and scoutingInfo spell it
    zoneGlobalId, visitAllZones and objectConstruction spell it globalZoneId,
    truckDelivery spells it globalZoneDeliveryId. The docs say globalZoneId for
    all of them. Accept whichever is present."""
    for k in ("globalZoneId", "zoneGlobalId", "globalZoneDeliveryId"):
        if isinstance(rec.get(k), dict) and rec[k].get("globalZoneId"):
            return rec[k], k
    return None, "globalZoneId"


def check_zone_ref(rep, where, gid, names, field="globalZoneId"):
    z = short(gid)
    if not z:
        rep.err(where, f"{field} is not set",
                "common_fields_of_objectives - zone fields must name a zone")
        return
    if z not in names:
        rep.err(where, f"{field} -> {z!r} names no zone on this map",
                "a dangling ref hangs Deploy inside GetDefaultDeployZone")
    elif " " in z:
        rep.warn(where, f"{field} -> {z!r} contains a space",
                 "LOOT_ZONE_SPACE_BUG.md - holderId splits on whitespace")


def check_substage_common(rep, where, sub):
    if sub.get("visitWithDroneOnly") and not sub.get("mayVisitZoneWithDrone"):
        rep.err(where, "visitWithDroneOnly is on but mayVisitZoneWithDrone is off "
                       "- the zone cannot be reached by any means",
                "visitallzones.md")


def check_stage(rep, obj_name, i, st, names, is_expedition, this_level):
    where = f"{obj_name} stage[{i}]"
    filled = [k for k in SUBSTAGES if st.get(k) not in (None, [], {})]
    if not filled:
        rep.err(where, "no substage is filled in - the stage can never complete",
                "objectives_overview.md - at least one stage, each with an assignment")

    # optional_substages.md, explicit IMPORTANT: "stages where all substages are
    # optional are prohibited and will lead to an error"
    subs, opt = [], []
    for k in filled:
        v = st[k]
        recs = v if isinstance(v, list) else (
            v.get("zones") or v.get("zoneToFill") or v.get("zoneToTakeInvFrom")
            or v.get("scoutingsProp") or [])
        for r in (recs or []):
            if isinstance(r, dict):
                subs.append(r)
                if r.get("OptionalToPerform"):
                    opt.append(r.get("uiDesc") or "?")
    if subs and len(opt) == len(subs):
        rep.err(where, f"every substage here is OptionalToPerform ({len(opt)} of {len(subs)}) "
                       "- a stage with no required substage is prohibited and errors. "
                       "Turn one off, or move it into a stage that has a required substage",
                "optional_substages.md - explicit IMPORTANT")

    if "freeRoamInfo" in filled:
        fr = st["freeRoamInfo"]
        # freeroaminfo.md: "map - The name of the XML file of the map". Naming
        # THIS map is correct and documented. An earlier note in memory claimed
        # it had to be empty; clearing it on 2026-09-27 removed both maps from
        # the list. The real defect is naming a DIFFERENT map.
        m = fr.get("map") or ""
        if m and this_level.lower() not in m.lower():
            rep.err(where, f"freeRoamInfo.map = {m!r} but this map is {this_level!r} "
                           "- a Free Roam stage must name its own map",
                    "freeroaminfo.md")
        if len(filled) > 1:
            rep.err(where, f"freeRoamInfo shares a stage with {sorted(set(filled)-{'freeRoamInfo'})} "
                           "- a Free Roam Expedition must have exactly one stage "
                           "containing only freeRoamInfo",
                    "expeditions_and_contracts.md")

    for rec in (st.get("visitAllZones") or {}).get("zones") or []:
        w = f"{where} visitAllZones {rec.get('uiDesc') or '?'!r}"
        g, k = zref(rec)
        check_zone_ref(rep, w, g, names, k)
        check_substage_common(rep, w, rec)
        if rec.get("truckUid"):
            rep.warn(w, "truckUid is set but cannot be used in Expeditions",
                     "visitallzones.md - trucks are player-deployed")

    for rec in (st.get("objectConstruction") or {}).get("zoneToFill") or []:
        w = f"{where} objectConstruction {rec.get('uiDesc') or '?'!r}"
        g, k = zref(rec)
        check_zone_ref(rep, w, g, names, k)
        check_substage_common(rep, w, rec)
        if not rec.get("repairsNum") and not rec.get("fuelNum") and not rec.get("inventory"):
            rep.err(w, "none of repairsNum, fuelNum or inventory is set "
                       "- nothing to deliver",
                    "objectconstruction.md")
        for z in rec.get("additionalUnloadZones") or []:
            check_zone_ref(rep, w, z, names, "additionalUnloadZones")
        for z in rec.get("modelPlacementZones") or []:
            check_zone_ref(rep, w, z, names, "modelPlacementZones")

    for rec in (st.get("takeInventory") or {}).get("zoneToTakeInvFrom") or []:
        w = f"{where} takeInventory {rec.get('uiDesc') or '?'!r}"
        g, k = zref(rec)
        check_zone_ref(rep, w, g, names, k)
        check_substage_common(rep, w, rec)
        if not (rec.get("inventoryToTake") or []):
            rep.err(w, "inventoryToTake is empty - nothing to collect",
                    "takeinventory.md - at least one record required")

    for rec in st.get("truckDelivery") or []:
        w = f"{where} truckDelivery {rec.get('uiDesc') or '?'!r}"
        g, k = zref(rec)
        check_zone_ref(rep, w, g, names, k)
        # the doc says "Truck Uid"; the JSON field is truckId (122 vanilla records,
        # initial.cache_block; truckUid there is always "", 2026-11-13)
        if not (rec.get("truckId") or rec.get("truckUid")):
            rep.err(w, "truckId is not set - it must match the truck's Id in the editor",
                    "truckdelivery.md")
        a = rec.get("afterStageFinished")
        if a == 0:
            rep.warn(w, "afterStageFinished = REWARD, which does not work in Expeditions "
                        "and behaves as DO_NOTHING; use DESTROY, and grant the truck "
                        "via ObjectiveRewardItem or ObjectiveRewardUnlock",
                     "truckdelivery.md")
        for z in rec.get("additionalDeliveryZones") or []:
            check_zone_ref(rep, w, z, names, "additionalDeliveryZones")

    for rec in (st.get("scoutingInfo") or {}).get("scoutingsProp") or []:
        w = f"{where} scoutingInfo {rec.get('uiDesc') or '?'!r}"
        g, k = zref(rec)
        check_zone_ref(rep, w, g, names, k)
        # the documented crash: this field must carry a Vector2d even when zero
        if rec.get("localOffsetScoutingAreaRelativeZone") in (None, {}):
            rep.err(w, "localOffsetScoutingAreaRelativeZone is null - THIS CRASHES. "
                       "Select Vector2d and leave x/y at 0 if no offset is wanted",
                    "scoutinginfo.md - explicit WARNING")
        wc = rec.get("winCondition")
        if wc is not None and not (0 < float(wc) <= 1):
            rep.err(w, f"winCondition = {wc}; it is a fraction and must be >0 and <=1",
                    "scoutinginfo.md")
        if not rec.get("radius"):
            rep.err(w, "radius is 0 - the area to explore has no size", "scoutinginfo.md")

    for rew in (st.get("rewards") or {}):
        if rew in DEAD_REWARDS:
            rep.warn(f"{where} stage reward", f"{rew} is not used in Expeditions "
                     "and will never pay", "rewards.md")


def check_objective(rep, o, names, kind, this_level):
    name = o.get("name") or "<unnamed>"
    is_exp = kind == "contract" and o.get("contractType") == 1

    if not name.strip():
        rep.err("<unnamed>", "objective has no name", "common_fields_of_objectives.md")

    stages = o.get("stages") or []
    if not stages:
        rep.err(name, "no stages - the objective is unplayable",
                "objectives_overview.md - at least one stage required")
    for i, st in enumerate(stages):
        check_stage(rep, name, i, st, names, is_exp, this_level)

    for rew in (o.get("rewards") or {}):
        if rew in DEAD_REWARDS:
            rep.warn(name, f"reward {rew} is not used in Expeditions and will never pay",
                     "rewards.md")

    if o.get("isRepeatable") and kind != "task":
        rep.warn(name, "isRepeatable is on; in Expeditions it is valid for Tasks only",
                 "common_fields_of_objectives.md")

    # these three are Expedition-only per two explicit WARNINGs in the docs
    for f in ("requiredInventory", "requiredSpecialities", "requiredTruckAddons"):
        if (o.get(f) or []) and not is_exp:
            rep.warn(name, f"{f} is set on a {kind}; documented for Expeditions only",
                     "common_fields_of_objectives.md WARNING 1")
    if (o.get("bonusRewards") or {}) and not is_exp:
        rep.warn(name, "bonusRewards is set but is valid for Expeditions only",
                 "rewards.md")

    if kind == "task":
        if not short(o.get("taskGiverZone")) and not isinstance(o.get("taskGiverZone"), str):
            check_zone_ref(rep, name, o.get("taskGiverZone"), names, "taskGiverZone")
        elif not o.get("taskGiverZone"):
            rep.err(name, "a Task must name a taskGiverZone or the player cannot start it",
                    "tasks.md - A Must: Task Giver Zone")
        for z in o.get("additionalTaskGivers") or []:
            check_zone_ref(rep, name, z, names, "additionalTaskGivers")

    if kind == "contract":
        if o.get("failReasons"):
            rep.warn(name, "failReasons is set; documented as unused in Expeditions",
                     "expeditions_and_contracts.md")


def run(level, use_pak):
    rep = Report()
    if use_pak:
        p = None
        for cand in (f"{level}.pak", f"{level.lower()}.pak"):
            q = os.path.join(MEDIA, "Mods", cand)
            if os.path.isfile(q):
                p = q
                break
        if not p:
            print(f"no pak for {level}")
            return 2
        z = zipfile.ZipFile(p)
        obj = json.loads(z.read("objective_settings.json").decode("utf-8-sig"))
        zon = json.loads(z.read("zone_settings.json").decode("utf-8-sig"))
        z.close()
        src = os.path.basename(p)
    else:
        d = os.path.join(MEDIA, "prebuild", level.lower())
        if not os.path.isfile(os.path.join(d, "objective_settings.json")):
            d = os.path.join(MEDIA, "levels", level)   # build_objectives writes here
        try:
            obj = json.load(open(os.path.join(d, "objective_settings.json"), encoding="utf-8-sig"))
            zon = json.load(open(os.path.join(d, "zone_settings.json"), encoding="utf-8-sig"))
        except FileNotFoundError as e:
            print(f"{level}: {e}")
            return 2
        src = os.path.relpath(d, MEDIA)

    names = zone_names(zon)
    sv = obj.get("SslValue", obj)
    contracts = sv.get("contractsSettings") or []
    tasks = sv.get("tasksSettings") or []

    deploy = [z["name"] for z in zon["SslValue"]["zoneDesc"]
              if "ZonePropertyDeploy" in (z.get("props") or {})]
    exps = [c for c in contracts if c.get("contractType") == 1]

    print(f"=== {level}  ({src})")
    print(f"    {len(contracts)} contract(s): {len(exps)} Expedition, "
          f"{len(contracts)-len(exps)} Contract   {len(tasks)} task(s)   "
          f"{len(deploy)} deploy zone(s)")

    # A map's identity lives in user_name_of_map.txt. A clone that keeps the
    # original's token makes TWO maps claim one id, and the game loads whichever
    # it resolves first - which reads exactly like "this is an older version of
    # my map". level_Diamondback_Divide_2 shipped this way.
    for root in ("prebuild", "levels"):
        base = os.path.join(MEDIA, root)
        if not os.path.isdir(base):
            continue
        hit = [d for d in os.listdir(base) if d.lower() == level.lower()]
        if not hit:
            continue
        tok_p = os.path.join(base, hit[0], "user_name_of_map.txt")
        if not os.path.isfile(tok_p):
            continue
        tok = open(tok_p, encoding="utf-8-sig").read().strip().replace("~#@%$`*", "")
        if tok and tok.lower() != level.lower():
            rep.err(level, f"{root}/{hit[0]}/user_name_of_map.txt says {tok!r} "
                           f"- this map is claiming another map's identity. Two paks "
                           f"with one id means the game loads whichever it finds first",
                    "objectives_overview.md - reusing settings requires changing the map name")

    # the two hard prerequisites for a loadable map
    if not exps:
        rep.err(level, "no Expedition (contractType EXPEDITION) - the map cannot be loaded",
                "objectives_overview.md - Required Expedition")
    if not deploy:
        rep.err(level, "no Deploy zone - the map cannot be loaded",
                "objectives_overview.md / deploy_zones.md")

    # objectives_overview.md: "The single required Expedition may be a Free Roam
    # one." So one Free Roam Expedition satisfies the load requirement on its own,
    # and every map here is expected to offer Free Roam regardless of what else
    # it ships. A rule that lives only in prose does not fire; this does.
    free = [c for c in exps
            if any((st.get("freeRoamInfo") or {}) for st in (c.get("stages") or []))]
    if not free:
        rep.standard(level, "no Free Roam Expedition. Free Roam is always offered on "
                            "his maps, and one Free Roam Expedition is by itself enough "
                            "to satisfy the required-Expedition rule. Add an Expedition "
                            "with a single stage containing only freeRoamInfo",
                     "his build standard + objectives_overview.md Required Expedition")
    elif len(free) > 1:
        rep.warn(level, f"{len(free)} Free Roam Expeditions ({[c['name'] for c in free]}); "
                        "one is the convention", "expeditions_and_contracts.md")

    seen = {}
    for o in contracts + tasks:
        n = (o.get("name") or "").strip()
        if n in seen:
            rep.err(n, f"duplicate objective name (also a {seen[n]}) - names must be "
                       "unique across every objective type",
                    "common_fields_of_objectives.md WARNING")
        seen[n] = "contract" if o in contracts else "task"

    for c in contracts:
        check_objective(rep, c, names, "contract", level)
    for t in tasks:
        check_objective(rep, t, names, "task", level)

    for tag, rows in (("ERROR", rep.errors), ("WARNING", rep.warnings),
                      ("STANDARD", rep.standards)):
        for where, msg, cite in rows:
            print(f"    {tag:<7} {where}\n             {msg}\n             [{cite}]")
    if not (rep.errors or rep.warnings or rep.standards):
        print("    clean - every documented rule holds, Free Roam offered")
    elif not rep.errors:
        print(f"    no errors; {len(rep.warnings)} warning(s), "
              f"{len(rep.standards)} standard(s)")
    return 1 if rep.errors else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level", nargs="?")
    ap.add_argument("--pak", action="store_true", help="read the packed map instead of the source")
    ap.add_argument("--all", action="store_true", help="every level under Media\\prebuild")
    a = ap.parse_args()
    if a.all:
        rc = 0
        for d in sorted(os.listdir(os.path.join(MEDIA, "prebuild"))):
            if not d.startswith("level_"):
                continue
            if not os.path.isfile(os.path.join(MEDIA, "prebuild", d, "objective_settings.json")):
                continue
            rc |= run(d, a.pak)
            print()
        return rc
    if not a.level:
        ap.error("give a level name or --all")
    return run(a.level, a.pak)


if __name__ == "__main__":
    raise SystemExit(main())
