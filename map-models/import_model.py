#!/usr/bin/env python3
"""Turn a .glb model into one you can place on a map in the Expeditions editor.

    python import_model.py rock.glb --name my_rock --height 3.5
    python import_model.py shed.glb --name my_shed --height 4 --two-sided
    python import_model.py bent.glb --name my_bent --keep-origin --collider box

One command runs the whole import: it checks the name and the file, converts with
glb_to_fbx.py (from ../fob-modules), installs the four files into your Media
folder, runs the game's own ResourceConverter on them, and cleans up after it.
Then restart the editor and search Select Asset for the bare name.

The GLB can come from anywhere: Blender, a model library, or an image-to-3D
service such as Meshy (python ../fob-modules/meshy.py, a paid outside service; see
the README). What it must be:

  * at most 65,534 vertices and 65,534 faces per mesh, counted in the FILE, which
    splits vertices at every UV and normal seam (remesh at the source; decimating
    here scrambles the texture);
  * textures that fit 2048 x 2048 (larger ones are scaled down);
  * no object, mesh or material named the same as --name. The game's check
    rejects any converted file that carries the model's own name inside it, and
    the error ("wrapper empty") does not say why.

Two choices matter most:

  --height   the real height in metres. Imported models arrive at any scale.
  --keep-origin  keep the origin where you authored it instead of setting the
             model on the ground. Use it for kit pieces meant to line up when
             placed at the same point; leave it off for everything else.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FOB = HERE / ".." / "fob-modules"
sys.path.insert(0, str(FOB))
from saber import gamepaths  # noqa: E402

NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def glb_names(glb: Path) -> set[str]:
    """Every node, mesh and material name in a binary glTF."""
    data = glb.read_bytes()
    if data[:4] != b"glTF":
        raise SystemExit(f"{glb.name} is not a binary glTF (.glb)")
    n, kind = struct.unpack_from("<II", data, 12)
    if kind != 0x4E4F534A:   # "JSON"
        raise SystemExit(f"{glb.name}: first chunk is not JSON")
    doc = json.loads(data[20:20 + n])
    return {x.get("name", "") for k in ("nodes", "meshes", "materials") for x in doc.get(k, [])} - {""}


def problems(glb: Path, name: str) -> list[str]:
    """Reasons this import would fail, found before spending time on it."""
    out = []
    if not NAME.match(name):
        out.append(f"name '{name}': use lowercase letters, digits and underscores, starting with a letter")
    clash = sorted(x for x in glb_names(glb) if x.lower() == name)
    if clash:
        out.append(f"the GLB has an object, mesh or material named '{clash[0]}', the same as --name; "
                   "rename it in Blender (or pick another --name), or the game's check rejects the model")
    return out


def stray_bake(media: Path, name: str) -> Path | None:
    """glb_to_fbx bakes a normal map for every model and leaves it behind when the
    model ships its own (13 MB each). Return it when nothing uses it."""
    bake = media / "textures" / "models" / f"{name}_baked__n_d.tga"
    xml = media / "meshes" / "models" / f"{name}.xml"
    if not bake.is_file():
        return None
    if xml.is_file() and bake.name in xml.read_text(encoding="utf8", errors="replace"):
        return None
    return bake


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("glb", type=Path)
    ap.add_argument("--name", required=True, help="the model's name in the editor: lowercase, digits, underscores")
    ap.add_argument("--height", type=float, default=0.0, help="real height in metres (recommended)")
    ap.add_argument("--length", type=float, default=0.0, help="real length in metres, instead of --height")
    ap.add_argument("--collider", default="auto", choices=["auto", "hull", "box", "none"],
                    help="collision shape; auto tries hull, then box, then none")
    ap.add_argument("--keep-origin", action="store_true", help="keep the authored origin (kit pieces)")
    ap.add_argument("--two-sided", action="store_true",
                    help="draw faces from both sides (single-sided walls you can see from inside)")
    ap.add_argument("--dry-run", action="store_true", help="convert and report; install nothing")
    a = ap.parse_args()

    if not a.glb.is_file():
        raise SystemExit(f"no such file: {a.glb}")
    bad = problems(a.glb, a.name)
    if bad:
        for b in bad:
            print(f"STOP: {b}")
        return 2

    cmd = [sys.executable, str(FOB / "glb_to_fbx.py"), str(a.glb.resolve()), "--name", a.name,
           "--collider", a.collider, "--tex-max", "2048"]
    if a.height:
        cmd += ["--height", str(a.height)]
    if a.length:
        cmd += ["--length", str(a.length)]
    if a.keep_origin:
        cmd.append("--keep-origin")
    if a.two_sided:
        cmd.append("--two-sided")
    if not a.dry_run:
        cmd += ["--apply", "--check"]
    r = subprocess.run(cmd, cwd=FOB)

    media = gamepaths.media_root("expeditions")
    bake = stray_bake(media, a.name)
    if bake:
        bake.unlink()
        print(f"removed unused {bake.name}")
    if r.returncode:
        print(f"\nFAILED (exit {r.returncode}); read the lines above. Nothing to place yet.")
        return r.returncode
    if not a.dry_run:
        print(f"\nInstalled '{a.name}'. Restart the editor, then search Select Asset for: {a.name}")
        print("It may show pure white until the editor has converted it; restart once more if so.")
        print("After you place it and Pack terrain, run: python after_pack.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
