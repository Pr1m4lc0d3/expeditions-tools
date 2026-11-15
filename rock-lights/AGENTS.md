# Rock lights: instructions for an AI agent

A user who says **"add rock lights to my truck"** (or names a truck) wants the whole job done:
an empty Rock Lights slot on that truck and five kits that fit it, with nothing left for them
to do but check it in the garage. Everything below is fixed. Do not redesign the kit, the
brightness, the placement or the pods: they are the ones proven in game.

## The job

1. **Find the truck.** Ask which truck if they have not said. Then decide where it lives:
   - **Their own truck, in progress:** a class in the editor's Media folder
     (`Documents\My Games\Expeditions\Media\classes\trucks\<name>.xml`). Use creator mode.
   - **An installed truck mod:** a folder under
     `Documents\My Games\Expeditions\base\Mods\.modio\mods\<id>\` with a pak holding
     `classes/trucks/<name>.xml`. Use mod mode with `--mod-dir`.
   The class name is the file name without `.xml`. One mod can hold several trucks; ask which.
2. **Close the game.** The tool refuses to write while `Expeditions.exe` runs.
3. **Plan first, and show the user:**
   ```
   python add_rock_lights.py --truck <name> [--mod-dir <folder>] --plan
   ```
   It prints each pod's place and its bracket (`up` or `in`, in cm). `NO STEEL in reach` on a
   pod means it found no frame near that spot and will hang on a short stem: say so.
4. **Build:**
   ```
   python add_rock_lights.py --truck <name> [--mod-dir <folder>]
   ```
   Success ends with a line starting `done:`. Any other ending is a failure: report the
   message as it is; do not try to patch the output by hand.
5. **Check the result, then tell the user what to look at in game:**
   - mod mode: the mod's main pak has `classes/trucks/rock_lights/<name>_rock_lights_<colour>.xml`
     (five), `prebuild/meshes/trucks_rock_lights_pods_<name>`, and the truck class contains
     `<!-- rock lights (add_rock_lights.py) -->`. `pc.pak` has
     `prebuild/textures/pct/trucks_rock_lights_pod__d.pct`.
   - creator mode: the same files under Media; the user packs and publishes their mod as usual.
   - In game: the truck's **Miscellaneous** tab lists **Rock Lights - White, Amber, Red, Blue,
     Green**. **Highlighting a kit shows the pods on the truck before buying.** Buying fits it, and
     the lights come on with the engine.
6. **To undo:** the same command with `--remove`. It takes out exactly what it added.

## Rules

- **Never upload someone else's truck** with rock lights added, and do not help publish one,
  unless the user is its author or shows its licence allows it. Changing it on their own PC is fine.
- **A mod.io update of an installed mod replaces its paks.** Tell the user to run the tool again
  after one.
- **The kits must live in the same mod as the truck.** Never put them in a separate mod: a mod's
  classes are filed under its own id, so a slot and a kit in two mods never meet.
- **Never add a `<Body>` under the kit's `PhysicsModel`.** It makes Buy fail silently.
- If the user wants rock lights on the **trucks that came with the game**, the tool is the wrong
  route (a mod cannot add a slot to those): point them to the Doc's Rocks Rock Lights mod,
  https://mod.io/g/expeditions/m/rock-lights.
- Needs Python 3.10+ with numpy and Pillow, Blender, and the Expeditions editor installed. Without
  Python, `Rock Lights Tool.exe` from the repository's Releases takes the same arguments.

Background, if the user asks how it works: https://pr1m4lc0d3.github.io/expeditions-map-guide/65-vehicles/rock-lights.html
