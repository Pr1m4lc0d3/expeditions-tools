"""Build the low-poly from Meshy's FULL-DETAIL model and bake its colour from it.

Replaces Meshy's remesh for any model that carries lettering. Measured
2026-10-25: the remesh broke letters on three signs out of seven, each with a
stray triangle of its own (Tuck's sign with a lamp on it at 30k and 40k alike,
Meg's cart with nothing on the sign at all), while the full-detail model read
cleanly every time. Here the low-poly is our own collapse of the full-detail
model, unwrapped fresh, and its colour is baked straight from the full-detail
surface, so a stray face takes the colour of whatever is behind it.

Then build with the normal map baked from the same full-detail model:

    python rebake_from_hd.py <brand>.glb <brand>_rb.glb
    python glb_to_fbx.py <brand>_rb.glb --name <brand> --high <brand>.glb --apply --check ...

⛔ A small part bolted onto a sign (Tuck's lamp) still collapses into a visible
face: keep signs clear in the concept image. See custom-model.md.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from saber import gamepaths

BLENDER = gamepaths.blender()

SCRIPT = r'''
import bpy, bmesh, sys, os, math, time
a = sys.argv[sys.argv.index("--") + 1:]
SRC, OUT, TRIS, SIZE = a[0], a[1], int(a[2]), int(a[3])
t0 = time.time()
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=SRC)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
for o in bpy.context.scene.objects:
    o.select_set(o in meshes)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
hd = bpy.context.view_layer.objects.active
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
hd.name = "hd"

lo = hd.copy(); lo.data = hd.data.copy(); lo.name = "lo"
bpy.context.scene.collection.objects.link(lo)
size = max(hd.dimensions)
bm = bmesh.new(); bm.from_mesh(lo.data)
bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=size * 1e-5)
bm.to_mesh(lo.data); bm.free()
for uv in list(lo.data.uv_layers):
    lo.data.uv_layers.remove(uv)
m = lo.modifiers.new("dec", "DECIMATE")
m.ratio = min(1.0, TRIS / max(1, len(lo.data.polygons)))
bpy.context.view_layer.objects.active = lo
for o in bpy.context.scene.objects:
    o.select_set(o is lo)
bpy.ops.object.modifier_apply(modifier="dec")

lo.data.uv_layers.new(name="UVMap")
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=4 / SIZE)
bpy.ops.object.mode_set(mode="OBJECT")

img = bpy.data.images.new("baked", SIZE, SIZE, alpha=False)
mat = bpy.data.materials.new("bakedmaterial"); mat.use_nodes = True
nt = mat.node_tree
tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = img
nt.links.new(tex.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
nt.nodes.active = tex
lo.data.materials.clear(); lo.data.materials.append(mat)

sc = bpy.context.scene
sc.render.engine = "CYCLES"; sc.cycles.device = "CPU"; sc.cycles.samples = 1
bk = sc.render.bake
bk.use_pass_direct = False; bk.use_pass_indirect = False; bk.use_pass_color = True
bk.use_selected_to_active = True
bk.cage_extrusion = size * 0.004
bk.max_ray_distance = size * 0.01
bk.margin = 8
for o in sc.objects:
    o.select_set(o in (hd, lo))
bpy.context.view_layer.objects.active = lo
bpy.ops.object.bake(type="DIFFUSE")

img.filepath_raw = os.path.splitext(OUT)[0] + "_albedo.png"; img.file_format = "PNG"; img.save()
bpy.data.objects.remove(hd)
for o in sc.objects:
    o.select_set(o is lo)
bpy.ops.export_scene.gltf(filepath=OUT, export_format="GLB", use_selection=True)
print("REBAKE_OK %d triangles  %.0fs" % (len(lo.data.polygons), time.time() - t0))
'''


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hd", type=Path, help="Meshy's full-detail GLB")
    ap.add_argument("out", type=Path)
    ap.add_argument("--tris", type=int, default=30000)
    ap.add_argument("--size", type=int, default=4096)
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as td:
        s = Path(td) / "rebake.py"
        s.write_text(SCRIPT, encoding="utf8")
        r = subprocess.run([str(BLENDER), "-b", "--factory-startup", "-P", str(s), "--",
                            str(a.hd), str(a.out), str(a.tris), str(a.size)],
                           capture_output=True, text=True, timeout=3600)
    line = next((l for l in r.stdout.splitlines() if l.startswith("REBAKE_OK")), None)
    if not line or not a.out.exists():
        print(r.stdout[-1500:], r.stderr[-800:])
        print("!! rebake failed")
        return 1
    print("  " + line + f"  -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
