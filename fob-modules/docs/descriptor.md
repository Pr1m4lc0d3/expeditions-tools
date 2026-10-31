# The BaseUpgradeDesc, and the identity rules that matter

A FOB module mod is one JSON file named `BaseUpgradeDesc`, shipped inside
`BaseUpgradeDesc_<Name>.pak`. The smallest working mods on disk contain nothing
else: Essentials Storage and Nom Nom's Drone Delivery are a single member each,
because they point at stock models.

## Identity: the field that empties the Base modules panel

`moduleId` is **not** at the top level. It lives at:

```
SslValue.zoneProperty.moduleId
```

⛔ A `.get("moduleId")` on `SslValue` returns `None` for every mod on disk and
reads exactly like an absent field. That mistake was made and reported as fact.
Positive-control any zero result here.

### What collides

Saber publishes a closed table of 14 original identifiers:

```
GAS_STATION  BASE_MODULE  EXTRA_STORAGE  REPAIR_ZONE  DOUBLE_STOCK
SUPPLY_SHOP  TOW_TRUCK    PARKING        WORKSHOP     SERVICE_STATION
DRONE_BASE   HELICOPTER   GENERATOR      ANTENNA
```

Reusing one means sharing identity with the stock module. Measured 2026-10-16:
three mods had been doing exactly that for weeks with a working game.

**The failure came from a different collision: two MODS holding one id.** When a
second mod took `DOUBLE_STOCK`, the Base modules panel came up completely empty
with its header showing the raw key `UI_BASE_BUILDING_SUBHEADER`, no crash and
nothing in `LegacyLog.txt`.

⚠ That is one data point. Treat mod-versus-mod as the known-dangerous case and
mod-versus-vanilla as tolerated-but-unwise, not as proven law.

### Two shapes known to work

| mod | moduleId | zoneProperty |
|---|---|---|
| PebbleGas | `PebbleGas`, its own mod name | `ZonePropertyFobModuleStorage` |
| Nom Nom's Drone Delivery | absent entirely | `ZonePropertyDroneDelivery` |

A unique off-table id, or none. `recolor_fob.py` takes the first: it derives a
unique id from the module's display name and reports the substitution. It also
reads the live `Media\Mods` folder, so an id already held by an installed mod is
refused before anything is written.

⛔ A store role on its own is **not** a hazard. An early version of the guard
refused every `ZonePropertyFobModuleStorage` descriptor, which would have made
the tool unable to build the most common kind of module. PebbleGas is that
shape and it works.

## The four model fields

```
modelId                  medium model, main base
modelIdAdditional        small model, outposts
modelAnimated            medium build animation
modelAdditionalAnimated  small build animation
```

`defaultDeployPoint` chooses medium or small. Fill only `modelId` and every
outpost deployment renders the vanilla model instead of yours.

The animated slots take `_objective` classes. Those carry `<Subset
IsDefault="true" Name="stage_0">` which is **empty**, meaning "not built yet",
plus `AnimSubset` and `AnimTrack`.

⛔ A non-empty `modelAnimated` whose class lacks `AnimSubset` and `AnimTrack`
sets native `animated=true` and **crashes at placement**. Copy a vanilla
`_objective` class rather than authoring one; the animation lives in the mesh.
The unset value is `""`, not `null`: the schema declares the field as a
non-nullable string.

## alwaysSpawn

`zoneProperty.alwaysSpawn: true` makes the module place itself at deploy zones.
A module that places itself is one the player never chooses, and therefore never
sells, which defeats any sell testing. `recolor_fob.py` forces it to `false`.

## Packing

```
python pack_fob_module.py <mod folder>
python check_fob_module.py <Mods dir>
```

Layout, measured against Gimbo's Scanner and PebbleGas, the two working
custom-model mods:

```
Media\Mods\BaseUpgradeDesc_<Name>\          assets only, NO descriptor
    classes\models\<brand>.xml              NeedPreload="true" required
    prebuild\meshes\models_<brand>           the converted mesh
    textures\models\*.tga                    source textures
    prebuild\textures\pct\*.pct              what the RUNTIME reads
Media\Mods\BaseUpgradeDesc_<Name>.pak        descriptor + models
Media\Mods\BaseUpgradeDesc_<Name>_pc.pak     0 bytes, both working mods have one
```

⛔ The descriptor goes in the **pak only**. A descriptor in both the folder and
the pak gives two `BaseUpgradeDesc` objects sharing one id.

🚨 The pak carries each asset under **both** path namespaces, which is easy to
miss and produces classes naming meshes the engine cannot resolve:

```
classes/models/<x>.xml      AND  [media]/classes/models/<x>.xml
prebuild/meshes/models_<x>  AND  [meshes]/models_<x>
```

⛔ No `.tga` goes in the pak. Neither working mod has a single one; source
textures stay in the folder.

🚨 Mod paks are DEFLATE; level paks are STORED. Never rebuild a mod pak with a
generic zip tool.
