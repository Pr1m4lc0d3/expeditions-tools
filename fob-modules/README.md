# FOB module tools

Build custom base modules (FOB modules) for *Expeditions: A MudRunner Game*:
the buildings you place at a main base or an outpost, with their own name,
price, stats, store art and look.

Six modules built with these tools are live on mod.io: Platt's Gate Works,
Treadwell Craftworks, Chuck-It, The Long Pull, Ohm Sweet Ohm and The Crow's
Nest.

Not affiliated with or endorsed by Saber Interactive.

## Three ways to build one

| | You get | You need |
|---|---|---|
| **A vanilla model** | Your module, with its own stats and price, using a stock model | Python |
| **A recoloured vanilla model** | A stock model in another colour, side by side with the stock one | Python |
| **Your own 3D model** | A new building, from an image, through Meshy and Blender | Python, Blender, a paid Meshy account; optionally the Codex CLI for the concept image |

Every route shares one descriptor (`BaseUpgradeDesc`) and one packing step.

## Before you install: outside services you set up yourself

The first two routes need nothing but Python and the game. **The third route
depends on programs and paid services that are not part of this repository.**
These tools do not include them, do not install them, and cannot set them up
for you. Set each one up on your own, under your own account, before you start.

| Dependency | Used for | What you have to do | Cost |
|---|---|---|---|
| **[Meshy](https://www.meshy.ai/)** (a 3D model generator, a third-party web service) | Turning your concept image into a 3D model (`meshy.py`) | Create a Meshy account, get API access, and save your API key where the tools look for it (see Requirements) | **Paid.** Billed by Meshy in credits. One model as these tools build it costs 35 credits: 30 to generate, 5 to remesh. Every retry costs again. Check Meshy's own pricing for what API access and credits cost today |
| **[Codex CLI](https://github.com/openai/codex)** (OpenAI's command-line agent, used here as an image generator) | Making the concept image (`model_pipeline.py gen` and `back`) | Install Node.js, install the Codex CLI with npm, and sign in with an OpenAI account that has image generation | Counts against your OpenAI plan or usage. **Optional:** skip it by bringing your own image (see Quick start) |
| **[Blender](https://www.blender.org/)** | Cleaning, re-baking, checking and converting the model | Install it. The tools find it, or set `BLENDER` | Free |

Read these before you spend anything:

- **Your images leave your computer.** `meshy.py` uploads your concept image to
  Meshy's servers; the Codex step sends your prompt to OpenAI. Their terms,
  privacy policies and rules on who owns the output apply, not this
  repository's licence. Read them first, especially if you plan to sell or
  publish what you make.
- **Credits are spent the moment a command runs.** A failed or ugly result is
  still billed. Nothing here asks for confirmation before calling Meshy.
- **This project is not affiliated with Meshy or OpenAI** and has no say over
  their prices, limits or availability. If either service changes its API,
  plans or model, the matching step here can stop working until it is updated.
- **No keys ship with these tools.** You supply your own, and they stay outside
  this folder.

## Requirements

- Windows, with Expeditions installed. The tools use the game's own
  `ResourceConverter` and read Saber's packed models.
- Python 3.10 or newer: `pip install -r requirements.txt`
- For your own model: Blender (any recent version) and a paid Meshy account
  with API access (see above). The Meshy key is read from `%LOCALAPPDATA%\ExpeditionsToolkit\meshy_api_key` or
  `MESHY_API_KEY`, never from this folder.

The tools find the game, its Media folder and Blender on their own. If they
guess wrong, set `SABER_GAME_DIR`, `SABER_MEDIA` or `BLENDER`, or put the same
keys (`game_dir`, `media`, `blender`, `meshy_store`) in a `toolkit.json` beside
these files. `python -c "from saber import gamepaths; print(gamepaths.describe())"`
shows what was found.

## Quick start

**A module on a vanilla model.** Generate a descriptor for any of the 14 module
types from Saber's own schema (`--list` shows them and which are proven in
game), then pack it:

```
python make_fob_module.py --list
python make_fob_module.py WORKSHOP "My Module" --uiname "My Module" --cost 7500 --out mymod
python pack_fob_module.py "mymod\BaseUpgradeDesc_My Module" -o "%USERPROFILE%\Documents\My Games\Expeditions\Media\Mods"
python check_fob_module.py "%USERPROFILE%\Documents\My Games\Expeditions\Media\Mods"
```

**A recoloured vanilla model.**

```
python recolor_fob.py reach fob_module_workshop
python recolor_fob.py build fob_module_workshop olive --out "BaseUpgradeDesc_Olive Workshop" ^
    --descriptor-from "<any working module .pak>" --ui-name "Olive Workshop"
python pack_fob_module.py "BaseUpgradeDesc_Olive Workshop" -o "<Mods folder>"
```

A recolour is a tint on Saber's own textures. It can only darken, so red,
orange, olive, green, carbon and black work on amber paint; blue, purple and
white do not. Read [docs/recolour.md](docs/recolour.md) first.

**Your own model.** Follow [docs/custom-model.md](docs/custom-model.md) step
by step; every step exists because skipping it broke a real build.
`fob_projects/model_pipeline.py` runs the steps in order, the same way every
time. Its `gen` and `back` steps make the concept image through the Codex CLI;
if you have your own image, put it in `models/source/<brand>_01.png` and start
at `meshy`.

## Read before you publish

- **A sold module leaves its model on the map** until you leave and come back.
  The sale itself works: the slot frees and the refund arrives. Every module
  that ships its own model or a recoloured one does this; stock-model modules
  do not. Say so in your mod's description. See [docs/known-limits.md](docs/known-limits.md).
- **Every player on a map needs the mod** for multiplayer.
- **Never remove a model from a mod after it has been built in a save.** The
  save names it, and the game crashes on load if it is gone.
- **The game is the only real test.** A build that passes every check here can
  still look wrong in game. Build it, look at it, then publish.

## Using it with an AI agent

`SKILL.md` is an installable skill for Claude Code or Codex. It carries the
same rules as the docs, in the order an agent needs them.

## Tests

```
python -m pytest
```

## Licence

MIT. See [../LICENSE](../LICENSE).
