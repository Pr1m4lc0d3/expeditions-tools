"""Blender side of add_rock_lights.py: the pod mesh (engine space, numpy .npz) into one FBX the game's
converter takes, with a box collider frame named cdt (RootNode / cdt / mesh, like every truck add-on).

    blender -b --factory-startup -P pods_blender.py -- <spec.json>

spec: {"parts": npz, "atlas_png": png, "material": name, "name": mesh name, "fbx_dir": folder}
Engine axes (x fwd, y up, z left) map to Blender (x, z, y), which flips handedness, so the winding is
reversed here. Each face carries an atlas swatch (0 black powder coat, 1 frosted lens; 4 swatches a row).
"""
import json
import math
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Vector

spec = json.loads(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8").read())
bpy.ops.wm.read_factory_settings(use_empty=True)
d = np.load(spec["parts"])
V, F, SW = d["v"], d["f"], d["sw"]


def uv_of(sw):
    return ((sw % 4) + 0.5) / 4, 1 - ((sw // 4) + 0.5) / 4


img = bpy.data.images.load(spec["atlas_png"])
mat = bpy.data.materials.new(spec["material"])
mat.use_nodes = True
tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
tex.image = img
tex.interpolation = "Closest"
mat.node_tree.links.new(tex.outputs["Color"], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])

coll = bpy.data.collections.new("rock_lights")
bpy.context.scene.collection.children.link(coll)
bm = bmesh.new()
vs = [bm.verts.new((float(p[0]), float(p[2]), float(p[1]))) for p in V]
uv = bm.loops.layers.uv.new("UVMap")
skipped = 0
for (a, b, c), s in zip(F, SW):
    try:
        fc = bm.faces.new((vs[int(c)], vs[int(b)], vs[int(a)]))
    except ValueError:
        skipped += 1
        continue
    for l in fc.loops:
        l[uv].uv = uv_of(int(s))
    fc.smooth = True
name = spec["name"]
me = bpy.data.meshes.new(name)
bm.to_mesh(me)
bm.free()
if hasattr(me, "set_sharp_from_angle"):
    me.set_sharp_from_angle(angle=math.radians(30))
me.materials.append(mat)
ob = bpy.data.objects.new(name, me)
coll.objects.link(ob)
co = np.array([v.co[:] for v in me.vertices])
lo, hi = co.min(0), co.max(0)

cme = bpy.data.meshes.new(name + "_cdt")
cbm = bmesh.new()
for v in bmesh.ops.create_cube(cbm, size=1.0)["verts"]:
    v.co = Vector(((lo[0] + hi[0]) / 2 + v.co.x * (hi[0] - lo[0]),
                   (lo[1] + hi[1]) / 2 + v.co.y * (hi[1] - lo[1]),
                   (lo[2] + hi[2]) / 2 + v.co.z * (hi[2] - lo[2])))
cbm.to_mesh(cme)
cbm.free()
cd = bpy.data.objects.new("cdt", cme)
coll.objects.link(cd)

bpy.ops.object.select_all(action="DESELECT")
ob.select_set(True)
cd.select_set(True)
bpy.ops.export_scene.fbx(filepath=f'{spec["fbx_dir"]}/{name}.fbx', use_selection=True,
                         path_mode="STRIP", embed_textures=False, bake_anim=False, add_leaf_bones=False,
                         axis_forward="Y", axis_up="Z", apply_unit_scale=False, global_scale=1.0,
                         object_types={"MESH", "EMPTY"}, mesh_smooth_type="FACE")
report = {"verts": len(me.vertices), "faces": len(me.polygons), "skipped": skipped,
          "lo": [float(lo[0]), float(lo[2]), float(lo[1])], "hi": [float(hi[0]), float(hi[2]), float(hi[1])]}
print("REPORT " + json.dumps(report))
