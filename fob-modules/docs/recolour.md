# Path A: recolouring a vanilla module

```
python recolor_fob.py reach <module>                 # which colours each paint map can take
python recolor_fob.py sheet --map <albedo map> ...   # preview: the map as the game will draw it
python recolor_fob.py build <module> <colour> --out <dir> \
    --descriptor-from <a working module .pak> --ui-name "My Module"
python pack_fob_module.py <dir> -o "<Documents>\My Games\Expeditions\Media\Mods"
python check_fob_module.py <dir>
```

Colours: `red orange olive green carbon black grey blue purple white`. On
Saber's amber paint only the first six can work; see "What a tint can reach".

## How it works

A converted Saber mesh is three parts:

```
[u32 xml_len][CombineXMesh XML][geometry]
```

The vanilla FOB module meshes in `shared.pak` carry their material list as
**readable XML** in that header:

```xml
<Material AlbedoMap="models/fob_exp_boxes__d.tga" Name="fob_exp_containers"
          NormalMap="models/fob_exp_boxes__n_d.tga"
          ShadingMap="models/fob_exp_boxes__sh_d.tga" />
```

A material may also carry `DiffuseMultiplier="g(r; g; b; a)"`, which the
engine multiplies into the albedo. Saber uses it themselves: the vanilla
helicopter's yellow and red panels are one grey texture under two multipliers.

So the recolour is: measure the amber paint in each of the module's albedo
maps, work out the multiplier that turns that paint into the target colour,
and write it onto every material that uses the map. **Saber's own textures stay
in place and the mod ships no textures at all**, only the four edited meshes
(medium, small, and their two `_objective` build animations). Geometry is
copied byte-identical; `split_mesh(join_mesh(...))` is checked on every brand.

The module comes out under new brand names (`<module>_<colour>`), so a stock
module and a recoloured one stand side by side and read as different modules.

## Why a tint and not new textures

⛔ **New textures on an edited vanilla mesh draw black.** Measured in game on
2026-10-31: a vanilla mesh whose header names textures shipped in the mod logs
`Info| Failed to load texture textures/models/<name>.tga.` for every one of
them and draws those parts black. That held whatever the colour, the texture
names, the pak size, or whether the normal and shading maps came along. A
custom model built through the converter loaded its own textures in the same
session with no failures. The mechanism is not known.

✅ Saber's own textures under a `DiffuseMultiplier` render correctly. The
Crow's Nest helipad mod went carbon grey and olive this way.

## What a tint can reach

A multiplier works channel by channel and cannot go above 1.0, so it can only
**darken** a channel. Amber paint is high in red, middling in green and low in
blue:

- **Reachable:** red, orange, olive, green, carbon, black.
- **Not reachable:** blue, purple, white, light grey. There is not enough blue
  in amber to build them from.

`build` measures every paint map and refuses a colour it cannot reach (worst
channel more than 12% off) rather than ship a muddy approximation.

### The colour cast

A multiplier hits **every pixel** of the texture, not only the paint. Cancelling
amber takes far more blue than red, so a map that also carries greys, whites or
decals turns those blue under carbon or black. `build` measures that cast on
each map's unpainted pixels. A map that would pick up a visible cast is
**left at Saber's amber** and named in the output, and the rest of the module is
tinted. It refuses outright only when no map can be tinted.

```
python recolor_fob.py reach fob_module_workshop
```

prints one row per paint map: `ok`, `cast` (the paint is reachable but the
map's greys would turn colour) or `--` (out of reach).

If a map you care about is marked `cast`, the Crow's Nest shows the way round
it: point that material at a neutral grey texture Saber already ships (the
helicopter used `heli_tile__d`) and tint that instead. It loses the map's own
printed detail and gains a clean colour.

## Rules carried over from the modules that work

- **Both animated descriptor fields stay empty** (`modelAnimated`,
  `modelAdditionalAnimated`). Pointing them at the copied `_objective` brands
  gave a ghost model that turned nearby trucks ghostly. The `_objective` brands
  still ship, because a save that already names them must find them.
- **The card art is the vanilla module's**, read from the game, never the
  descriptor you cloned: a custom module's own art is not in this mod.
- **A unique `moduleId`** is derived from the name, so nothing collides.
- **Never delete a brand from a mod once it has been deployed in a save.**

See `known-limits.md` for the one limitation every mod-delivered module has.
