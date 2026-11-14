# Map tools

Command-line tools for building and fixing *Expeditions: A MudRunner Game* maps: objectives
and story chains, earning a custom truck and its parts, gateways between maps, packing
checks, real-world terrain, roads, rivers and placed models.

Not affiliated with or endorsed by Saber Interactive.

Every tool here was written against a real failure, and most say in their own header what
they fix and how it was tested. The [map-making guide](https://pr1m4lc0d3.github.io/expeditions-map-guide/)
explains the why; this folder is the how.

## Before you run anything

- **Read a tool's header first** (`python <tool>.py --help` on the ones with options, or
  open the file). Most report what they would change and only write with `--apply`. A few
  are libraries other tools import and do nothing on their own.
- **Close the game and the editor** before any tool writes. The game keeps paks open, and
  the editor reads a map's files only when it opens the map.
- **Back up your map** (`Documents\My Games\Expeditions\Media\prebuild\<level>` and
  `Media\levels\<level>`) before the first run of any tool that edits it.

## Setup

1. Install the game and run the editor once.
2. Install [Python](https://www.python.org/) 3.10 or newer.
3. `pip install -r requirements.txt`
4. Keep this folder next to [`fob-modules`](../fob-modules/) and [`map-models`](../map-models/),
   as it is in the repository: the tools share the `saber` package in `fob-modules`.

The tools find the game and your `Media` folder themselves. If they pick the wrong one,
set `SABER_MEDIA` (your Media folder) or `SABER_GAME` (`expeditions` or `snowrunner`).

## Run after every Pack terrain

| Tool | What it does |
|---|---|
| `after_pack.py` | **Run after every pack.** Fixes custom models and road overlays (through `../map-models`), gateway links, and refreshes regions. `--check` to report only |
| `check_pack.py <level>` | Did the pack quietly drop every zone property? Counts them, in one screen |
| `check_objectives.py <level>` | Checks a map's objectives against the official rules before the game does; `--pak` reads the packed map |

## Objectives, stories and unlocks

| Tool | What it does |
|---|---|
| `build_objectives.py <plan.json>` | Writes a whole chain of objectives from one plan file: Free Roam, tasks, contracts, expeditions, map locks, hidden camps, bases, deliveries, truck hauls, rewards. Refuses a chain that could trap the player |
| `vehicle_unlocks.py <plan.json> --mod <id>` | Locks a custom truck and its parts behind those objectives, writes each reward id the way the game stores it, and gives a parked haul truck the map-prefixed Id it needs |
| `objective_plans/` | Two plans that ran in game: a map locked behind another, and a two-map expedition that opens a base |

Guide: [Unlocking trucks and parts](https://pr1m4lc0d3.github.io/expeditions-map-guide/50-missions/vehicle-unlocks.html),
[Building a story](https://pr1m4lc0d3.github.io/expeditions-map-guide/50-missions/building-a-story.html).

## Gateways between maps

| Tool | What it does |
|---|---|
| `fix_gateway_links.py <level.pak>` | Puts the real `mod_level_<name>` link back after a pack, and stops on the three things that crash a crossing silently. `after_pack.py` runs it |
| `build_region.py new / refresh` | Builds a region of linked maps that the game will load, or refreshes one after a pack |

Guide: [Gateways between maps](https://pr1m4lc0d3.github.io/expeditions-map-guide/40-zones/gateways.html).

## New maps and real terrain

| Tool | What it does |
|---|---|
| `new_map_from.py` | Starts a new map from an existing one's support layers, without carrying its models, zones, roads or water |
| `check_new_map.py` | Acceptance check for a map made that way |
| `build_map.py` | A complete map from a real place: terrain, trails, ground cover |
| `fetch_terrain.py` | Real elevation (USGS 3DEP, US only) into a map's height tiles |
| `fetch_trails.py` | Real trails from OpenStreetMap, laid as road overlays |
| `finish_map.py` | Writes the settings a map needs before it will pack |
| `pack_map.py` | Builds a level `.pak` without the editor (advanced) |

## Roads, rivers and terrain edits

| Tool | What it does |
|---|---|
| `make_overlays.py` | Lays a road network as overlay objects, the engine's own roads |
| `sculpt_roads.py`, `smooth_roadway.py` | The editor's strokes and Smooth brush, applied along a whole network |
| `road_profile.py` | Measures the bumps a driver actually feels along a road |
| `clear_roadway.py`, `sync_tiles.py` | Takes trees and rocks off the roads (library functions) |
| `push_heights.py` | Moves edited terrain from the master into the height tiles |
| `paint_river.py`, `shape_river.py` | River speed, foam and mud masks; riffle-and-pool profiles |

## Models and dressing

| Tool | What it does |
|---|---|
| `reseat_models.py`, `move_cluster.py` | Puts placed models back on the ground after terrain moves; moves a group as one |
| `make_sign.py` | Letters a blank road sign and installs it as a model. Needs the Sign Maker blanks from mod.io |
| `list_installed_models.py` | What custom models the editor has, and the name to type |
| `stock_models.py`, `fetch_blendkit.py` | Fill the asset library with free CC0 models from BlendKit |
| `camo_from_source.py` | Turns a camouflage photo into a truck skin |

## Pak repair

| Tool | What it does |
|---|---|
| `patch_member.py` | Rewrites one file inside a `.pak` without rezipping it (library) |
| `pak_fingerprint.py` | A full, diffable description of a `.pak` |
| `fix_pak_names.py` | Repairs file names that Python's zip module rewrote |
| `install_music.py` | Gives a level background music the way the shipped maps do |

## Tests

```text
python test_build_objectives.py
python test_build_region.py
python test_vehicle_unlocks.py
```

They check the tools against setups that worked in game, kept in `fixtures/`. A test whose
input is not on your machine reports `skipped` and names the file.

## Licence

MIT. See [LICENSE](../LICENSE).
