# Path B: a custom 3D model — the proven recipe

**Proven in game 2026-10-24** on Doc Windsor EMT (main base + outpost trailer),
after three weeks of failed builds. PebbleGas and Gimbo's Resource Scanner were
built earlier by luck of the old toolchain. Follow every step, in order. Each one
exists because skipping it broke a real build; the trap table says which.

Tools: `fob-modules/`. Meshy files land in your Meshy store (`$MESHY_STORE`, or `meshy_store` in toolkit.json).
Mods live in `Documents\My Games\Expeditions\Media\Mods`.

## The recipe

```
# 1. Generate with Meshy's default HIGH DETAIL model, through the API (30 credits)
python meshy.py models\source\<name>_01.png --name <name>

# 2. Remesh THAT task to 30k triangles (5 credits). The remesh KEEPS the original skin.
python meshy.py --remesh <task_id from tasks.json> --name <name>_rm --polycount 30000

# 3. Check the base. Render any candidate before cutting, and render again after.
python check_model_base.py <meshy store>\<name>_rm.glb
python check_model_base.py <name>_rm.glb --fix <name>_clean.glb [--floor LO HI]
python preview_models.py <folder> --pick <name>_clean

# 4. Import. Every flag matters. --check is MANDATORY: it runs the back-face gate,
#    and step 6 refuses any model without a pass on record (see "ENFORCED" below).
python glb_to_fbx.py <name>_clean.glb --name <brand> --apply --check --collider hull ^
    --length <metres> --sink <metres> --need-preload --two-sided

#    then confirm the shading normals survived (built FBX should match the GLB, ~0-1%)
blender -b -P check_normals.py -- <name>_clean.glb "<Media>\meshes\models\<brand>.fbx"

# 5. (Optional) the outpost model: repeat 1-4 with its own brand, or reuse the main brand.

# 6. Install into the mod folder: copies meshes, classes and TGAs, publishes .pct,
#    and DELETES the .pct_header the converter also writes.
python install_model_to_mod.py "<Mods>\BaseUpgradeDesc_<Mod>" <brand> [<small_brand>]

# 7. Descriptor (BaseUpgradeDesc in the mod folder):
#      modelId                 = <brand>
#      modelIdAdditional       = <small_brand>   (or <brand> again, as Gimbo's does)
#      modelAnimated           = ""              (no build animation, see traps)
#      modelAdditionalAnimated = ""

# 8. Pack: writes the .pak AND the _pc.pak (textures, .pct only)
python pack_fob_module.py "<Mods>\BaseUpgradeDesc_<Mod>" -o "<Mods>"

# 9. Check
python check_fob_module.py "<Mods>\BaseUpgradeDesc_<Mod>"     # must read [clean]
```

**10. Test in the game**, which is the only arbiter. Deploy at a main base and at an
outpost. Look up close and from 30 m, in daylight, and with headlights at night.
Brighten and zoom your screenshot before you judge it (see "reading a screenshot").

### Sizes that are right

| model | `--length` | `--sink` | reference |
|---|---|---|---|
| main-base module | 12.5 | 0.3 | PebbleGas station 12.53 m; vanilla main modules about 10 m wide |
| outpost / small | 5 to 6 | 0.1 | vanilla small modules 4-7 m wide |

Use `--length`, never `--height`, for anything with a canopy, awning, mast or rack.
The accessory eats the height budget and the body comes out small.

`--sink` matches vanilla: every vanilla main-base FOB module extends 0.19-0.63 m
below zero (shared.pak bounding boxes), so uneven ground never shows a gap.

## The traps, by what you will see

| what you see in game | cause | where it is handled |
|---|---|---|
| **Blurry, smeared texture, scrambled lettering, at every distance** | the mod shipped `.pct_header` beside its `.pct`; the game then draws only the smallest mips | `install_model_to_mod.py` deletes them, `pack_fob_module.py` never packs them. **Confirmed in game 2026-10-24.** |
| Black triangles on the ground under the model | Meshy stored the floor slab twice, facing up and down on one plane; coplanar faces z-fight | `check_model_base.py --floor` |
| Model floats | (a) the collider hull and the LODs are decimated with COLLAPSE, which overshoots below the mesh, and grounding read them | `glb_to_fbx.py` grounds on LOD0 and clamps cdt and LODs to it (fixed 2026-10-24) |
| | (b) a near-zero-area sliver hangs below the real base | `check_model_base.py --fix` |
| | (c) the model sits at exactly 0 on uneven terrain | `--sink` |
| See-through walls from inside | Meshy meshes are single-sided and the engine culls back faces | `--two-sided`: writes `TwoSided="true"` on `<Material>`. Undocumented by Saber, used by 1,331 vanilla meshes |
| Model black | shading map channel order. Saber: **R metalness, G roughness, B ambient occlusion** (Saber's own Material page). glTF packs R occlusion, G roughness, B metalness | `glb_to_fbx.py` reorders (`saber.shading_map.from_gltf_orm`). A remesh comes out (0,204,255), the same as PebbleGas |
| Flat, detail-free red | `.pct` missing; only TGAs shipped | step 6 publishes and gates on them |
| Trucks turn ghostly while the model rises out of the ground | the build animation (`modelAnimated` set to an `_objective` brand) | leave both animated fields `""`. `""` is the schema's unset value (non-nullable string) |
| **Game crashes on load in `Game.LoadSave`** (logs empty, screenshot black) | a brand the save still references was removed from the mod. Each level's state file `storage\<id>\sts_<level>.dat` (zlib after a 4-byte header) lists standing models as `<brand>_<Mod name>` | ⛔ **NEVER remove a brand from a mod that has been deployed in any save.** Stop using it in the descriptor instead; keep its class and meshes in the pak. Confirmed in game 2026-10-24 |
| A deployed module's model is missing after a crash or save repair | the level's state file was lost and the game rebuilt it from `baseBuildings`; it re-created modules with empty animated fields and skipped one still pointing at an `_objective` brand (lead, 2026-10-24) | empty animated fields; then reload the map |
| Model half the size it should be | built with `--height` | `--length` |
| Garbled sign, muddy skin that is not in Meshy's viewer | built from Meshy **Smart Topology**, which paints a NEW, lower-quality texture | use the remesh of the High Detail task (steps 1-2) |
| Model renders, but the outposts stay vanilla | only `modelId` was set | set `modelIdAdditional` too |
| **Sloppy surface: flat panels shade as diagonal light/dark triangles, corrugation looks soft** | the importer welded every UV/normal seam and lost Meshy's normals (largest faces leaning >25°: 0% in the GLB, 60% after import) | `glb_to_fbx.py` no longer welds an under-budget LOD0 (fixed 2026-10-24: 1% after the whole chain). Check with the normal-lean measurement below |
| Converter crash `IsValidIdx(index)`, ds_vector_new.h:314 | coincident vertices or loose points reaching the converter. Since 2026-10-24 seen only via the hull collider | `glb_to_fbx.py` builds the hull from welded points only; `--collider box` isolates it |

### Reading a screenshot

Twice a dark or headlit screenshot was misread: a heavily blurred texture was taken
for "metallic", and a mip blur was taken for a UV-layout problem. Crop the model,
brighten it about 2x, and look again before naming a cause.

To tell mip blur from a bad layout offline: render the FBX with its albedo reduced
to 512 and 256 px (`preview_models.py --tex <reduced png>`). If that reproduces the
in-game look, the game is sampling small mips; check for `.pct_header` first.

## ⛔ Refuted on Doc Windsor. Do not re-chase these

- **"The descriptor is broken"** (2026-10-22). The control test swapped only the
  static model while the build-animation model, which is what is displayed, kept
  its own textures.
- **"Green in the shading map is gloss, so it is inverted."** Saber's docs say
  roughness. The black render was the R/B swap above.
- **"The fragmented Meshy atlas is the blur."** It was `.pct_header`. A remesh atlas
  has ~16,000 islands; PebbleGas and Gimbo's have 9,000-11,000 and render fine.
- **Re-baking onto a Smart UV Project layout in Blender.** The remesh is a triangle
  soup; even after welding, the unwrap stays thousands of fragments.
- **Material name** (`material_0` vs `bakedmaterial`). Irrelevant.
- **A Meshy re-generation with Smart Topology** as the fix for the blur. It costs
  the original skin and fixes nothing that the header removal does not.

### Measuring shading normals

The sloppy-surface fault is invisible in a Blender preview (Blender draws Meshy's
normals from the GLB). Measure the built FBX instead: for the largest 10% of faces,
the share of area whose corner normals lean more than 25° off the face. Meshy's GLB
measures 0-1%; a healthy build the same. A weld-damaged build measured 36-60%.

```
<blender.exe> -b -P check_normals.py -- ^
    <meshy store>\<name>_clean.glb "<Media>\meshes\models\<brand>.fbx"
```

The number to beat is "about what the GLB shows".

⛔ Tried and dropped for this fault: a CUSTOM_NORMAL Data Transfer after welding
(Meshy models thin walls on both sides, so it copied the far side's normal and
flipped 39%), and smooth-by-angle on the welded mesh (34-86% still leaning).

🚨 BIG DARK TRIANGLES = FLIPPED FACES, not normals and not texture (2026-10-24).
`clean_mesh` used to call `bmesh.ops.recalc_face_normals`. On an unwelded mesh
(thousands of one-triangle regions) it flipped 48% of plattgate's surface; 4.2%
even welded. Under `TwoSided` a flipped face is drawn from behind and lit from
the wrong side: sharp dark wedges that sit still on the model from every camera
angle, with the texture continuing through them darker. Removed; Meshy's winding
needs 0 flips. Four fixes aimed at normals changed nothing in game first (flat
normal map, `--smoothing OFF`, winding-vs-normal check, `--smooth-angle 40`),
because each left the flipped faces in place.

✅ Confirmed in game 2026-10-24: the big wedges went with the removal. A smaller
one over the "P" remained: THE SAME FLAW (a face seen from behind), from Meshy's
own mesh, where an inner-skin scrap pokes through the outer skin. Don't call that
"a different problem"; the author rightly rejected that. `fix_backfacing()` now
casts rays from each face to 320 above-ground directions, and deletes (if covered
within 5 cm) or flips any face seen mostly from behind. Visible-from-behind
pixels: plattgate 2.40% -> 0.38%, plattskid 0.33% -> 0.01%. A simple
fixed-distance ray rule was tried first and made the skid WORSE (deleting hidden
faces exposes the backs of others). Only faces someone can actually see are touched.

### ⛔ ENFORCED since 2026-10-25: the back-face gate

The check is no longer a step to remember. Three tools refuse:

| tool | refuses |
|---|---|
| `glb_to_fbx.py --check` | runs `check_backfacing.py` (8 views, front faces white, back faces red) on Meshy's GLB and the built FBX. Exit 3 if the FBX shows > GLB + 0.25 points, or > 1.0% at all. Renders and `backface.json` go to `Toolkit\build_reports\<brand>\` |
| `install_model_to_mod.py` | any brand without a PASS recorded for that exact FBX (size and timestamp). No override |
| `check_fob_module.py` | `popupModuleBg` naming the mod's own PNG (`symbol.customPopup`) |

Calibrated against the author's in-game verdicts (2026-10-25), share seen from behind:

| build | GLB | FBX | in game | gate |
|---|---|---|---|---|
| plattgate | 3.45% | 0.07% | clean | pass |
| plattskid | 0.16% | 0.00% | clean | pass |
| doctrailer | 0.05% | 0.05% | clean | pass |
| pebblegas_station | 0.28% | 0.17% | clean | pass |
| pebblegas_tanker | 0.13% | 0.07% | clean | pass |
| docbase (pre-`fix_backfacing` build) | 1.94% | 1.95% | **vertical scramble on the container walls** | **FAIL** |

Also refused: more than 0.25% of Meshy's outline missing in any view (holes from
deleted faces).

`fix_backfacing` was rewritten 2026-10-25 to cast a ray per pixel from 96
outside directions. The per-face version (rays from 4 points on each face)
missed long thin inner-skin triangles that only PARTLY poke through the outer
skin: all four points sat on the buried part. That was Doc's remaining wall
scramble. After the rewrite: docbase 0.04%, plattgate 0.05%, plattskid 0.00%,
no holes. What stays red is the inside of a single-skin wall seen through an
opening (an interior; it reads as dark, like a real one).

Note docbase: the old build was no worse than Meshy's GLB; Meshy's own inner skin
showed through the corrugation. Comparing with the GLB alone would have passed
it. That is why the absolute 1.0% limit exists. Never loosen either number to
let a model through; fix the model.

Look at a model by hand: `python check_backfacing.py <glb> [<fbx>] --out <dir>`.

## Still open

- A sold module leaves its model behind. Permanent, see `known-limits.md`.

## ⭐ Models with lettering: rebake instead of remesh (2026-10-25)

Meshy's remesh broke letters on 3 of 7 signs with stray triangles (Tuck's sign at
30k AND 40k; Meg's cart with nothing on the sign at all) while the full-detail
model read cleanly every time. The fix that reads every letter:

```
python rebake_from_hd.py <brand>.glb <brand>_rb.glb          # our collapse + colour baked from full detail
python glb_to_fbx.py <brand>_rb.glb --name <brand> --high <brand>.glb --apply --check ...
```
`fob_projects/model_pipeline.py rebake|build` does both. The rebake carries colour
only; the converter supplies a flat shading map. Seen in game on Tuck's shop: letters
correct. A part bolted ON a sign still collapses into a face: keep signs clear.
Specialist FOBs built this way: see fob_projects/specialist_fobs.py.

## Concept images: what Meshy can reproduce (learned 2026-10-25, Tuck's Rover Works)

Meshy textures the whole model from ONE picture, then the remesh cuts it to 30k.
- Lettering only on flat, vertical panels facing the camera square-on. Text on a
  tilted surface (a canopy) came back mirrored nonsense; every square-on sign read.
- Nothing small on or in front of a sign: the remesh stretched a 5 cm lamp on a
  sign into a wedge across the letters, at 30k and at 40k alike. Removing faces
  that float off the full-detail surface did NOT catch it (the wedge is anchored
  on both surfaces) and opened roof holes: fix it in the concept, not the mesh.
- Big solid masses; no wire, chain, cable, lattice or loose clutter.
- Transparent background. A worked prompt pair: fob_projects/tucks_rover_works_prompts.md.

## Meshy facts

🚨 **The web app and the API are separate task spaces.** A browser model cannot be
fetched through the API. Generate through `meshy.py`; the task id lands in
`tasks.json` and the model can be rebuilt later.

`target_polycount` is ignored on image-to-3d; the remesh honours it. Never decimate
a Meshy model in Blender: it collapses the UV atlas and the texture scrambles.

## Import facts that still hold

🚨 **The FBX already has a `RootNode`.** Building a second one buries the collider
and NULL-crashes the editor.

🗑 Delete `Media\prebuild\meshes\models_<brand>` before a rebuild, or a stale
conversion reproduces byte-identically.

🧭 R = 0 in a shading map is normal; 291 of 500 shipped maps have it.

## Which gate decides

| gate | what it proves |
|---|---|
| `ResourceConverter` | the file parses. It has passed models that crashed the editor. |
| `check_fob_module.py` | the descriptor and pak structure are sound |
| the **game** | the module works. ⛔ Report nothing as fixed until it has been seen there. |

## Choosing between the two paths

| | recolour | custom model |
|---|---|---|
| time | one command | Meshy, then this recipe |
| result | vanilla shape, your colour | your shape |
| sells and disappears | **no** | **no** |

Both carry the limitation in `known-limits.md`.
