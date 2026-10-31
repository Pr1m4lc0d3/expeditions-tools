#!/usr/bin/env python
"""Validate custom FOB module mods (BaseUpgradeDesc) against the official rules.

Usage:
    python check_fob_module.py <path>            # a mod folder, a .pak, or a tree to walk
    python check_fob_module.py <path> --json     # machine-readable

A "mod" here is the editor's EXP MODs output: a folder named
``BaseUpgradeDesc_<identifier>`` holding a JSON file named ``BaseUpgradeDesc``,
plus the packed ``.pak`` / per-platform ``.zip`` files produced by File > Pack.

Every rule below is sourced from either the shipped guides in ``docs/`` or from
``Sources\\BinEditor\\meta_desc_ssl.ps`` (the engine schema).  Rule ids are stable
so callers can filter.  Labels follow check_objectives.py:

    ERROR    breaks the module in game
    WARNING  works but is wrong or fragile
    STANDARD our own house rule, not Saber's
"""

import argparse
import io
import json
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from saber import gamepaths  # noqa: E402

GAME = str(gamepaths.game_dir("expeditions"))
SCHEMA = os.path.join(GAME, r"Sources\BinEditor\meta_desc_ssl.ps")
INITIAL_PAK = os.path.join(GAME, r"preload\paks\client\initial.pak")
GFX_PAK = os.path.join(GAME, r"preload\paks\client\gfx.pak")

DESC_MEMBER = "BaseUpgradeDesc"

# A correct pack produces TWO paks: `<Name>.pak` carries the descriptor, and
# `<Name>_<part>.pak` carries only `prebuild/textures/pct/*.pct`.  The companion
# is texture-only BY DESIGN -- see docs/CUSTOM_FOB_MODEL_PIPELINE.md, "What a
# correct pack produces".  Demanding a descriptor from it is a false positive,
# and it fired on PebbleGas, the one module with a confirmed-working custom model.
PLATFORM_PARTS = ("pc", "nx64", "playstation_4", "playstation_5",
                  "xbox_one", "xbox_series")


def platform_part(pak):
    """The build part a companion pak is for, or None if it is the main pak."""
    stem = os.path.splitext(os.path.basename(pak))[0]
    for part in PLATFORM_PARTS:
        if stem.endswith("_" + part):
            return part
    return None


# ---------------------------------------------------------------------------
# Rules from docs/custom_gameplay_entities__fob_modules__* and
# docs/map_modding__..__zones_of_fob_modules__*
# ---------------------------------------------------------------------------

# The only two classes the editor offers.
VALID_SSL_TYPES = {"BaseUpgradeDesc", "AntennaUpgradeDesc"}

# "Identifiers and Zones of Original In-Game FOB Modules" table, verbatim.
# moduleId -> required zoneProperty $type.  None means the field must be null.
VANILLA_MODULES = {
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
    "ANTENNA":         None,
}

# "Interaction with Player" table.  zoneProperty $type -> required isInteractive.
# AntennaUpgradeDesc is keyed by class, not by prop, and is handled separately.
# Zone property types that inherit ZonePropertyInventoryStorage, read out of the
# editor's own meta_desc_ssl.ps. They share InitZoneInventory, so they share the
# alwaysSpawn requirement below.
INVENTORY_BACKED = {
    "ZonePropertyDroneDelivery",
    "ZonePropertyFobModuleStorage",
    "ZonePropertyGenerator",
    "ZonePropertyInventoryModelBreakageQuest",
    "ZonePropertyInventoryStorageQuest",
    "ZonePropertyParking",
}

INTERACTIVE_BY_PROP = {
    "ZonePropertyFobModuleStorage": True,
    "ZonePropertySupplyShop":       True,
    "ZonePropertyTowTruck":         False,
    "ZonePropertyParking":          True,
    "ZonePropertyWorkshop":         True,
    "ZonePropertyServiceStation":   False,
    "ZonePropertyDroneDelivery":    True,
}

# "Types of Zones of FOB Modules" -- the closed list of props a module may use,
# plus ZonePropertyFobModuleDefault which the moduleId section names explicitly.
VALID_ZONE_PROPS = set(INTERACTIVE_BY_PROP) | {
    "ZonePropertyFobModuleDefault",
    "ZonePropertyHelicopter",
    "ZonePropertyGenerator",
}

UI_NAME_MAX = 20   # "should not exceed 20 characters, including spaces"
UI_DESC_MAX = 240  # "should not exceed 240 characters, including spaces"

# Fields the docs mark dead.  Setting them is harmless but misleading.
INERT_FIELDS = {
    "zoneProperty.fuelLevel":
        "docs: 'not used. You should use fuelSlotSettings instead.'",
    "zoneProperty.fuelAmountForOneConvert":
        "docs: 'not used; this setting will be removed soon.'",
}


# ---------------------------------------------------------------------------
# reference data pulled live from the install, with graceful degradation
# ---------------------------------------------------------------------------

def _schema_blocks():
    """Map type name -> (parent, [member names]) from meta_desc_ssl.ps."""
    if not os.path.exists(SCHEMA):
        return {}
    lines = open(SCHEMA, encoding="utf-8", errors="replace").read().split("\n")
    starts = {}
    for i, line in enumerate(lines):
        m = re.match(r"^(\w+)   =   \{", line)
        if m:
            starts.setdefault(m.group(1), i)
    out = {}
    for name, i in starts.items():
        depth = 0
        body = []
        for j in range(i, len(lines)):
            body.append(lines[j])
            depth += lines[j].count("{") - lines[j].count("}")
            if j > i and depth <= 0:
                break
        text = "\n".join(body)
        pm = re.search(r'__type\s+=\s+"(\w+)"', text)
        parent = pm.group(1) if pm else "object"
        members, depth, inside = [], 0, False
        for line in body:
            if line.strip().startswith("__members"):
                inside, depth = True, 0
                continue
            if inside:
                depth += line.count("{") - line.count("}")
                mm = re.match(r"^      (\w+)   =   \{", line)
                if mm and depth == 1:
                    members.append(mm.group(1))
                if depth < 0:
                    inside = False
        out[name] = (parent, members)
    return out


def _all_members(schema, type_name):
    """Flatten a schema type's members across its inheritance chain."""
    seen, out = set(), []
    while type_name and type_name != "object" and type_name in schema:
        if type_name in seen:
            break
        seen.add(type_name)
        parent, members = schema[type_name]
        out = members + out
        type_name = parent
    return out


def _model_classes():
    """Names of model XML classes shipped in initial.pak, without .xml."""
    if not os.path.exists(INITIAL_PAK):
        return None
    names = set()
    with zipfile.ZipFile(INITIAL_PAK) as z:
        for n in z.namelist():
            if n.endswith(".xml") and "/classes/models/" in n:
                names.add(os.path.basename(n)[:-4])
    return names


def _ui_symbols():
    """Symbol names present in the Scaleform gfx bundle."""
    if not os.path.exists(GFX_PAK):
        return None
    with zipfile.ZipFile(GFX_PAK) as z:
        blob = z.read("[gfx]/gfxbundle.gfxbundle")
    names = set()
    for m in re.finditer(rb"[ -~]{4,}", blob):
        for w in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", m.group(0).decode()):
            names.add(w)
    return names


def _loc_keys():
    """Localization identifiers available in English.

    strings_english.str is UTF-16LE with a BOM, not ASCII.  Reading it as bytes
    and matching ASCII finds nothing and reports every real key as missing.
    """
    if not os.path.exists(INITIAL_PAK):
        return None
    with zipfile.ZipFile(INITIAL_PAK) as z:
        blob = z.read("[strings]/strings_english.str")
    text = blob.decode("utf-16", errors="replace")
    return set(re.findall(r"[A-Z][A-Z0-9_]{3,}", text))


class Refs(object):
    """Lazily loaded reference data so a missing install degrades to fewer checks."""

    def __init__(self):
        self._cache = {}

    def __getattr__(self, name):
        if name not in self._cache:
            self._cache[name] = {
                "schema": _schema_blocks,
                "models": _model_classes,
                "symbols": _ui_symbols,
                "loc": _loc_keys,
            }[name]()
        return self._cache[name]


REFS = Refs()


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def read_pak_desc(path):
    """Return the BaseUpgradeDesc text inside a .pak, or None."""
    if os.path.getsize(path) == 0:
        return None
    with zipfile.ZipFile(path) as z:
        if DESC_MEMBER not in z.namelist():
            return None
        return z.read(DESC_MEMBER).decode("utf-8-sig")


def read_platform_zip_desc(path):
    """Platform .zip wraps a .pak which wraps the descriptor."""
    with zipfile.ZipFile(path) as z:
        inner = [n for n in z.namelist()
                 if n.endswith(".pak") and z.getinfo(n).file_size > 0]
        if not inner:
            return None
        with zipfile.ZipFile(io.BytesIO(z.read(inner[0]))) as iz:
            if DESC_MEMBER not in iz.namelist():
                return None
            return iz.read(DESC_MEMBER).decode("utf-8-sig")


def find_mods(root):
    """Yield (identifier, {'dir','source','paks','zips'}) for each mod under root."""
    root = os.path.abspath(root)
    if os.path.isfile(root):
        yield os.path.basename(root), {"dir": os.path.dirname(root),
                                       "source": None, "paks": [root], "zips": []}
        return
    seen = set()
    for cur, dirs, files in os.walk(root):
        base = os.path.basename(cur)
        if not base.startswith(DESC_MEMBER + "_"):
            continue
        if DESC_MEMBER not in files:
            continue
        ident = base[len(DESC_MEMBER) + 1:]
        parent = os.path.dirname(cur)
        seen.add(ident)
        yield ident, {
            "dir": cur,
            "source": os.path.join(cur, DESC_MEMBER),
            "paks": sorted(os.path.join(parent, f) for f in os.listdir(parent)
                           if f.endswith(".pak") and ident in f),
            "zips": sorted(os.path.join(cur, f) for f in files if f.endswith(".zip")),
        }

    # A module installed as a loose pak has no source folder, so the walk above
    # skipped it IN SILENCE.  Run over Media\Mods on 2026-10-11 that reported on
    # one module and said nothing about the other three, which reads as a clean
    # set rather than as three modules never examined.  One of them was holding
    # an ERROR at the time.
    for cur, dirs, files in os.walk(root):
        loose = {}
        for f in files:
            if not (f.startswith(DESC_MEMBER + "_") and f.endswith(".pak")):
                continue
            ident = f[len(DESC_MEMBER) + 1:-len(".pak")]
            part = platform_part(f)
            if part:
                ident = ident[:-(len(part) + 1)]
            if ident and ident not in seen:
                loose.setdefault(ident, []).append(os.path.join(cur, f))
        for ident, paks in sorted(loose.items()):
            seen.add(ident)
            yield ident, {"dir": cur, "source": None,
                          "paks": sorted(paks), "zips": []}


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _pak_member_names(pak_path):
    """Every member path inside a .pak, forward-slashed. Empty on any failure."""
    try:
        with zipfile.ZipFile(pak_path) as z:
            return {n.replace("\\", "/") for n in z.namelist()}
    except Exception:
        return set()


def _local_names(mod_dir, subpath, ext, members=None):
    """Basenames of files the mod ships itself, e.g. ui/textures/*.png.

    ⛔ A PACKED MOD SHIPS ITS FILES INSIDE THE PAK, NOT BESIDE IT.

    This used to consult only a filesystem directory. Checking a .pak then set
    mod_dir to the folder CONTAINING the pak -- the Mods folder, which has no
    classes/models subdirectory -- so this returned an empty set meaning "this
    mod ships nothing", and every self-shipped model class and UI texture was
    reported as missing.

    BaseUpgradeDesc_PebbleGas.pak was reported with FOUR ERRORS while plainly
    containing all four referenced files. The three modules this tool is
    calibrated against were only ever checked as unpacked folders, so the pak
    path was never exercised.

    A validator that reports a correct artefact as broken is worse than no
    validator: it sends you to fix something that was never wrong.
    """
    found = set()
    if mod_dir:
        d = os.path.join(mod_dir, subpath)
        if os.path.isdir(d):
            found |= {f[:-len(ext)] for f in os.listdir(d)
                      if f.lower().endswith(ext)}
    if members:
        want = subpath.replace("\\", "/").strip("/") + "/"
        for m in members:
            if m.lower().startswith(want.lower()) and m.lower().endswith(ext):
                found.add(os.path.basename(m)[:-len(ext)])
    return found


def check_descriptor(raw, origin, ident, mod_dir=None, members=None):
    """Validate one descriptor blob.  Returns a list of (level, rule, message)."""
    out = []

    def add(level, rule, msg):
        out.append((level, rule, "%s: %s" % (origin, msg)))

    try:
        doc = json.loads(raw)
    except ValueError as exc:
        add("ERROR", "json", "not valid JSON (%s)" % exc)
        return out

    ssl_type = doc.get("SslType")
    value = doc.get("SslValue") or {}
    name = doc.get("name", "")

    # -- class -------------------------------------------------------------
    if ssl_type not in VALID_SSL_TYPES:
        add("ERROR", "sslType",
            "SslType %r is not a class the editor offers; only %s exist. "
            "An unknown class cannot be deserialized."
            % (ssl_type, " / ".join(sorted(VALID_SSL_TYPES))))

    # -- identifier --------------------------------------------------------
    if not name:
        add("ERROR", "name.missing", "top-level 'name' (the module identifier) is empty")
    elif not re.fullmatch(r"[A-Za-z0-9_]+", name):
        # The docs forbid spaces here, but three modules that work in game carry
        # them ("Cliffs Sky Crane", "PMOR Service Center", "Doc Windsors Outback
        # EMT Service"), so this is tolerated in practice and is never the cause
        # of a module failing to appear.  Noted, not blamed.
        add("STANDARD", "name.charset",
            "identifier %r contains spaces or punctuation; the docs ask for Latin "
            "letters, digits and underscore only. Measured: 'Nom Noms Drone "
            "Delivery Service' loads and works, so spaces do not break a module."
            % name)

    zp = value.get("zoneProperty")
    prop_type = (zp or {}).get("$type")
    module_id = (zp or {}).get("moduleId")

    # -- zoneProperty / moduleId pairing -----------------------------------
    if ssl_type == "AntennaUpgradeDesc":
        if zp is not None:
            # Was an ERROR until 2026-10-11, and that grading was backwards.
            # The docs-literal null is written for a VANILLA antenna, whose
            # ANTENNA identity is compiled in; a mod has no baked identity, so
            # null leaves it with no zone prop and nothing installs.
            # MEASURED: the null variant appeared in ZERO of 5 CompleteSave.dat
            # files.  With $type ZonePropertyFobModuleDefault + moduleId ANTENNA
            # the module IS in modModulesUsed (save of 2026-10-11 13:15) and the
            # owner confirmed it purchasable and deployable in game.
            add("STANDARD", "antenna.zoneProperty",
                "AntennaUpgradeDesc carries a zoneProperty ($type %r); the docs "
                "say 'for this type of the FOB module the zoneProperty field is "
                "set to null and not specified'. Measured: null registered in 0 "
                "of 5 saves, while ZonePropertyFobModuleDefault + moduleId "
                "ANTENNA does register and deploy, so the documented null is "
                "for a vanilla antenna and is not required of a mod."
                % prop_type)
        if "radiusDescoverInventoryStorageZone" not in value:
            add("ERROR", "antenna.radius",
                "AntennaUpgradeDesc requires radiusDescoverInventoryStorageZone "
                "(note the engine's spelling 'Descover', which differs from the docs)")
    elif zp is None:
        add("ERROR", "zoneProperty.null",
            "zoneProperty is null, which is only legal for AntennaUpgradeDesc. "
            "A BaseUpgradeDesc gets all of its function from this field.")
    else:
        if prop_type not in VALID_ZONE_PROPS:
            add("ERROR", "zoneProperty.type",
                "$type %r is not a FOB module zone prop. Valid: %s. The packer "
                "silently writes zoneProperty:null when it cannot resolve the type."
                % (prop_type, ", ".join(sorted(VALID_ZONE_PROPS))))

        schema = REFS.schema
        if schema and prop_type in schema:
            declares_module_id = "moduleId" in _all_members(schema, prop_type)
        else:
            declares_module_id = prop_type in {
                "ZonePropertyFobModuleDefault", "ZonePropertyFobModuleStorage",
                "ZonePropertyGenerator", "ZonePropertyHelicopter",
                "ZonePropertyServiceStation", "ZonePropertySupplyShop",
                "ZonePropertyTowTruck", "ZonePropertyWorkshop",
            }

        if declares_module_id and not module_id:
            add("ERROR", "moduleId.missing",
                "%s carries a moduleId and it is absent. Nothing else in "
                "BaseUpgradeDesc identifies the module, so the game cannot bind it."
                % prop_type)
        if module_id and not declares_module_id:
            add("WARNING", "moduleId.unsupported",
                "moduleId %r is set but %s has no such field in the schema; it will "
                "be dropped on load." % (module_id, prop_type))

        if module_id and declares_module_id and module_id not in VANILLA_MODULES:
            # The table is Saber's closed list of original module identifiers, and
            # BaseUpgradeDesc carries no other identity field. A value outside it
            # passes the schema (moduleId is a plain string) and passes every
            # structural check, so nothing catches it -- but PebbleGas shipped
            # "PebbleGas" here and sold without a refund and without its model
            # being removed, while the zone itself released normally. One
            # unresolvable identity, three symptoms.
            # ⚠ NOT proven to be the cause. It is a WARNING because the schema
            # permits a free string and no Saber text forbids one.
            add("WARNING", "moduleId.offtable",
                "moduleId %r is not in Saber's table of original module "
                "identifiers (%s). The schema allows any string, but the game "
                "has nothing else to identify the module by. All three known "
                "working module mods use a table value."
                % (module_id, ", ".join(sorted(VANILLA_MODULES))))

        if module_id in VANILLA_MODULES:
            want = VANILLA_MODULES[module_id]
            if want is None:
                add("ERROR", "moduleId.pairing",
                    "moduleId %r is configured without a zone -- it requires "
                    "AntennaUpgradeDesc with zoneProperty null, not %s."
                    % (module_id, prop_type))
            elif prop_type != want:
                add("ERROR", "moduleId.pairing",
                    "moduleId %r must use %s, not %s (official table of original "
                    "in-game FOB modules)." % (module_id, want, prop_type))
        elif module_id and module_id != name:
            add("WARNING", "moduleId.identifier",
                "moduleId %r is neither an original module id nor equal to this "
                "mod's name %r. The docs require it to match the name field when "
                "the module is not re-skinning a vanilla slot."
                % (module_id, name))

        if prop_type in INTERACTIVE_BY_PROP:
            want = INTERACTIVE_BY_PROP[prop_type]
            got = value.get("isInteractive")
            if got is not None and bool(got) != want:
                add("ERROR", "isInteractive",
                    "isInteractive is %s but %s requires %s; a mismatch leaves the "
                    "module unusable or its dialog unreachable."
                    % (got, prop_type, want))

        if prop_type == "ZonePropertyFobModuleStorage":
            if zp.get("isAtDeployZone") is not True:
                add("ERROR", "storage.isAtDeployZone",
                    "isAtDeployZone must be enabled for every FOB module using "
                    "ZonePropertyFobModuleStorage")

        # Every one of these inherits ZonePropertyInventoryStorage, whose
        # InitZoneInventory is where the base-building popup dies. Measured
        # 2026-10-06: a GENERATOR module with alwaysSpawn disabled crashed the
        # popup, which made returning to headquarters impossible -- no error
        # message, the game simply refused. Every stock module of every one of
        # these types has it enabled, as did both of the user's working modules;
        # the crashing one was the only False among them.
        # 🚨 DOWNGRADED ERROR -> WARNING 2026-10-16, on two counter-examples.
        #
        # 1. PebbleGas ships `alwaysSpawn: False` on ZonePropertyFobModuleStorage
        #    and the owner has used it in game. This rule was reporting ERROR on
        #    a module that WORKS.
        # 2. The owner's Generator was reinstalled with alwaysSpawn flipped to
        #    True and NOTHING else changed -- a one-field, schema-clean diff --
        #    and the base store was still empty. So it is not the generator's
        #    fault either.
        #
        # The 2026-10-06 crash it was derived from was real, but "alwaysSpawn
        # False breaks inventory-backed modules" does not survive either test.
        # Keeping it as a WARNING because every STOCK module of these types does
        # enable it, so False remains worth flagging -- but it is ⛔ NOT a cause
        # and ⛔ must not block a pack.
        if prop_type in INVENTORY_BACKED and zp.get("alwaysSpawn") is not True:
            add("WARNING", "inventory.alwaysSpawn",
                "alwaysSpawn is %r on %s, and every STOCK module of these types "
                "enables it. ⛔ NOT graded an error and ⛔ not a known cause: "
                "PebbleGas ships False on this same property type and works in "
                "game, and flipping the owner's Generator to True as a one-field "
                "schema-clean change did not restore its base store. The "
                "2026-10-06 InitZoneInventory crash this rule came from was real "
                "but is not explained by this field alone."
                % (zp.get("alwaysSpawn"), prop_type))

    # 2026-10-15/16: a modded ZonePropertyGenerator empties the base-building
    # store and makes it unreachable. Established by four installs with a
    # passing control, on the owner's own machine:
    #
    #   no generator pak installed  -> store works        (the control)
    #   my build, moduleId occupied -> store empty, UI gaps, freeze on exit-to-HQ
    #   my build, moduleId FREE     -> same
    #   his UNMODIFIED 2025 archive pak -> same
    #
    # The id collision is therefore NOT the cause, and neither are any of the
    # descriptor fields I changed -- his untouched pak fails identically. The
    # property type itself is what does it.
    #
    # ⛔ IT IS SILENT. Zero SSL dumps across every broken session, no crash
    # screenshot, no Windows event, and THIS CHECKER REPORTED [clean] on the
    # builds that broke the game. That is why the rule has to live here: there
    # was no other way to find out except costing the owner a session each time.
    #
    # 🧭 This is what his 2025 "Not Working" folder label actually meant for the
    # Generator -- not "fails to function" but "breaks the base store".
    if prop_type == "ZonePropertyGenerator":
        add("ERROR", "generator.breaksStore",
            "Every generator module tried so far EMPTIES the base-building store "
            "and makes it unreachable. Measured 2026-10-15/16, four installs with "
            "a passing no-pak control: with no generator pak the store works; with "
            "moduleId GENERATOR occupied it breaks; with that id FREE it still "
            "breaks; and the owner's own UNMODIFIED 2025 pak breaks it the same "
            "way. It fails SILENTLY -- no SSL dump, no crash screenshot, no "
            "Windows event -- and this checker said [clean] on the builds that "
            "broke the game, which is why the rule is here. "
            "*** WHY IS NOT KNOWN. *** Both paks tested share one lineage: the "
            "second was built FROM the first, inheriting 22 unchanged "
            "zoneProperty fields, so these runs do NOT isolate the property type "
            "and a shared bad field would be present in both. UNTESTED and the "
            "way to settle it: a generator descriptor authored from Saber's live "
            "GENERATOR values rather than from that lineage. Until then treat "
            "this as a hard stop, not a verdict on the type. Safe alternatives "
            "with a live working mod: ZonePropertyFobModuleStorage (PebbleGas), "
            "ZonePropertyDroneDelivery (Nom Noms).")

    if ssl_type == "BaseUpgradeDesc" and "radiusDescoverInventoryStorageZone" in value:
        add("WARNING", "radius.wrongClass",
            "radiusDescoverInventoryStorageZone only exists on AntennaUpgradeDesc; "
            "on BaseUpgradeDesc it is ignored")

    # -- consumable slot chances ------------------------------------------
    # Measured over 77 ConsumablesSlot instances in real zone_settings.json:
    # chance is a 0..1 probability (0.1 .. 0.8 appear, and 1 is the most common
    # value at 36 occurrences, meaning "always").  Values above 1 are not
    # canonical and are presumed clamped, so 100 is not a percentage.
    for key, slot in sorted((zp or {}).items()):
        if isinstance(slot, dict) and slot.get("$type") == "ConsumablesSlot":
            chance = slot.get("chance")
            if chance == 0:
                add("WARNING", "slot.chance",
                    "%s.chance is 0, so the slot never spawns" % key)
            elif isinstance(chance, (int, float)) and chance > 1:
                add("STANDARD", "slot.chance",
                    "%s.chance is %s; chance is a 0..1 probability, where 1 means "
                    "always. Values above 1 appear in shipped data and are presumed "
                    "clamped, so this is not a defect, only non-canonical."
                    % (key, chance))

    # -- UI limits ---------------------------------------------------------
    ui_name = value.get("uiName", "")
    if not ui_name:
        add("ERROR", "uiName.empty",
            "uiName is empty; the module has no label in the build list")
    elif len(ui_name) > UI_NAME_MAX:
        # REFUTED as a failure cause 2026-10-10: "Nom Nom's Drone Delivery
        # Service" is 32 characters and works in game.  The documented limit is
        # about layout, not loading.  Do not cite it as a cause again.
        add("STANDARD", "uiName.length",
            "uiName is %d characters (%r); the documented maximum is %d. Measured: "
            "a 32-character uiName works in game, so this is layout risk, not "
            "breakage." % (len(ui_name), ui_name, UI_NAME_MAX))
    if '"' in ui_name:
        add("WARNING", "uiName.quote",
            "uiName contains a double quote (%r); it renders literally" % ui_name)

    ui_desc = value.get("uiDesc", "")
    if len(ui_desc) > UI_DESC_MAX:
        # Measured: PMOR Service Center ships a 311-character uiDesc and works,
        # so this limit is not enforced by the engine.  Cosmetic only.
        add("STANDARD", "uiDesc.length",
            "uiDesc is %d characters; the documented maximum is %d. Measured: a "
            "311-character uiDesc works in game, so this is layout risk, not "
            "breakage." % (len(ui_desc), UI_DESC_MAX))

    # -- models ------------------------------------------------------------
    # A mod may ship its own model classes and its own UI art, so resolve
    # against the mod folder before calling a reference unknown.  Gimbo's
    # bg = "gimbo_bg" resolves to the pak member ui/textures/gimbo_bg.png.
    local_classes = _local_names(mod_dir, "classes/models", ".xml", members)
    local_art = _local_names(mod_dir, "ui/textures", ".png", members)

    models = REFS.models
    for field in ("modelId", "modelIdAdditional", "modelAnimated",
                  "modelAdditionalAnimated"):
        ref = value.get(field)
        if not ref:
            if field in ("modelId", "modelIdAdditional"):
                add("ERROR", "model.missing", "%s is empty; it is required" % field)
            continue
        if models is not None and ref not in models and ref not in local_classes:
            add("ERROR", "model.unknown",
                "%s references model class %r, which is neither in initial.pak nor "
                "shipped in this mod's classes/models folder" % (field, ref))

    # -- UI symbols --------------------------------------------------------
    symbols = REFS.symbols
    if symbols is not None:
        for field in ("bg", "bgSmall", "bgRight", "icon20", "icon40", "iconMap40",
                      "iconTrade", "iconModuleBg", "tradeArrowsSpecialIcon",
                      "popupModuleBg"):
            ref = value.get(field)
            if not ref:
                continue
            if ref in symbols:
                continue
            if ref in local_art:
                # Shipped with the mod.  Only the three picture fields may be
                # custom; the docs say custom icons are not supported for FOB
                # modules, so a local PNG in an icon field will not render.
                if field == "popupModuleBg":
                    # The game preloads mod PNGs only for MODULE_BG, MODULE_BG_SMALL
                    # and MODULE_BG_RIGHT (ExpModImagesBufferWrapper, initial.pak
                    # SSL, 2026-10-24); a custom popup shows a placeholder.
                    add("ERROR", "symbol.customPopup",
                        "popupModuleBg = %r is this mod's own PNG, but the game "
                        "loads mod PNGs only for bg / bgSmall / bgRight; the popup "
                        "would show a placeholder. Set it to \"\" or a vanilla "
                        "symbol." % ref)
                elif field not in ("bg", "bgSmall", "bgRight"):
                    add("ERROR", "symbol.customIcon",
                        "%s = %r resolves to this mod's ui/textures art, but the "
                        "docs state custom icons are not supported for FOB "
                        "modules. Only bg / bgSmall / bgRight may be custom."
                        % (field, ref))
                continue
            add("ERROR", "symbol.unknown",
                "%s references UI symbol %r, which is neither in the game's gfx "
                "bundle nor shipped as ui/textures/%s.png in this mod. Custom "
                "icons are not supported for FOB modules; only bg / bgSmall / "
                "bgRight may be custom PNGs." % (field, ref, ref))

    # -- localization ------------------------------------------------------
    loc = REFS.loc
    for field in ("uiName", "uiDesc", "uiDescTradeHeader",
                  "uiTradeDefaultDescText"):
        ref = value.get(field) or ""
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{3,}", ref):
            continue
        if loc is not None and ref not in loc:
            add("WARNING", "loc.unknown",
                "%s is the localization key %r, which is not in "
                "strings_english.str; the raw key will be shown" % (field, ref))
        else:
            # A key that resolves is the failure mode nobody sees: the slot
            # renders Saber's stock copy, so the author's flavour text appears
            # to "not work" while every structural check passes.
            add("WARNING", "loc.vanilla",
                "%s is the stock localization key %r, so this slot shows "
                "Saber's text, not yours. If you wrote flavour text for this "
                "module, put the literal string here instead."
                % (field, ref))

    # -- inert fields ------------------------------------------------------
    for path, why in sorted(INERT_FIELDS.items()):
        head, tail = path.split(".", 1)
        holder = zp if head == "zoneProperty" else value
        if holder and tail in holder and holder[tail]:
            add("WARNING", "inert",
                "%s is set to %r but has no effect -- %s"
                % (path, holder[tail], why))

    return out


def check_mod(ident, info):
    """Validate a whole mod: source, packer output, and shipped paks."""
    findings = []
    source_raw = None

    if info["source"] and os.path.exists(info["source"]):
        # Read as bytes: text mode translates CRLF to LF on Windows, which makes
        # every pak look stale against an identical source.
        source_raw = open(info["source"], "rb").read().decode("utf-8-sig")
        findings += check_descriptor(source_raw, "source", ident, info["dir"])
    else:
        findings.append(("WARNING", "source.missing",
                         "no source descriptor found; only packed output can be checked"))

    if info["dir"]:
        findings += check_output_zips_present(info["dir"])

    live_paks = [p for p in info["paks"] if os.path.getsize(p) > 0]
    if not live_paks:
        findings.append(("ERROR", "pack.missing",
                         "no non-empty .pak; the module was never packed, so the "
                         "game cannot see it. Use File > Pack in the editor."))

    for pak in live_paks:
        raw = read_pak_desc(pak)
        label = "pak(%s)" % os.path.basename(pak)
        if raw is None:
            if platform_part(pak):
                # Texture-only companion. Carrying no descriptor is correct here.
                continue
            findings.append(("ERROR", "pack.empty",
                             "%s contains no %s member" % (label, DESC_MEMBER)))
            continue
        # The pak is the artefact being judged, so its own members are
        # what "does this mod ship it?" must be answered against.
        findings += check_descriptor(raw, label, ident, info["dir"],
                                     _pak_member_names(pak))
        if source_raw is not None and raw != source_raw:
            findings.append(("ERROR", "pack.stale",
                             "%s does not match the source descriptor. A source fix "
                             "is invisible until a repack." % label))
        findings += check_pack_selfbundle(pak, label)

    for zp in info["zips"]:
        raw = read_platform_zip_desc(zp)
        label = "zip(%s)" % os.path.basename(zp)
        if raw is None:
            findings.append(("ERROR", "zip.empty",
                             "%s has no usable inner pak" % label))
            continue
        if source_raw is not None and raw != source_raw:
            findings.append(("WARNING", "zip.stale",
                             "%s does not match the source descriptor; it would "
                             "upload the wrong build to mod.io" % label))

    return findings


def check_pack_selfbundle(pak_path, label):
    """The editor packs the mod FOLDER, and its own outputs live in that folder.

    Measured on PebbleGas, 2026-10-11, in a single pack:

        pak      2,371,584 B  ->  73,027,584 B   (x30.8)
        each zip    12,705,792 B  ->  83,369,984 B   (x6.6)

    The pack writes the pak first (18:44:34) and then builds one distribution
    zip per platform around it (18:44:41 through 18:44:56). Leave those zips in
    the folder and the NEXT pack sweeps them into the pak, which the next set of
    zips then wraps. It compounds every cycle, silently, and nothing fails.

    Six zips were 0.46 GB after one cycle, on a drive with 19.76 GB free.

    ⛔ Delete the platform zips before packing. The pack rebuilds them, so
    nothing is lost -- and the ones already on disk are stale anyway.
    """
    out = []
    members = _pak_member_names(pak_path)
    nested = sorted(m for m in members if m.lower().endswith(".zip"))
    if nested:
        out.append(("ERROR", "pack.selfbundle",
                    "%s contains %d of the editor's own output zip(s) (%s). The "
                    "pack swept its previous outputs back in; this compounds "
                    "~6x per pack. Delete the platform zips from the mod folder "
                    "and pack again." % (label, len(nested), ", ".join(nested))))
    return out


def check_output_zips_present(mod_dir):
    """Platform zips sitting in the mod root will be swept into the next pack."""
    try:
        names = sorted(n for n in os.listdir(mod_dir)
                       if n.lower().endswith(".zip"))
    except OSError:
        return []
    if not names:
        return []
    return [("WARNING", "pack.outputzips",
             "%d platform zip(s) in the mod folder (%s). The next pack will "
             "sweep them INTO the pak. Delete them first; the pack rebuilds "
             "them." % (len(names), ", ".join(names)))]


LEVEL_ORDER = {"ERROR": 0, "WARNING": 1, "STANDARD": 2}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="mod folder, .pak, or a tree to walk")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    mods = list(find_mods(args.path))
    if not mods:
        print("no BaseUpgradeDesc_* mods found under %s" % args.path)
        return 2

    report, worst = {}, 2
    for ident, info in sorted(mods):
        findings = sorted(check_mod(ident, info),
                          key=lambda f: (LEVEL_ORDER[f[0]], f[1]))
        report[ident] = [{"level": l, "rule": r, "message": m} for l, r, m in findings]
        for level, _, _ in findings:
            worst = min(worst, LEVEL_ORDER[level])

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        print()
        return 0 if worst > 0 else 1

    for ident in sorted(report):
        entries = report[ident]
        errors = sum(1 for e in entries if e["level"] == "ERROR")
        verdict = "%d ERROR" % errors if errors else "clean"
        print("\n=== %s  [%s] ===" % (ident, verdict))
        if not entries:
            print("   no findings")
        for e in entries:
            print("   %-8s %-22s %s" % (e["level"], e["rule"], e["message"]))
    print()
    return 0 if worst > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
