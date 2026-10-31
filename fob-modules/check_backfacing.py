"""Measure how much of a model the player sees from BEHIND, and gate on it.

Renders the model from 8 directions with every front face white and every face
seen from its back red (Blender Geometry > Backfacing), and counts red pixels as
a share of all model pixels. Run it on Meshy's GLB and on our built FBX: the FBX
must not show more than the GLB.

Why this exists (2026-10-24): under TwoSided="true" a face seen from behind is lit
from the wrong side and shows as a sharp dark wedge that sits still on the model
from every camera angle -- the "scrambled texture" that cost three weeks on Doc
Windsor and Platt's Gate Works. check_normals.py PASSED those builds, because it
measured corner normals, not which side of a face the player looks at. This
measures what the player sees. glb_to_fbx.py --check runs it and fails the build.

    python check_backfacing.py <meshy.glb> <built.fbx> [--out DIR]
    python check_backfacing.py <model.glb|fbx>                 (measure one)
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from saber import gamepaths

BLENDER = gamepaths.blender()
# One folder per model brand: the red renders and backface.json, the record
# install_model_to_mod.py demands before it will put a model into a mod.
REPORTS = Path(__file__).resolve().parent / "build_reports"

# The FBX may show at most this much MORE than Meshy's GLB (percentage points),
# and never more than ABS_MAX overall. Calibrated 2026-10-25: every build the owner
# judged clean in game measured <= 0.17%; Doc's base with visible wall scramble
# measured 0.25%. See the skill's custom-model.md for the table.
MARGIN = 0.25
ABS_MAX = 0.2
# Deleting inner-skin faces must not open holes: the share of Meshy's outline
# missing from ours, worst view. Clean builds measured 0.00-0.06% (2026-10-25).
HOLE_MAX = 0.25

BLENDER_SCRIPT = r'''
import bpy, sys, math, json, mathutils
import numpy as np
a = sys.argv[sys.argv.index("--") + 1:]
src, out = a[0], a[1]
bpy.ops.wm.read_factory_settings(use_empty=True)
(bpy.ops.import_scene.fbx if src.lower().endswith(".fbx") else bpy.ops.import_scene.gltf)(filepath=src)
ms = []
for o in list(bpy.context.scene.objects):
    # collision_* is a supplied collider in the source glTF; glb_to_fbx strips it,
    # so leaving it in the reference reads as "faces missing from the build"
    if o.type != "MESH" or o.name.startswith(("cdt", "collision_")) or "_lod" in o.name:
        bpy.data.objects.remove(o)
    else:
        ms.append(o)
m = bpy.data.materials.new("bf"); m.use_nodes = True; nt = m.node_tree; n = nt.nodes
for x in list(n): n.remove(x)
geo = n.new("ShaderNodeNewGeometry"); mix = n.new("ShaderNodeMix"); mix.data_type = "RGBA"
mix.inputs[6].default_value = (1, 1, 1, 1); mix.inputs[7].default_value = (1, 0, 0, 1)
em = n.new("ShaderNodeEmission"); outn = n.new("ShaderNodeOutputMaterial")
nt.links.new(geo.outputs["Backfacing"], mix.inputs[0])
nt.links.new(mix.outputs[2], em.inputs[0]); nt.links.new(em.outputs[0], outn.inputs[0])
m.use_backface_culling = False
for o in ms:
    o.data.materials.clear(); o.data.materials.append(m)
pts = [o.matrix_world @ mathutils.Vector(c) for o in ms for c in o.bound_box]
lo = mathutils.Vector([min(p[i] for p in pts) for i in range(3)])
hi = mathutils.Vector([max(p[i] for p in pts) for i in range(3)])
c, r = (lo + hi) / 2, (hi - lo).length / 2
sc = bpy.context.scene
eng = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
sc.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in eng else "BLENDER_EEVEE"
sc.render.resolution_x = sc.render.resolution_y = 700
sc.render.film_transparent = True
sc.view_settings.view_transform = "Standard"
views = []
for az in range(0, 360, 45):
    el = 8 if az % 90 == 0 else 35          # four side views, four from above the corners
    t, e = math.radians(az), math.radians(el)
    views.append((az, el, mathutils.Vector((math.cos(t) * math.cos(e), math.sin(t) * math.cos(e), math.sin(e)))))
red = total = 0
per = []
for az, el, d in views:
    cam = bpy.data.objects.new("c", bpy.data.cameras.new("c")); cam.data.type = "ORTHO"
    cam.data.ortho_scale = r * 2.05
    cam.location = c + d * r * 4; cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    cam.data.clip_end = r * 10
    sc.collection.objects.link(cam); sc.camera = cam
    path = "%s_az%03d.png" % (out, az)
    sc.render.filepath = path; bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path)
    px = np.array(img.pixels[:], dtype=np.float32).reshape(-1, 4)
    bpy.data.images.remove(img)
    model = px[:, 3] > 0.5
    back = model & (px[:, 1] < 0.5)
    red += int(back.sum()); total += int(model.sum())
    per.append({"az": az, "el": el, "pct": round(100.0 * back.sum() / max(1, model.sum()), 3)})
print("BACKFACE_REPORT " + json.dumps({"pct": round(100.0 * red / max(1, total), 3), "views": per}))
'''


def measure(model: Path, out_dir: Path, tag: str) -> dict:
    """Share (%) of model pixels seen from behind, over 8 views. Saves the renders."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "bf.py"
        script.write_text(BLENDER_SCRIPT, encoding="utf8")
        r = subprocess.run([str(BLENDER), "--background", "--factory-startup",
                            "--python", str(script), "--", str(model), str(out_dir / tag)],
                           capture_output=True, text=True, timeout=1200)
    line = next((l for l in r.stdout.splitlines() if l.startswith("BACKFACE_REPORT ")), None)
    if not line:
        raise RuntimeError(f"back-face render failed for {model}:\n{r.stdout[-1200:]}\n{r.stderr[-600:]}")
    return json.loads(line[len("BACKFACE_REPORT "):])


def verdict(glb_pct: float, fbx_pct: float) -> tuple[bool, str]:
    """The built model may not show more faces from behind than Meshy's original."""
    if fbx_pct > glb_pct + MARGIN:
        return False, (f"built model shows {fbx_pct:.2f}% of its pixels from behind, "
                       f"Meshy's GLB {glb_pct:.2f}%: the conversion turned faces around")
    if fbx_pct > ABS_MAX:
        return False, (f"built model shows {fbx_pct:.2f}% of its pixels from behind "
                       f"(limit {ABS_MAX}%), even though Meshy's GLB has {glb_pct:.2f}%")
    return True, f"seen from behind: built {fbx_pct:.2f}%, Meshy's GLB {glb_pct:.2f}%"


def holes(out_dir: Path, yaw: float = 0.0) -> float:
    """Worst view's share (%) of the GLB's outline that the FBX does not cover.

    A build turned by --yaw is compared view for view with the GLB turned the same
    way: GLB view `az` is FBX view `az + yaw`. Exact for multiples of 90 degrees,
    since the framing depends only on the bounding box's centre and diagonal."""
    import numpy as np
    from PIL import Image
    worst = 0.0
    for g in sorted(out_dir.glob("glb_az*.png")):
        az = int(g.stem[-3:])
        a = np.array(Image.open(g).convert("RGBA"))[:, :, 3] > 127
        b = np.array(Image.open(out_dir / f"fbx_az{int(az + yaw) % 360:03d}.png").convert("RGBA"))[:, :, 3] > 127
        worst = max(worst, 100.0 * (a & ~b).sum() / max(1, a.sum()))
    return round(worst, 3)


def gate(glb: Path, fbx: Path, out_dir: Path, yaw: float = 0.0) -> tuple[bool, str]:
    g = measure(glb, out_dir, "glb")["pct"]
    f = measure(fbx, out_dir, "fbx")["pct"]
    ok, msg = verdict(g, f)
    h = holes(out_dir, yaw)
    if ok and h > HOLE_MAX:
        ok, msg = False, (f"{h:.2f}% of Meshy's outline is missing from the build in one "
                          f"view (limit {HOLE_MAX}%): faces were deleted that the player sees")
    msg += f"; outline missing {h:.2f}%"
    st = fbx.stat()
    (out_dir / "backface.json").write_text(json.dumps(
        {"glb": str(glb), "fbx": str(fbx), "glb_pct": g, "fbx_pct": f, "pass": ok,
         "fbx_size": st.st_size, "fbx_mtime": st.st_mtime}, indent=1))
    return ok, msg


def passed(brand: str, fbx: Path) -> tuple[bool, str]:
    """Did THIS fbx (same size and timestamp) pass the gate? For install_model_to_mod."""
    rec = REPORTS / brand / "backface.json"
    if not rec.is_file():
        return False, (f"no back-face check on record ({rec}); rebuild with "
                       f"glb_to_fbx.py --check")
    r = json.loads(rec.read_text())
    if not fbx.is_file():
        return False, f"{fbx} missing"
    st = fbx.stat()
    if r.get("fbx_size") != st.st_size or abs(r.get("fbx_mtime", 0) - st.st_mtime) > 1:
        return False, ("the installed FBX changed after its back-face check; "
                       "rebuild with glb_to_fbx.py --check")
    if not r.get("pass"):
        return False, (f"FAILED its back-face check: built {r['fbx_pct']:.2f}% seen from "
                       f"behind vs Meshy's GLB {r['glb_pct']:.2f}% (renders: {rec.parent})")
    return True, f"back-face check passed: built {r['fbx_pct']:.2f}%, GLB {r['glb_pct']:.2f}%"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("models", type=Path, nargs="+", help="GLB [FBX]")
    ap.add_argument("--out", type=Path, help="where the red renders go")
    a = ap.parse_args()
    out = a.out or Path(tempfile.gettempdir()) / f"backface_{a.models[-1].stem}"
    if len(a.models) == 1:
        rep = measure(a.models[0], out, a.models[0].suffix[1:])
        print(f"{a.models[0].name}: {rep['pct']:.2f}% seen from behind  "
              + "  ".join(f"az{v['az']}:{v['pct']}" for v in rep["views"]))
        print(f"renders: {out}")
        return 0
    ok, msg = gate(a.models[0], a.models[1], out)
    print(("PASS  " if ok else "FAIL  ") + msg)
    print(f"renders (red = seen from behind): {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
