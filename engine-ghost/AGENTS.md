# Engine ghost: instructions for an AI assistant

The user wants a truck's engine ghost (the see-through engine shown at start-up and on engine
damage) to sit in the truck's engine bay. Do the whole job; the user's only task is to look in game.

## Facts you must not re-derive

- The ghost is drawn at the truck's **engine sound**: `<Sounds Origin="(x; y; z)">` in the truck
  class, attached to `<Sounds ParentFrame>` if it has one. Read in Expeditions.exe: the drawer takes
  the world transform of the truck's engine-sound object. `<Steam Origin>` and the engine
  `<DamageArea>` do NOT move it; both were tried in game and failed. Do not change them to move the ghost.
- Coordinates are truck space: x forward, y up, z sideways, metres.
- The ghost model is `EngineIconMesh` (generic: `env/engine_small`, `env/engine_default`,
  `env/engine_big`), sized by `EngineIconScale`. A truck-specific ghost (`env/engine_<truck>`) is
  modelled in its own truck's space and will not fit another body: switch to a generic one.
- A truck replacement (`classes/truck_replacements/<name>.xml`, e.g. a lift kit's) has its own
  `EngineIconMesh` and `<Sounds>`. If the user's truck has one, place both.
- The game must be closed before writing to a mod's `.pak`.

## Steps

1. Find the mod folder (`Documents/My Games/Expeditions/base/Mods/.modio/mods/<id>`) and the truck
   class name (the `classes/trucks/<name>.xml` inside its `.pak`).
2. Report and picture it:
   `python engine_ghost.py --mod "<mod folder>" --truck <name> --preview bay.png`
   Look at the picture. The amber lines are the bay; the green box must sit between them, under the
   blue hood line. If the bay is wrong (an odd body, no windshield glass), give it with
   `--bay X_FIREWALL X_RADIATOR` read off the picture.
3. Write it: add `--apply` (close the game first).
4. Ask the user to start the truck in game and say whether the ghost should go up, down, forward
   or back. Apply their words as `--raise/--lower/--forward` in metres (a "few centimetres" is 0.04),
   always together with `--set` at the current place, so nudges do not stack on a new suggestion:
   `python engine_ghost.py --mod ... --truck <name> --apply --set X Y Z --raise 0.04`
5. If the truck's builder writes the class XML from a recipe or script, put the final numbers there
   too, or the next build puts the ghost back.

## Done means

The user has seen the ghost in game and is happy with it, and the final `<Sounds Origin>` is in
the mod and in whatever builds it.
