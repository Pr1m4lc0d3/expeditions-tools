# Map model tools

Put your own 3D models on an *Expeditions: A MudRunner Game* map: rocks,
buildings, ruins, signs, anything you can get into a `.glb` file.

Not affiliated with or endorsed by Saber Interactive.

| Script | What it does |
|---|---|
| `import_model.py` | One command from a `.glb` to a model you can place in the editor: checks the name and the file, converts it, installs it into your Media folder, and runs the game's own converter on it |
| `after_pack.py` | **Run after every Pack terrain.** Without it, a map with your own models on it drops back to the main menu when you load it |
| `fix_map_model_brands.py` | What `after_pack.py` runs, for one map at a time |
| `pak_splice.py` | Replaces one file inside a pak without rebuilding it (a rebuilt pak is one the game will not list) |

These tools share the converter and the game-finding code with the FOB module
tools, so keep this folder next to [`fob-modules`](../fob-modules/), as it is in
the repository.

## Before you install: what you set up yourself

**Importing a model you already have needs only Python, Blender and the game.**
Making a model from a picture needs outside services that are not part of this
repository. These tools do not include them, do not install them, and cannot set
them up for you.

| Dependency | Needed for | What you have to do | Cost |
|---|---|---|---|
| **Expeditions**, installed | Everything: the tools use the game's own `ResourceConverter` | Install the game and run the editor once | You own it |
| **[Python](https://www.python.org/) 3.10+** | Everything | Install it, then `pip install -r ../fob-modules/requirements.txt` | Free |
| **[Blender](https://www.blender.org/)** | Converting the model (`import_model.py` runs it in the background) | Install it. The tools find it, or set `BLENDER` | Free |
| **[Meshy](https://www.meshy.ai/)** (a 3D model generator, a third-party web service) | Only if you want a model made from a picture: `../fob-modules/meshy.py` | Create a Meshy account, get API access, and save your API key (see [the FOB tools README](../fob-modules/README.md#requirements)) | **Paid.** Billed by Meshy in credits: 30 to generate and 5 to remesh, 35 per model as these tools build it. `meshy.py` starts a new task on every run; some Meshy plans include free retries, but whether those cover API runs is Meshy's call, so check your plan |
| **An image generator** of your choice | Only if you want a concept picture to feed Meshy | Any tool that makes a picture of one object on a plain background. The FOB tools can drive OpenAI's Codex CLI for this; it needs its own install and an OpenAI account | Depends on the tool |

Read these before you spend anything:

- **Your images leave your computer** when you use Meshy or an online image
  generator. Their terms, privacy policies and rules on who owns the output
  apply, not this repository's licence. Read them first, especially if you plan
  to sell or publish what you make.
- **Credits are spent the moment a command runs.** A failed or ugly result is
  still billed, and nothing here asks before calling Meshy.
- **This project is not affiliated with Meshy, OpenAI or any model library.**
  If a service changes its API, plans or prices, the matching step can stop
  working until it is updated.
- **Downloaded models carry their own licences.** A CC BY model needs its credit
  shipped with your map.

## Import a model

```
python import_model.py shed.glb --name my_shed --height 4
```

Then **restart the editor** and search Select Asset for the bare name
(`my_shed`, without the `\Media` it lists with). A new model can show pure white
until the editor has converted it: click **Rebuild terrain** and it comes up textured,
with no second restart.

| Option | Use it when |
|---|---|
| `--height 4` or `--length 12` | Always. Imported models arrive at any scale; give the real size in metres |
| `--two-sided` | You can see through walls from inside. Generated models are often single-sided |
| `--keep-origin` | Kit pieces meant to line up when placed at the same point (a chute and the trestle under it). Leave it off and the model is set on the ground |
| `--collider box` | The automatic collision shape fails or is too lumpy |
| `--dry-run` | Convert and report without installing |

What the `.glb` must be:

- **At most 65,534 vertices and 65,534 faces per mesh, counted in the file.** The
  file splits a vertex at every UV and normal seam, so Blender's count is low: a
  mesh Blender called 41,559 vertices was 77,486 in the file. Reduce at the
  source (Meshy's remesh, or by hand). Decimating a textured model scrambles its
  texture.
- **No object, mesh or material named the same as `--name`.** The game rejects a
  converted model that carries its own name inside, and its error does not say
  why. `import_model.py` stops before converting if it finds one.
- **Textures up to 2048 x 2048.** Larger ones are scaled down.

## After every Pack terrain

```
python after_pack.py --check        see what it would change
python after_pack.py                fix it (close the game first)
```

The game registers your model under one name and looks it up under another, so
a packed map with your models on it bounces to the main menu. This rewrites the
name in the packed map to the one the game registers, keeps a backup beside the
pak (`.before-brands`), and reads the result back. Packing undoes it, so run it
after every pack. It is safe to run when nothing needs fixing.

It also checks that every texture your models use is shipped in the map. If it
says **do not upload this map**, a model will render untextured on everyone
else's machine.

## Known limits

- **Proven on a map loaded from your own Mods folder** (2026-10-28). A map
  installed **from mod.io** may be mounted under a different name, and if it is,
  the fix misses. `--archive <name>` sets the name once it is known. Test a
  mod.io install before you rely on it.
- **The converter is a syntax check, not an acceptance test.** A model can pass
  every check here and still look wrong. Place it, look at it in the editor and
  in game, then publish.
- **Only Expeditions.** The brand fix was measured on Expeditions; SnowRunner is
  not tested.

## Tests

```
python -m pytest
```

## Licence

MIT. See [../LICENSE](../LICENSE).
