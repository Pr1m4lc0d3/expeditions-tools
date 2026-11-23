# Overlay tools

Pictures for your own roads, fences and models in the *Expeditions: A MudRunner Game* map
editor's library, and the fix that lets the editor show them.

Not affiliated with or endorsed by Saber Interactive.

- **How to make your own overlays, step by step:** [Making your own overlays](https://pr1m4lc0d3.github.io/expeditions-map-guide/20-land/making-overlays.html), in the modding guide

| Script | What it does |
|---|---|
| `make_icon.py` | Writes `Media\icons\<kind>_<name>.dds`, the 128 x 128 picture the library shows, from any image. `--square` takes the middle of a road texture |
| `editor_pictures_fix.py` | Changes 41 bytes of `ExpeditionsEditor.exe` so the library loads those pictures for custom pieces (without it, it never does). Keeps a backup; `--undo` puts it back; `--check` only reports |
| `game_paths.py` | Finds the game, shared with map-tools |

```
pip install Pillow pefile
python editor_pictures_fix.py
python make_icon.py overlays my_road my_road__d_a.tga --square
python make_icon.py overlays my_fence_line render.png
```

Close the editor before running the fix. The editor reads library pictures the first time a
list is opened in a session, so restart it to see new ones. A game update replaces the editor:
run the fix again after one.

It is an ordinary command-line program. It does not use AI or the internet.
