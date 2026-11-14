"""layers.py -- the terrain layer names the game actually ships.

A descriptor naming a layer that does not exist does not error. It renders as
something else or as nothing, and the map just looks wrong. `arizona_rock_flat`
was in the layer list here for weeks and is not a real layer: the shipped Arizona
set is `arizona_rocky_plateau`, `arizona_cracked_ground`, `arizona_sharp_gravel`,
`arizona_ground`, `arizona_gravel`, `arizona_sand` and `arizona_mud`.

So names get checked against the paks before they are written.
"""
from __future__ import annotations

import zipfile
from functools import lru_cache
from pathlib import Path

from . import gamepaths  # noqa: E402
GAME = gamepaths.game_dir("expeditions") or Path(".")   # the install, found on any machine


@lru_cache(maxsize=1)
def known() -> frozenset[str]:
    out = set()
    # Custom brushes live loose in the Media folder and are just as real as the
    # shipped ones. Media/classes/<path> overrides or adds to [media]/classes/
    # <path>; the guide puts custom PbrMaterial classes in
    # Media/classes/terrain_layers and their textures in Media/textures/tiles.
    try:
        from . import project
        custom = project.MEDIA / "classes" / "terrain_layers"
        if custom.is_dir():
            out |= {q.stem for q in custom.glob("*.xml")}
    except Exception:
        pass
    for p in sorted(GAME.rglob("*.pak")):
        try:
            z = zipfile.ZipFile(p)
        except Exception:
            continue
        for n in z.namelist():
            if "/terrain_layers/" in n and n.endswith(".xml"):
                out.add(Path(n).stem)
    return frozenset(out)


def check(names, what="terrain layer"):
    """Raise on any name the game does not ship, listing near misses."""
    have = known()
    if not have:
        return                      # game not installed here; do not block
    bad = [n for n in names if n not in have]
    if not bad:
        return
    import difflib
    lines = []
    for n in bad:
        near = difflib.get_close_matches(n, have, n=3, cutoff=0.5)
        lines.append(f"  {n!r} is not a {what}" +
                     (f"; did you mean {', '.join(near)}?" if near else ""))
    raise ValueError(f"{len(bad)} unknown {what} name(s):\n" + "\n".join(lines))
