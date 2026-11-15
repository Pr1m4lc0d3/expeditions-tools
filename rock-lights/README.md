# Rock lights

Adds a rock-light kit to any truck in *Expeditions: A MudRunner Game*: eight 2 in LED pods
lighting the ground round the truck, in five colours, fitted from the garage.

Not affiliated with or endorsed by Saber Interactive.

- **How it works, step by step:** [Rock lights on any truck](https://pr1m4lc0d3.github.io/expeditions-map-guide/65-vehicles/rock-lights.html), in the modding guide
- **Just want rock lights on the trucks that came with the game?** You don't need this tool:
  get the **Doc's Rocks Rock Lights** mod on [mod.io](https://mod.io/g/expeditions/m/rock-lights)
  (PC only, one installer)

| Script | What it does |
|---|---|
| `add_rock_lights.py` | Gives one truck an empty Rock Lights slot and five add-ons that fit it: **Rock Lights - White, Amber, Red, Blue and Green** (650 each, Miscellaneous tab, on whenever the engine runs) |
| `pods.py` | Where the pods go: placed from the truck's wheels, then mounted on the truck's own frame or floor, read from its converted mesh |
| `pods_blender.py` | The pod mesh to FBX, run in Blender by `add_rock_lights.py` |
| `test_rock_lights.py` | Checks that need neither the game nor Blender: `python -m pytest test_rock_lights.py` |

It is an ordinary command-line program. It does not use AI or the internet.

**No Python?** Download **Rock Lights Tool.exe** from this repository's
[Releases](https://github.com/Pr1m4lc0d3/expeditions-tools/releases) and double-click it. It asks
which truck and does the rest, with the same options as below. It still needs Blender and the
Expeditions editor installed. (`build_exe.py` builds it from this folder.)

**Using an AI assistant?** Point it at [AGENTS.md](AGENTS.md) and say "add rock lights to my
truck". The file tells it the whole job, step by step, with nothing left for you to do but check
the truck in the garage.

This folder shares the converter and game-finding code with the other tools, so keep it next
to [`fob-modules`](../fob-modules/), as it is in the repository.

## What you need

| Dependency | What for | Cost |
|---|---|---|
| **Expeditions** and its map editor, installed | The game's own `ResourceConverter` converts the pods; run the editor once first | You own it |
| **[Python](https://www.python.org/) 3.10+** | Running the tool. Then `pip install -r ../fob-modules/requirements.txt` | Free |
| **[Blender](https://www.blender.org/)** 4.x or 5.x | Building the pod mesh (run in the background). The tool finds it, or set `BLENDER` | Free |

Close the game before you run it.

## Use it

**The easy way:** run it with no arguments, or double-click it if `.py` files open with Python.
It asks which truck, shows where every pod goes, and asks before it changes anything.

```
python add_rock_lights.py
```

**Your own truck**, in the editor's Media folder:

```
python add_rock_lights.py --truck my_truck --plan     # where the pods go; changes nothing
python add_rock_lights.py --truck my_truck            # build it
python add_rock_lights.py --truck my_truck --remove   # take it out again
```

It writes the five add-ons to `Media\classes\trucks\rock_lights\`, the pods to
`Media\meshes\trucks\` (converted into `Media\prebuild\`), their texture to
`Media\textures\trucks\`, and one slot into `Media\classes\trucks\my_truck.xml` (kept once as
`my_truck.xml.rocklights_bak`). Open the truck in the editor once before you start, so its
converted mesh exists; the tool reads it to find the frame. Then pack your mod as usual.

**An installed truck mod, on your own PC:**

```
python add_rock_lights.py --truck their_truck --mod-dir "%USERPROFILE%\Documents\My Games\Expeditions\base\Mods\.modio\mods\<id>"
```

It edits that mod's paks in place after one backup of each (`*.rocklights_bak`), and
`--remove` takes out exactly what it added. A mod.io update of that mod replaces its paks:
run the tool again afterwards. The kits go inside the same mod as the truck, because a slot and
a kit in two different mods never meet (each mod's classes are filed under its own id).

## Sharing

Changing a truck on your own PC is yours to do. **Uploading someone else's truck with rock
lights on it needs their permission or their licence.** If the truck is yours, ship the kit
with it like any other add-on.

## What has been checked

- The kit (lights, brightness, pods, mounting) is the one released as the Doc's Rocks Rock
  Lights mod, checked in game on the trucks that came with the game.
- On all 39 of those trucks, `pods.py` reproduces the released placement to the millimetre.
- On installed truck mods, the output has been checked file by file: five add-ons, the slot, the
  pods and their texture, and `--remove` giving back the original paks byte for byte.
- **Checked in game on a truck mod** (November 2026): a tube-frame buggy from mod.io got its kits
  from this tool; the pods showed in the garage and the lights came on.
- It never touches the game's own files (`initial.pak`). Everything it writes goes into your
  Media folder or into the truck mod's own paks.

If a kit is listed and highlights in the garage but **Buy does nothing**, or the pods do not
show while it is highlighted, the add-on's model setup is wrong. These kits carry no `<Body>`
under `PhysicsModel` for exactly that reason.
