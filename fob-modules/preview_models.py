"""preview_models.py -- render every model in a set to one labelled proof sheet.

THE SELECTION GATE IS THE EYE, AND IT NEEDS A RELIABLE PICTURE.

Name filters get a search from 160 hits to 79, and no further. They cannot see
that a "philodendron" is sitting in a black plastic POT, that a "mangrove tree"
is faceted low-poly, or that a model's origin is a metre above its own roots.
Only looking can, and looking needs each model framed on its own rather than
dropped into one scene, where half of them end up off-frame or floating.

So each model is rendered alone, grounded, and framed to its own size, then the
tiles are composed into a sheet with the name and triangle count under each.

    python preview_models.py models/sketchfab --out proof.png
    python preview_models.py models/sketchfab --pick coconut_palm bamboo
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from saber import gamepaths  # noqa: E402

BLENDER = gamepaths.blender()

SCRIPT = r'''
import bpy, sys, math, mathutils
argv = sys.argv[sys.argv.index("--") + 1:]
glb, out_png = argv[0], argv[1]
yaw = float(argv[2]) if len(argv) > 2 else 0.0
tex = argv[3] if len(argv) > 3 and argv[3] != "-" else ""
bpy.ops.wm.read_factory_settings(use_empty=True)
if glb.lower().endswith(".fbx"):
    bpy.ops.import_scene.fbx(filepath=glb)
else:
    bpy.ops.import_scene.gltf(filepath=glb)
objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
# An engine-ready FBX names its albedo by a RELATIVE engine path that resolves
# under Media\textures, not next to the FBX, so Blender renders it magenta and
# the preview cannot answer the one question worth asking: is the UV mapping
# intact? Wire the texture in explicitly when the caller supplies it.
if tex:
    _m = bpy.data.materials.new("preview")
    _m.use_nodes = True
    _bsdf = _m.node_tree.nodes["Principled BSDF"]
    _ti = _m.node_tree.nodes.new("ShaderNodeTexImage")
    _ti.image = bpy.data.images.load(tex)
    _m.node_tree.links.new(_bsdf.inputs["Base Color"], _ti.outputs["Color"])
    _bsdf.inputs["Roughness"].default_value = 0.85
    for o in objs:
        o.data.materials.clear()
        o.data.materials.append(_m)
# An engine-ready FBX carries its LOD chain and its cdt_ collision hull in the
# same file. Rendering all of them stacks four copies of the truck inside one
# silhouette and buries it in its own collider, which reads as a broken model.
# Keep LOD0 only -- that is what the player sees.
_lod = [o for o in objs if o.name.lower().startswith(("cdt", "mesh_lod"))]
if _lod and len(_lod) < len(objs):
    for o in _lod:
        bpy.data.objects.remove(o, do_unlink=True)
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not objs:
    print("PREVIEW_EMPTY"); raise SystemExit(0)

# Measure the WHOLE import in world space, then sit it on the floor. A model
# whose origin is not at its base otherwise renders floating or buried, which
# reads as a broken asset when it is only an origin.
pts = [o.matrix_world @ mathutils.Vector(c) for o in objs for c in o.bound_box]
lo = mathutils.Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = mathutils.Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
size = hi - lo
h = max(size.z, 1e-6)
k = 4.0 / h                                   # every model framed at 4 m tall
mid = (lo + hi) * 0.5
for o in objs:
    o.location = (o.location - mathutils.Vector((mid.x, mid.y, lo.z))) * k
    o.scale = o.scale * k

cam_d = bpy.data.cameras.new("c"); cam = bpy.data.objects.new("c", cam_d)
bpy.context.collection.objects.link(cam); cam_d.lens = 50
cam.location = (0, -13.0, 3.2); cam.rotation_euler = (math.radians(82), 0, 0)
if yaw:
    _r = mathutils.Matrix.Rotation(math.radians(yaw), 4, "Z")
    cam.location = _r @ mathutils.Vector(cam.location)
    cam.rotation_euler = (math.radians(82), 0, math.radians(yaw))
bpy.context.scene.camera = cam
sd = bpy.data.lights.new("s", "SUN"); sd.energy = 4.0; sd.angle = 0.1
s = bpy.data.objects.new("s", sd); bpy.context.collection.objects.link(s)
s.rotation_euler = (math.radians(50), 0, math.radians(35))
bpy.ops.mesh.primitive_plane_add(size=200)
gm = bpy.data.materials.new("g"); gm.use_nodes = True
gm.node_tree.nodes["Principled BSDF"].inputs[0].default_value = (0.40, 0.38, 0.35, 1)
gm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
bpy.context.active_object.data.materials.append(gm)
w = bpy.data.worlds.new("w"); bpy.context.scene.world = w; w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (0.62, 0.68, 0.78, 1)
sc = bpy.context.scene
try: sc.render.engine = "BLENDER_EEVEE_NEXT"
except Exception: pass
sc.render.resolution_x = 320; sc.render.resolution_y = 300
sc.render.filepath = out_png
bpy.ops.render.render(write_still=True)
print("PREVIEW_OK %.2f %.2f %.2f" % (size.x, size.y, size.z))
'''


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--pick", nargs="*", default=[])
    ap.add_argument("--out", default=None)
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--tex", default=None,
                    help="albedo to wire in explicitly. An engine FBX names its "
                         "texture by a path Blender cannot resolve, so without "
                         "this the render cannot show a UV fault.")
    ap.add_argument("--yaw", type=float, default=0.0,
                    help="orbit the camera this many degrees. One view hides "
                         "the side an image-to-3d generator had to invent.")
    a = ap.parse_args()

    src = HERE / a.folder
    stems = a.pick or sorted(p.stem for p in src.glob("*.glb"))
    if not stems:
        stems = sorted(p.stem for p in src.glob("*.fbx"))
    cache = src / "_previews"
    cache.mkdir(exist_ok=True)
    tiles = []
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "p.py"
        script.write_text(SCRIPT, encoding="utf8")
        for stem in stems:
            glb = src / f"{stem}.glb"
            if not glb.is_file():
                glb = src / f"{stem}.fbx"
            png = cache / (f"{stem}.png" if not a.yaw else f"{stem}_y{a.yaw:g}.png")
            dims = ""
            if not png.is_file():
                r = subprocess.run(
                    [str(BLENDER), "--background", "--factory-startup",
                     "--python", str(script), "--", str(glb), str(png),
                     str(a.yaw), a.tex or "-"],
                    capture_output=True, text=True, timeout=900)
                line = next((l for l in r.stdout.splitlines()
                             if l.startswith("PREVIEW_OK")), "")
                dims = line.replace("PREVIEW_OK ", "")
                print(f"  {'ok  ' if png.is_file() else 'FAIL'} {stem[:44]:46s} {dims}")
            tiles.append((stem, png, dims))

    from PIL import Image, ImageDraw
    cw, ch = 330, 345
    cols = a.cols
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cw, rows * ch), (245, 245, 245))
    d = ImageDraw.Draw(sheet)
    for i, (stem, png, dims) in enumerate(tiles):
        x, y = (i % cols) * cw, (i // cols) * ch
        if png.is_file():
            im = Image.open(png).convert("RGB")
            sheet.paste(im, (x + (cw - im.width) // 2, y + 4))
        j = (src / f"{stem}.json")
        tris = ""
        if j.is_file():
            try:
                tris = f"{json.loads(j.read_text(encoding='utf8')).get('faces', 0):,} tris"
            except Exception:
                pass
        d.text((x + 6, y + ch - 36), stem[:40], fill=(0, 0, 0))
        d.text((x + 6, y + ch - 20), tris, fill=(90, 90, 90))
    out = Path(a.out) if a.out else (src / "proof.png")
    sheet.save(out)
    print(f"\n  -> {out}  ({len(tiles)} tiles)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
