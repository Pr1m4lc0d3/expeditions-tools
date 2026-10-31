---
name: expeditions-fob
description: Use when building, recolouring, or debugging a custom FOB module for Expeditions - a BaseUpgradeDesc mod, a recoloured vanilla module, or a module carrying a custom 3D model made in Meshy or similar. Covers the two ways to make a module visually distinct (recolour a vanilla model, or import your own), the proven step-by-step custom-model recipe, the descriptor and its identity rules, the texture publishing step the runtime requires, and the one permanent limitation every custom FOB has. Also use when a deployed module renders flat red, black, blurry or smeared, see-through, floating, or too small, when black triangles appear under it, when trucks go ghostly during the build animation, when the Base modules panel comes up empty, or when a sold module will not disappear from the map.
---

# Custom FOB modules for Expeditions

Tools: this folder (`fob-modules/`). Full detail in `docs/`.

A FOB module mod is a `BaseUpgradeDesc` descriptor plus, optionally, the models it
points at. There are two ways to make one look different from the stock amber
modules, and they sit side by side:

| | what you get | effort | reference |
|---|---|---|---|
| **A. Recolour** | a vanilla module in another colour, same shape | one command | `docs/recolour.md` |
| **B. Custom model** | your own 3D asset | image to 3D, then import | `docs/custom-model.md` |

Both produce a module the player builds and sees at a base. Both share the
descriptor rules in `docs/descriptor.md`.

## Read this before shipping anything

**A module carrying ANY mod-delivered model does not disappear from the map when
sold.** The sale otherwise completes correctly: the record is removed, the zone
is freed, the slot becomes reusable, the refund arrives. Only the model object
stays behind.

This is true for a fully custom model and equally true for a recoloured vanilla
one. It is not true for a module that points at a stock model brand.

Seven fixes have been tried and refuted, including `DynamicModel="false"`,
vanilla `_objective` animated slots, per-level `pak.load_list` registration, and
`--mod-convert` through the in-game Mod manager. The author closed it as
unrepairable on 2026-10-17.

⛔ Do not open this as a bug to solve. ⛔ Do not propose an eighth fix.
✅ Tell the user plainly, in the mod description, that a sold module leaves its
model behind. See `docs/known-limits.md`.

**A mod antenna module (`AntennaUpgradeDesc`) never performs its ability.** It
deploys, renders and sells, and does nothing. A patch to the game's compiled SSL
was built and tested on 2026-10-25 and changed nothing; the author closed the issue
and Gimbo's became a money module. ⛔ Do not reopen without new evidence. See
`docs/known-limits.md`.

## The three that cost the most time

### 1. An identifier collision empties the Base modules panel

`zoneProperty.moduleId` is the module's identity. Reuse one that another mod
already holds and the **Base modules panel comes up completely empty**, with its
header showing the raw key `UI_BASE_BUILDING_SUBHEADER` instead of text. No
crash, no log line, nothing in `LegacyLog.txt`.

Measured 2026-10-16: three mods had been colliding with *vanilla* identifiers for
weeks with a working panel. It broke only when a second *mod* took
`DOUBLE_STOCK`.

⛔ Never clone a descriptor and keep its `moduleId`. The tool now replaces it
with a unique one derived from the module's name, and `clone_hazards()` reads
the live `Media\Mods` folder and refuses an id already held.

Two shapes are known to work: a unique off-table id (PebbleGas uses its own mod
name), or no `moduleId` at all (Nom Nom's Drone Delivery).

### 2. The runtime reads `.pct`, never `.tga`, and a missing texture is RED

Ship only source TGAs and every affected surface renders **flat, detail-free
red**. It reads like a colour bug and it is a missing file.

⛔ Mod Manager's CONVERT did not publish them. Measured: the mod folder gained
no `prebuild/textures/` and no `RebuildLog` at all, and `LegacyLog.txt` stayed
0 bytes. The build step runs `ResourceConverter` itself and gates on
`saber.convert.textures_published()`.

🚨 **And a mod ships `.pct` ONLY, never `.pct_header`.** With headers beside the
`.pct`, the game draws only the smallest mips: a blurry, smeared texture with
scrambled lettering at every distance. Confirmed in game 2026-10-24; it cost
Doc Windsor three weeks. `install_model_to_mod.py` deletes them and
`pack_fob_module.py` builds the `_pc.pak` without them.

### 3. A module has FOUR models, and the one you see is the small one

```
modelId                  medium model, used at a main base
modelIdAdditional        SMALL model, used at outposts
modelAnimated            medium build animation
modelAdditionalAnimated  small build animation
```

`defaultDeployPoint` picks between medium and small. Recolour or replace only
the medium and every outpost deployment stays vanilla. 12 of the 13 module
families have a small twin; `fob_module_storage` has none. A custom module may
point `modelIdAdditional` at its main brand (Gimbo's does).

For a custom model, leave **both animated fields `""`**. A custom `_objective`
build animation turns nearby trucks ghostly while the model rises, and it was
only ever added to chase the despawn limitation, which it does not fix.

🚨 **Never delete a brand from a mod once the module has been deployed in a save.**
The level's saved state names every standing model by brand, and a missing one
crashes the game in `Game.LoadSave` on load (confirmed 2026-10-24). To retire a
brand, stop pointing the descriptor at it and leave its files in the pak.

## Path A, recolour a vanilla module

```
python recolor_fob.py reach <module>              # which colours each paint map can take
python recolor_fob.py sheet --map <albedo map>    # preview as the game will draw it
python recolor_fob.py build <module> <colour> --out <dir> \
    --descriptor-from <a working .pak> --ui-name "My Module"
```

Colours: `red orange olive green carbon black grey blue purple white`. On amber
paint only red, orange, olive, green, carbon and black can work.

It works because a converted Saber mesh is `[u32 len][CombineXMesh XML][geometry]`
and the vanilla module meshes carry their `<Material>` list as readable XML in
that header. The tool writes a `DiffuseMultiplier` onto each painted material
and ships **no textures**: Saber's own stay in place. Geometry is copied across
byte-identical. No remesh, no FBX.

⛔ **Never ship new textures for an edited vanilla mesh: they draw BLACK in
game** (measured 2026-10-31): the engine logs
`Info| Failed to load texture textures/models/<name>.tga.` for each. A model
built through the converter (Path B) loads its own textures fine.

A tint can only darken a channel, and it hits every pixel of the texture, so a
map whose greys would pick up a colour cast is left amber and named in the
output. See `docs/recolour.md`.

## Path B, a custom 3D model

⛔ **Follow `docs/custom-model.md` step by step.** It is the recipe proven
in game on 2026-10-24, with every symptom mapped to its cause. In short:

```
python meshy.py image.png --name x                         # High Detail, NOT --smart-topology
python meshy.py --remesh <task_id> --name x_rm --polycount 30000
python check_model_base.py x_rm.glb [--fix x_clean.glb --floor LO HI]
python glb_to_fbx.py x_clean.glb --name <brand> --apply --check --collider hull \
    --length 12.5 --sink 0.3 --need-preload --two-sided
python install_model_to_mod.py <mod folder> <brand>         # strips .pct_header
#   descriptor: modelId/modelIdAdditional = <brand>; both animated fields ""
python pack_fob_module.py <mod folder> -o <Mods>            # .pak + _pc.pak
python check_fob_module.py <mod folder>
```

## Verifying

```
python -m pytest                           # the tool's own tests
python check_fob_module.py <Mods dir>      # descriptor and pak structure
```

⛔ A build that passes every check above is still unproven in game. The arbiter
for a shipped module is the game, not the editor and not the checker.
