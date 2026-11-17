# Engine ghost

Puts a truck's **engine ghost** where its engine is, in *Expeditions: A MudRunner Game*. The ghost
is the see-through engine the game shows when you start the truck and when its engine takes damage.

Not affiliated with or endorsed by Saber Interactive.

- **Why the ghost floats out of the nose, and what the game really reads:** [Placing the engine ghost](https://pr1m4lc0d3.github.io/expeditions-map-guide/65-vehicles/engine-ghost.html), in the modding guide

## What it does

A truck brought in from another game, or built on another truck's class, keeps that truck's
engine-sound position. The game draws the ghost **at the engine sound** (`<Sounds Origin>`), so the
ghost appears wherever the old truck's engine was: out past the grille, above the hood, or off to
one side. `<Steam Origin>` and the engine `<DamageArea>` do not move it. We read this out of the
game's own code after both had failed in game.

This tool reads the truck's own converted mesh, finds its engine bay (from the windshield base to
the radiator, under the hood line), and fits the ghost in it.

| File | What it does |
|---|---|
| `engine_ghost.py` | Reports where the ghost is and what is wrong with it, suggests a place, draws a picture, writes the new place |
| `meshread.py` | Reads a converted Saber mesh: every frame's transform and every vertex in truck space |
| `test_engine_ghost.py` | Checks that need neither the game nor Blender: `python -m pytest test_engine_ghost.py` |

It is an ordinary command-line program. It does not use AI or the internet. It needs Python 3.10+
and `numpy` (`Pillow` for `--preview`). Keep this folder next to `fob-modules`, as it is in this
repository: it uses the shared `saber` package there.

## Use

```bash
# report: where the ghost is now, what is wrong with it, where it should go
python engine_ghost.py --mod "C:/Users/<you>/Documents/My Games/Expeditions/base/Mods/.modio/mods/<id>" --truck my_truck

# the same, with a side and plan picture of the bay, the ghost now (red) and suggested (green)
python engine_ghost.py --mod ".../mods/<id>" --truck my_truck --preview bay.png

# write the suggestion into the mod (close the game first)
python engine_ghost.py --mod ".../mods/<id>" --truck my_truck --apply

# then nudge it after looking in game, or set it exactly
python engine_ghost.py --mod ".../mods/<id>" --truck my_truck --apply --raise 0.04
python engine_ghost.py --mod ".../mods/<id>" --truck my_truck --apply --set 1.12 0.89 0
```

- `--truck` is the truck's class name: `classes/trucks/<name>.xml`. A **truck replacement**
  (`classes/truck_replacements/<name>.xml`, what a lift kit swaps the truck for) carries its own
  ghost, so place that one too.
- `--media <editor Media folder>` works on an unpacked project instead of an installed mod.
- `--steam` moves the radiator steam (`<Steam Origin>`) with the ghost.
- `--scale` and `--ghost env/engine_small|default|big` change the ghost's size and model.
- `--bay X_FIREWALL X_RADIATOR` gives the bay by hand when the measurement is wrong.

## What it can and cannot judge

The **front-to-back** place is measured: on two trucks checked in game, it landed within 2.5 cm of
the place their owner settled on by eye. The **height** is a starting point (the ghost's top
15 cm under the hood line). How low an engine should sit is a look, and on those two trucks the
owner chose about 25 cm and 8 cm under the hood, so check it in game and nudge with `--raise` / `--lower`.

Writing into a mod rewrites its `.pak` and checks it reads back before swapping it in. The game
must be closed.
