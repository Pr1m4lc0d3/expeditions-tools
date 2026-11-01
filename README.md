# Expeditions tools

Tools for modding *Expeditions: A MudRunner Game*, all in one place.

Not affiliated with or endorsed by Saber Interactive.

| Tool | What it does | Where |
|---|---|---|
| **FOB module tools** | Builds custom base modules: a vanilla model with your own stats and price, a recoloured vanilla model, or your own 3D model, with the descriptor, store art and packing. | [fob-modules](fob-modules/) in this repository |
| **Map model tools** | Puts your own 3D models on a map: one-command import from `.glb`, and the fix to run after every Pack terrain so the map loads. | [map-models](map-models/) in this repository |
| **Vehicle importer** | Ports SnowRunner trucks into Expeditions: textures, bumpers, tyres, store artwork and engine sound handled, plus the Expeditions systems a SnowRunner truck lacks (gear limit, supplies cameras, garage tabs) and a guide to fitting each vehicle a rack for its gear. | [expeditions-vehicle-importer](https://github.com/Pr1m4lc0d3/expeditions-vehicle-importer) |
| **Specialist builder** | Builds custom specialists: portraits, abilities, balance and mod.io publishing. | [expeditions-specialist](https://github.com/Pr1m4lc0d3/expeditions-specialist) |
| **Map and modding guide** | A plain-language guide to building maps and mods. | [expeditions-map-guide](https://pr1m4lc0d3.github.io/expeditions-map-guide/) |

**Some tools depend on outside services you set up yourself.** Making a 3D
model from a picture needs a paid [Meshy](https://www.meshy.ai/) account and an
image generator (the FOB tools can drive OpenAI's Codex CLI); importing a model
you already have needs Blender. None of these ships with the
tools. Each tool's README lists what it needs and what it costs; read it before
you install.

The tools are also listed on mod.io:
[Expeditions Modding Tools](https://mod.io/g/expeditions/m/expeditions-modding-tools-specialists-trucks).

## Licence

MIT. See [LICENSE](LICENSE).
