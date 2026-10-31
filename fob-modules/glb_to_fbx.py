"""glb_to_fbx.py -- turn a downloaded glTF into a model the Expeditions editor loads.

`fetch_blendkit.py` brings back glTF. The engine wants FBX plus two XML files and
textures under specific names. This closes that gap.

WHY THE FIRST VERSION OF THIS TOOL PRODUCED MODELS THAT NEVER LOADED

It exported the glTF meshes as-is. `ResourceConverter` rejected every one:

    Mesh| Number of vertices (53436) or faces (97964) is exceeded limit (65534)
          in mesh 'mesh_namaqualand_boulder_02_LOD0'.
    Mesh| Can't load 'meshes/models/namaqualand_boulder_02.fbx' model file

**65,534 is a hard per-mesh ceiling on BOTH vertices and faces** -- a 16-bit index
buffer. Photogrammetry assets blow through it: the boulder's LOD0 alone is 97,964
faces. Library models must be decimated, not just repacked.

Three more requirements from "Custom Level Entities" section 2.2.1 that the first
version did not meet:

  * "All meshes of the model must form a hierarchy, with one root node."
  * "The model must contain at least one collision mesh. Names of collision
    meshes should start with the cdt_ prefix."
  * "All objects and meshes must have the identity scale ... (1, 1, 1) ...
    identity rotation (0, 0, 0)."

And from 2.2.3: textures must be `.tga`, sized 2^N x 2^M, and should not exceed
2048x2048. The BlendKit albedo arrived far outside that.

⛔ DECIMATE AS A LAST RESORT. REMESH AT THE SOURCE INSTEAD.

Decimation keeps the silhouette and destroys the UV atlas. A Meshy asset
arrived at 2,202,674 triangles against the 65,534 ceiling, so this tool
collapsed 97% of it. In the editor the result had the right SHAPE and a
scrambled SKIN, which reads as a texture bug and is not one:

    source png -> tga   MAE  4.92 as-is,  28.77 flipped
    source png -> dds   MAE  5.56 as-is,  28.70 flipped

The image was correct at every stage. The mapping was destroyed.

Meshy's remesh endpoint rebuilds the mesh server-side at a requested density
with its UVs intact, for 5 credits:

    POST /openapi/v1/remesh   {input_task_id, target_polycount, topology}
    2,202,674 triangles -> 29,509 tris / 47,604 verts, so this tool then
    reports "decimated 29,509 tris -> 29,509" and touches nothing.

`target_polycount` is IGNORED by image-to-3d but IS honoured by remesh. Anything
heavy enough to need decimation here should be remeshed first.

VERIFY WITH THE CONVERTER, NOT BY EYE

`--check` runs the real `ResourceConverter --mod-convert` over the installed
files and reports what it says. That is the only thing whose opinion counts, and
it is fast. Do not trust an install that has not passed it.

NO PAID ADDON IS NEEDED. Saber's FBX page says to buy Better FBX, but that page
is the TRUCK workflow and exists to handle armatures. A static prop has no bones,
so Blender's built-in exporter covers it.

    python glb_to_fbx.py models/blendkit/namaqualand_boulder_02.glb
    python glb_to_fbx.py models/blendkit/namaqualand_boulder_02.glb --apply --check
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from saber import gamepaths, project
from saber import convert

BLENDER = gamepaths.blender()
RESOURCE_CONVERTER = gamepaths.resource_converter("expeditions")
BIN_EDITOR = RESOURCE_CONVERTER.parent.parent / "BinEditor"

MEDIA = project.MEDIA
# WHICH FOLDER A MESH BELONGS IN IS NOT COSMETIC.
#
# A `ModelBrand` that names a mesh does it through `<Landmark Mesh="...">`, and
# measured across the 4,372 shipped model classes in initial.pak, that element
# is the ONLY one that carries a Mesh attribute -- 1,699 of them. Of those,
# 1,695 point at `landmarks/`. The three that point at `models/` all name
# `models/_missing_model`, which is the engine's broken-model placeholder.
#
# So: anything a class references by name belongs in `landmarks`. `models` is
# for meshes resolved implicitly by matching the class name, which is what the
# remaining 2,673 classes do.
FOLDER = "models"
MESHES = MEDIA / "meshes" / FOLDER
CLASSES = MEDIA / "classes" / "models"      # classes always live under models/
TEXTURES = MEDIA / "textures" / FOLDER


def set_folder(folder: str) -> None:
    """Point the install paths at `models` or `landmarks`.

    ⛔ `landmarks` IS NOT FINISHED. Tried once and it fails two layers down:

        IO| Cant open find file textures/dds/models_pebblegas_station_lmk__d.dds

    The mesh XML still writes its AlbedoMap as `models/<base>__d.tga`, and
    `verify_model.py` looks for the installed files under `meshes/models`. Both
    assume the folder rather than taking it, so the converter looks for textures
    under a `models_` prefix that nothing wrote.

    A mesh in `models/` is NOT a workaround -- it is the mechanism 2,673 of the
    4,372 shipped classes use, resolving the mesh implicitly by matching the
    class name. `landmarks` is only needed when a class names its mesh through
    `<Landmark Mesh=...>`. Finish this before using it, do not force it.
    """
    global FOLDER, MESHES, TEXTURES
    if folder != "models":
        raise SystemExit(
            "--folder landmarks is unfinished: the mesh XML hardcodes "
            "'models/' in its texture paths and verify_model.py looks under "
            "meshes/models, so the converter cannot find the textures. Install "
            "under models/ and let the class resolve the mesh by name.")
    FOLDER = folder
    MESHES = MEDIA / "meshes" / folder
    TEXTURES = MEDIA / "textures" / folder

# The engine's 16-bit index buffer. Both counts are checked, so the budget has to
# hold for faces as well as vertices; decimation targets faces and vertices follow.
HARD_LIMIT = 65534
DEFAULT_BUDGET = 60000          # margin under the ceiling

BLENDER_SCRIPT = r'''
import bpy, bmesh, math, mathutils, sys, json, os, re

argv = sys.argv[sys.argv.index("--") + 1:]
src, out_fbx, out_tex, base, budget, tex_max = argv[0], argv[1], argv[2], argv[3], int(argv[4]), int(argv[5])
lod_ratios = [float(x) for x in argv[7].split(",") if x.strip()]
collider = argv[8]
# PLANTS NEED THE MESH OBJECT CALLED `plant`, NOT `mesh`.
# The converter builds a plant billboard by looking for a source LOD in an
# object of that name, and says so when it is missing: "Unable to create plant
# billboard. Can't find source LOD in object mesh 'plant'". Every shipped plant
# uses MeshName="plant" with a node spelled the same way.
MESH_NAME = argv[9] if len(argv) > 9 and argv[9] else "mesh"
# REAL-WORLD HEIGHT. Imported models arrive at whatever scale their author
# worked in: a downloaded philodendron measured 0.05 m tall and a fern 8.16 m
# wide. A brush's MinSize/MaxSize MULTIPLY that, so an unnormalised asset is
# either invisible or absurd. 0 leaves the source scale alone.
WANT_H = float(argv[10]) if len(argv) > 10 and argv[10] else 0.0
# WHICH AXIS the target applies to. Normalising HEIGHT is right for a building
# and WRONG for anything carrying a roof rack, mast or awning: the accessory
# eats the budget and the BODY comes out small. Measured 2026-10-17 -- Doc
# Windsor's jeep, built with `--height 2.1`, landed 2.86 m long against the
# 4.95 m of PebbleGas's tanker, which is confirmed correct in game. In world,
# beside the player's truck, it read as "really tiny", and it was.
# `x` targets the LENGTH instead, which no roof rack can inflate.
WANT_AXIS = (argv[11] if len(argv) > 11 and argv[11] else "z").lower()
# Metres to set the grounded model INTO the terrain. Every vanilla main-base FOB
# module extends below zero (shared.pak bounding boxes: -0.19 .. -0.63 m, about
# -0.3 typical), so uneven ground never shows a gap under it.
SINK = float(argv[12]) if len(argv) > 12 and argv[12] else 0.0
# FBX smoothing data. "FACE" writes per-face smoothing groups; "OFF" writes the
# normals alone, so the converter cannot rebuild them from smoothing flags.
SMOOTHING = (argv[13] if len(argv) > 13 and argv[13] else "FACE").upper()
# Corner normals averaged over faces within this many degrees at the same
# position (smooth_normals). 0 keeps the source's normals.
SMOOTH_ANGLE = float(argv[14]) if len(argv) > 14 and argv[14] else 0.0
# The FULL-DETAIL model to bake the normal map from (--high). Without it the
# normal is baked off this file's own geometry, which is right for a remesh but
# WRONG for a low-poly rebuilt from a Meshy model: a face the low-poly gained
# while collapsing a small part (Tuck's sign lamp) gets its own slanted normal
# and shows in game as a dark triangle over the letters, even with the colour
# baked correctly from the full-detail model (2026-10-25).
HIGH = argv[15] if len(argv) > 15 and argv[15] else ""
# Leave Z alone (--keep-origin): for a part authored in metres around a ground
# origin, e.g. a sign plate that hangs at mounting height over a separate post.
KEEP_ORIGIN = len(argv) > 16 and argv[16] == "1"
# Turn the model about the vertical axis (--yaw, degrees, positive = counter-
# clockwise seen from above), AFTER --length scales it, so the length target
# still applies to the model's own X and a turned building keeps its size.
YAW = float(argv[17]) if len(argv) > 17 and argv[17] else 0.0

bpy.ops.wm.read_factory_settings(use_empty=True)

# 🚨 TURN THE VIEW TRANSFORM OFF BEFORE ANY TEXTURE IS SAVED.
#
# `img.save_render()` runs the pixels through the scene's COLOUR MANAGEMENT,
# and Blender 4+ defaults to the AgX view transform, which desaturates and
# compresses. Every texture this tool has ever written went through it.
# Measured 2026-10-17 on Doc Windsor:
#
#     albedo        Meshy source mean RGB (77.6, 58.5, 46.8)
#                   written TGA  mean RGB (70.9, 53.0, 40.9)   MAE 6.43
#     flat normal   written (133, 144, 203)   must be (128, 128, 255)
#     flat shading  written (111, 177, 200)   must be (0, 204, 255)
#
# A tilted, weakened normal on every surface is why models come out looking
# washed and muddy in game while the texture file itself looks fine. This is
# the "texture mess on all models", and it is one line of colour management.
_vs = bpy.context.scene.view_settings
try:
    _vs.view_transform = "Standard"
    _vs.look = "None"
    _vs.exposure = 0.0
    _vs.gamma = 1.0
    bpy.context.scene.display_settings.display_device = "sRGB"
except Exception as _e:
    # `report` is not built yet at this point in the script, so say it here.
    print("WARNING: could not force Standard view transform: %s" % _e)

bpy.ops.import_scene.gltf(filepath=src)

report = {"meshes": [], "materials": [], "textures": {}, "decimated": [], "notes": []}

meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not meshes:
    print("BLENDER_REPORT " + json.dumps({"error": "no mesh in glTF"}))
    raise SystemExit(0)

# A SUPPLIED COLLIDER. A mesh named `collision_*` in the glTF is not part of the
# model: it is pulled out before the join and, with --collider box, its bounds
# become the cdt instead of the whole model's. Signs need this: one box round a
# plate on its post fills the air under the plate beside the post, an invisible
# wall; the sign's collision should be its post (2026-10-29).
GIVEN_BOX = None
_given = [o for o in meshes if o.name.startswith("collision_")]
if _given:
    for _o in _given:
        bpy.ops.object.select_all(action="DESELECT")
        _o.select_set(True)
        bpy.context.view_layer.objects.active = _o
        if _o.parent is not None:
            bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    _pts = [_o.matrix_world @ v.co for _o in _given for v in _o.data.vertices]
    GIVEN_BOX = (mathutils.Vector([min(p[i] for p in _pts) for i in range(3)]),
                 mathutils.Vector([max(p[i] for p in _pts) for i in range(3)]))
    for _o in _given:
        bpy.data.objects.remove(_o, do_unlink=True)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]

# Biggest first.
meshes.sort(key=lambda o: -len(o.data.polygons))

def apply_transforms(o):
    """2.2.1: identity scale and identity rotation on every object.

    UNPARENT FIRST, KEEPING THE WORLD TRANSFORM. glTF is Y-up and Blender is
    Z-up, so the importer corrects between them with a +90 degree X rotation --
    and it puts that rotation on the PARENT, not on the mesh. `transform_apply`
    only bakes an object's own local transform, so applying it to the child
    baked nothing, and dropping the parent afterwards threw the correction away.
    The export then converted Z-up to Y-up a second time and every asset came
    out rotated 90 degrees about X.

    It is invisible from inside the build: Blender's own numbers are right at
    every step, and the FBX on disk is wrong. Eight plants went into the map
    lying on their sides, and the saucer had been standing on its edge since
    September without being noticed.
    """
    bpy.ops.object.select_all(action="DESELECT")
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    if o.parent is not None:
        bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

def tri_count(o):
    o.data.calc_loop_triangles()
    return len(o.data.loop_triangles)

def clean_mesh(o, weld=True):
    """Repair what decimation leaves behind.

    Every model still asserting after the loose-vertex fix was one heavy enough to
    be decimated, and COLLAPSE routinely leaves zero-area faces and coincident
    vertices. Those are invisible in Blender and are exactly the kind of thing a
    converter indexes straight off the end of an array.

    🚨 `weld=False` for a mesh that has NOT been decimated. Welding merges every
    UV and normal seam and the source's split normals are lost: measured
    2026-10-24, Doc Windsor's trailer went 59,821 -> 14,055 vertices and, on its
    largest faces, corner normals leaning over 25 degrees went from 0% of the
    area to 60%. In game that is flat panels shading as diagonal light/dark
    triangles and soft, smeared corrugation. Unwelded, the normals survive the
    whole chain through FBX (0%), and Meshy's own count fits under 65,534.
    """
    bm = bmesh.new()
    bm.from_mesh(o.data)
    before = (len(bm.verts), len(bm.faces))
    if weld:
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
    bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges[:])
    # triangulate after dissolving: dissolve can turn triangles into n-gons
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    # ⛔ No recalc_face_normals. It guesses "outside" per connected region, and an
    # unwelded mesh is thousands of one-triangle regions: on plattgate it flipped
    # 13,379 of 26,658 faces (48% of the area, 272 of the 533 largest), and 4.2%
    # even welded (thin open shells). Meshy's own winding needs 0 flips. Flipped
    # faces are drawn from behind under TwoSided, lit from the wrong side: the
    # big dark triangles across Platt's sign, unchanged by every normal fix
    # (measured 2026-10-24).
    bm.to_mesh(o.data)
    after = (len(bm.verts), len(bm.faces))
    bm.free()
    o.data.update()
    return before, after

def drop_loose(o):
    """Delete vertices that belong to no face.

    Decimate COLLAPSE reduces FACES and leaves orphaned vertices behind. On a
    pine tree that is the difference between passing and failing: faces went
    951,311 -> 6,648 while vertices sat at 311,877 and never moved, so the model
    stayed over the 65,534 vertex limit no matter how hard it was decimated.
    """
    bm = bmesh.new()
    bm.from_mesh(o.data)
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
        bm.to_mesh(o.data)
    bm.free()
    return len(loose)

def decimate(o, budget):
    """Collapse until faces AND vertices fit. Iterated because a collapse ratio
    is approximate and undershooting once is cheaper than shipping a reject."""
    n = drop_loose(o)
    if n:
        report["notes"].append("removed %d loose vertices" % n)
    # Clean BEFORE testing the budget, not only after a collapse.
    #
    # A mesh that already fits returned from the loop below on the first test,
    # so it was the one mesh in the whole chain that never got cleaned -- the
    # LOD duplicates are all decimated and therefore all cleaned. Generated
    # meshes carry degenerate faces and coincident vertices straight from the
    # generator, and `ResourceConverter` asserts on them:
    #
    #     Condition: IsValidIdx(index)   ds_vector_new.h:314
    #
    # That is why mullein crashed the converter while beargrass converted: the
    # mullein LOD0 came in under budget and skipped the repair, the beargrass
    # LOD0 was over budget and got it.
    clean_mesh(o, weld=False)   # under budget: keep the source's seams and normals
    for _ in range(6):
        faces, verts = tri_count(o), len(o.data.vertices)
        if faces <= budget and verts <= budget:
            break
        ratio = min(budget / max(faces, 1), budget / max(verts, 1)) * 0.95
        m = o.modifiers.new("dec", "DECIMATE")
        m.decimate_type = "COLLAPSE"
        m.ratio = max(0.01, min(0.99, ratio))
        bpy.context.view_layer.objects.active = o
        bpy.ops.object.modifier_apply(modifier=m.name)
        clean_mesh(o)   # each collapse orphans vertices and leaves slivers
    # ⛔ Do not "restore" normals with a Data Transfer after welding instead. Tried
    # 2026-10-24: Meshy models thin walls on both sides, so the nearest source face
    # is often the far side of the same wall, and the transfer flipped normals on
    # 39% of the largest faces. Not welding is the fix.

def fix_backfacing(o, n_dirs=96, res=500, passes=4):
    """Flip or delete faces that the player would see from BEHIND.

    Casts one ray per "pixel" of a res x res view from `n_dirs` directions above
    the ground (what a player can actually look at) and records, for every face
    a ray hits FIRST, whether it was hit on its front or its back. Then, for each
    face hit on its back:
      - covered at its centre and three inner points by another surface within
        5 cm in front -> a scrap of Meshy's inner wall skin poking through the
        outer one: delete it, so the outer surface it covers shows;
      - otherwise, if hit from behind more often than from the front -> its
        winding is backwards: flip it.
    Repeated until nothing changes (deleting one layer can expose another).

    ⛔ Why per pixel and not per face (2026-10-25). The first version cast rays
    FROM four sample points per face. Doc Windsor's corrugated walls are full of
    long thin inner-skin triangles that only PARTLY cross the outer skin; all
    four sample points sat on the buried part, so the visible part was never
    seen. In game: vertical scramble down the container walls. Seen from behind,
    8 gate views: per-face version 0.25%, this one 0.03%, no new holes.
    (A single-point cover test made holes; the all-points test makes none.)
    Faces hit about as often from the front as from the back are single sheets
    seen from both sides; flipping them only moves the fault, so they stay.
    """
    from mathutils.bvhtree import BVHTree
    dirs = []
    i = 0
    while len(dirs) < n_dirs:
        z = 1 - 2 * (i + 0.5) / (n_dirs * 2)
        if z >= -0.25:              # nobody looks at a module from under the ground
            r = math.sqrt(1 - z * z); t = math.pi * (3 - math.sqrt(5)) * i
            dirs.append(mathutils.Vector((r * math.cos(t), r * math.sin(t), z)))
        i += 1
    me = o.data
    mw = o.matrix_world
    total_del = total_flip = 0
    for _ in range(passes):
        vs = [mw @ v.co for v in me.vertices]
        bvh = BVHTree.FromPolygons(vs, [list(p.vertices) for p in me.polygons])
        lo = mathutils.Vector([min(v[k] for v in vs) for k in range(3)])
        hi = mathutils.Vector([max(v[k] for v in vs) for k in range(3)])
        c, R = (lo + hi) / 2, (hi - lo).length / 2
        cover = 0.004 * max(hi - lo)    # 5 cm on a 12.5 m module, in whatever units Meshy used
        eps = 1e-5 * max(hi - lo)
        front, back = {}, {}
        step = 2 * R / res
        for d in dirs:
            q = (-d).to_track_quat("-Z", "Y")
            right = q @ mathutils.Vector((1, 0, 0)); up = q @ mathutils.Vector((0, 1, 0))
            base = c + d * R * 3
            for iy in range(res):
                row = base + up * ((iy + 0.5) * step - R)
                for ix in range(res):
                    hit = bvh.ray_cast(row + right * ((ix + 0.5) * step - R), -d, R * 6)
                    if hit[2] is None:
                        continue
                    tally = front if hit[1].dot(d) > 0 else back
                    tally[hit[2]] = tally.get(hit[2], 0) + 1
        dele, flip = [], []
        for idx, b in back.items():
            p = me.polygons[idx]
            n = (mw.to_3x3() @ p.normal).normalized()
            ctr = mw @ p.center
            pts = [ctr] + [(ctr * 2 + vs[k]) / 3 for k in p.vertices]
            if all(bvh.ray_cast(q + n * eps, n, cover)[2] is not None for q in pts):
                dele.append(idx)
            elif b > front.get(idx, 0):
                flip.append(idx)
        if not dele and not flip:
            break
        bm = bmesh.new(); bm.from_mesh(me); bm.faces.ensure_lookup_table()
        if flip:
            bmesh.ops.reverse_faces(bm, faces=[bm.faces[k] for k in flip])
        if dele:
            bmesh.ops.delete(bm, geom=[bm.faces[k] for k in dele], context="FACES")
        bm.to_mesh(me); bm.free(); me.update()
        total_del += len(dele); total_flip += len(flip)
    if total_del or total_flip:
        report["notes"].append("seen from behind: deleted %d inner-skin faces, flipped %d" % (total_del, total_flip))

def smooth_normals(o, angle_deg):
    """Give every corner the area-weighted average of the faces that meet at its
    POSITION and face within `angle_deg` of its own face.

    Meshy's corner normals are its face normals (plattgate, largest 2% of faces:
    86% of their area within 2 degrees), and its big facade triangles are not
    coplanar (median 3.7, max 34 degrees). Each triangle then lights as its own
    flat panel, and a low sun turns that into sharp light/dark wedges. The flat
    normal map and --smoothing OFF both left those per-face normals in place and
    changed nothing in game (2026-10-24).

    Keyed by position, not topology, because the LOD0 is unwelded (16% of edges
    shared, against 99% for PebbleGas). The angle limit keeps the two skins of a
    thin wall apart (they face opposite ways), which is what flipped normals when
    a Data Transfer was tried instead.
    """
    if angle_deg <= 0:
        return
    me = o.data
    lim = math.cos(math.radians(angle_deg))
    fn = [p.normal.copy() for p in me.polygons]
    fa = [p.area for p in me.polygons]
    key = lambda co: (round(co.x, 4), round(co.y, 4), round(co.z, 4))
    at = {}
    for p in me.polygons:
        for vi in p.vertices:
            at.setdefault(key(me.vertices[vi].co), []).append(p.index)
    loop_n = [None] * len(me.loops)
    for p in me.polygons:
        for li in p.loop_indices:
            acc = mathutils.Vector()
            for f in at[key(me.vertices[me.loops[li].vertex_index].co)]:
                if fn[f].dot(fn[p.index]) >= lim:
                    acc += fn[f] * fa[f]
            loop_n[li] = acc.normalized() if acc.length > 0 else fn[p.index]
    for p in me.polygons:
        p.use_smooth = True
    me.normals_split_custom_set(loop_n)

for o in meshes:
    apply_transforms(o)

def bbox(o):
    cs = [o.matrix_world @ mathutils.Vector(c) for c in o.bound_box]
    xs = [c.x for c in cs]; ys = [c.y for c in cs]; zs = [c.z for c in cs]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

def looks_like_lod_chain(objs):
    """Two very different things arrive as 'several meshes in one file'.

    A photogrammetry scan ships a LOD chain: the same object repeated at
    falling density, so every mesh occupies essentially the same box. A building
    ships PARTS: walls, roof, railings, each with its own box and its own
    material. Treating parts as LODs hides all but one of them, which is how a
    log cabin would arrive in the map as a single plank.

    Overlapping boxes plus falling poly counts means LODs.
    """
    if len(objs) < 2:
        return False
    boxes = [bbox(o) for o in objs]
    ref = boxes[0]
    rd = [max(ref[i + 3] - ref[i], 1e-6) for i in range(3)]
    for b in boxes[1:]:
        for i in range(3):
            # every axis has to line up closely with the biggest mesh's box
            if abs(b[i] - ref[i]) > 0.25 * rd[i] or abs(b[i + 3] - ref[i + 3]) > 0.25 * rd[i]:
                return False
    return True

mode = argv[6]
if mode == "auto":
    mode = "lods" if looks_like_lod_chain(meshes) else "parts"
report["mode"] = mode

bpy.ops.object.select_all(action="DESELECT")
if mode == "lods":
    # Keep the densest and drop the library's own chain; it is regenerated below
    # so the step ratios are ours and every level is inside the engine budget.
    base_obj = meshes[0]
    for o in meshes[1:]:
        bpy.data.objects.remove(o, do_unlink=True)
    report["notes"].append("LOD chain detected: kept the densest of %d, regenerating LODs"
                           % len(meshes))
else:
    # 2.2.1: "The fewer number of visual meshes a model contains - the better.
    # In most cases ... it's better to use a single visual mesh." Joining keeps
    # every material slot, so a 24-material cabin stays a 24-material cabin.
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.join()
    base_obj = bpy.context.view_layer.objects.active
    report["notes"].append("joined %d parts into one visual mesh" % len(meshes))

# An EMPTY material slot crashes the converter outright:
#
#   Assertion failed  ds_vector_new.h:314  Condition: IsValidIdx(index)
#
# forest_cabin joins 51 objects into 21 slots and slot 14 holds nothing, while
# polygons still carry material_index 14. The mesh XML can only list materials
# that exist, so the engine builds a 20-entry vector, the mesh asks for index 20,
# and it asserts. Filling the hole keeps the indices aligned and gives the
# orphaned geometry a defined surface instead of dropping it.
filler = None
for slot in base_obj.material_slots:
    if slot.material is None:
        if filler is None:
            filler = bpy.data.materials.new(base + "_default")
            filler.use_nodes = True
        slot.material = filler
        report["notes"].append("empty material slot filled with %s" % filler.name)

# FBX ALREADY HAS A ROOT CALLED `RootNode`. DO NOT BUILD ONE.
#
# 2026-09-29, after three identical crashes: the guide's "one root node, with
# RootNode under it" was implemented literally, as two empties named <base> and
# `RootNode`. FBX's implicit scene root is ALSO called RootNode, so the real
# tree became
#
#     RootNode(implicit) -> bob_lazar_ufo -> RootNode(ours) -> mesh, lods, cdt
#
# and `cdt` was a great-grandchild. `<Body>` with no ModelFrame resolves against
# the implicit RootNode and looks at its DIRECT children, so it never saw a cdt:
# "Can't create body for frame 'RootNode': no cdt" -> NULL -> 0xC0000005 WRITE
# at 0x0 in ExpeditionsEditor.exe+0xC45261. Three times, byte-identical, while
# the collider was renamed cdt_<base> -> cdt_mesh -> cdt. The name was never the
# problem; the depth was.
#
# Comparing CONVERTED resource against CONVERTED resource (not FBX against
# converted, which is not a valid comparison) shows the shipped shape:
#     Cube_3m_01               3 nodes: RootNode / cdt / Cube_3m_mesh
#     ancient_building_part_06 5 nodes: RootNode / cdt / mesh / lod1 / lod2
#     ours                    11 nodes, incl. leftover Sketchfab_model, Ufo_low
#
# So: everything sits at TOP LEVEL (parent None), and the glTF's own scaffolding
# is deleted -- Blender exports every object in the scene, not just the ones we
# reparented, which is how those import empties kept reaching the FBX.
# Snapshot the NAMES first. Holding object references across removals raises
# "ReferenceError: StructRNA of type Object has been removed" as soon as one
# delete invalidates a pointer still sitting in the list.
_keep = base_obj.name
for _name in [o.name for o in bpy.data.objects]:
    if _name == _keep:
        continue
    _o = bpy.data.objects.get(_name)
    if _o is not None:
        bpy.data.objects.remove(_o, do_unlink=True)
rootnode = None

def bake_normal_map(low, high, out_dir, stem):
    """Bake a REAL normal map from the pre-decimation geometry onto the
    decimated mesh.

    ⛔ A FLAT NORMAL IS NOT A HARMLESS PLACEHOLDER. Measured 2026-10-19:
    every model from this tool that renders correctly in game ships a real
    baked normal -- Gimbo's scanner has 3,351 distinct values -- and both
    Doc Windsor builds shipped the one-value flat stand-in and never rendered
    right, the second one visibly faceted. Meshy supplies colour only, so the
    stand-in fired on every model it ever produced.

    Baking here, between decimation and export, is the only point where the
    high-poly source and the final UVs both exist, so the map is guaranteed to
    match the mesh that actually ships.
    """
    try:
        os.makedirs(out_dir, exist_ok=True)
        img = bpy.data.images.new("baked_normal", 2048, 2048, alpha=False,
                                  float_buffer=False)
        img.colorspace_settings.name = "Non-Color"
        # The bake needs an active image node in EVERY material. They are removed
        # again below: left behind, the unwired node was read as the material's
        # ALBEDO by the map scan, and every colour-only material shipped the
        # baked normal (128,128,255) as its colour -- the lavender backs on all
        # 256 signs (owner, 2026-10-29).
        _bake_nodes = []
        for mat in low.data.materials:
            if mat is None or not mat.use_nodes:
                continue
            node = mat.node_tree.nodes.new("ShaderNodeTexImage")
            node.image = img
            node.select = True
            mat.node_tree.nodes.active = node
            _bake_nodes.append((mat, node))

        sc = bpy.context.scene
        sc.render.engine = "CYCLES"
        sc.cycles.samples = 1
        sc.cycles.use_denoising = False
        sc.render.bake.use_selected_to_active = True
        # A separate full-detail model (--high) is a different surface, so the
        # cage must stay tight: at a fixed 0.08 with unlimited reach, rays from
        # one container started inside the next and hit its far side. Chuck-It
        # shipped 30% backward normals on its own islands, seen in game as
        # triangles all over the paint; 0.2% of the model's size cut it to 1.2%
        # (2026-10-27). Tighter is better all the way down: Meg's power shack
        # measured 7.7 / 1.6 / 0.4 / 0.07% backward at cages of 0.4 / 0.2 / 0.1
        # / 0.05% of its size. check_normal_map.py fails a build that does this.
        if HIGH:
            _dim = max(high.dimensions)
            sc.render.bake.cage_extrusion = _dim * 0.0005
            sc.render.bake.max_ray_distance = _dim * 0.002
        else:
            sc.render.bake.cage_extrusion = 0.08
            sc.render.bake.max_ray_distance = 0.0
        sc.render.bake.margin = 8
        sc.render.bake.normal_space = "TANGENT"

        bpy.ops.object.select_all(action="DESELECT")
        high.select_set(True)
        low.select_set(True)
        bpy.context.view_layer.objects.active = low
        bpy.ops.object.bake(type="NORMAL")

        # ⛔ THE ENGINE READS DIRECTX NORMALS, BLENDER BAKES OPENGL: FLIP GREEN.
        # Measured 2026-10-28 with nconv (sign of corr(dR/drow, dG/dcol)): all
        # 12 normal maps in Saber's own Media\Mod Templates\mod_scout score
        # +0.27..+0.73; a Blender bake of a known bump scores -0.87. Unflipped,
        # the game lit every --high bake's detail from the wrong side: Chuck-It
        # showed smeared streaks and lumpy door ends in game while every
        # Blender render looked clean; flipping green in Blender reproduced it.
        import numpy as np
        _px = np.array(img.pixels[:], dtype=np.float32)
        _px[1::4] = 1.0 - _px[1::4]
        img.pixels[:] = _px

        # save_render runs the image through the view transform, and Blender 4+
        # defaults to AgX -- that tone-mapped every texture this tool wrote
        # until it was pinned. A normal map must never be tone-mapped.
        sc.view_settings.view_transform = "Standard"
        st = sc.render.image_settings
        st.file_format = "TARGA"; st.color_mode = "RGB"; st.color_depth = "8"
        fn = "%s_baked__n_d.tga" % stem
        img.save_render(filepath=os.path.join(out_dir, fn))

        for _mat, _node in _bake_nodes:
            _mat.node_tree.nodes.remove(_node)
        px = list(img.pixels)[:4000]
        distinct = len({tuple(px[i:i + 3]) for i in range(0, len(px) - 3, 4)})
        report["notes"].append(
            "baked a real normal map from the high-poly source "
            "(%d distinct values sampled)" % distinct)
        return fn if distinct > 1 else None
    except Exception as e:
        report["notes"].append("normal bake failed, falling back to flat: %s" % e)
        return None


before = (tri_count(base_obj), len(base_obj.data.vertices))

# keep the undecimated geometry so a real normal can be baked off it
if HIGH:
    _before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=HIGH)
    _new = [o for o in bpy.data.objects if o not in _before and o.type == "MESH"]
    for o in [o for o in bpy.data.objects if o not in _before and o.type != "MESH"]:
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.object.select_all(action="DESELECT")
    for o in _new:
        o.select_set(True)
    bpy.context.view_layer.objects.active = _new[0]
    if len(_new) > 1:
        bpy.ops.object.join()
    _hi = bpy.context.view_layer.objects.active
    report["notes"].append("normal map source: the full-detail model %s (%d triangles)"
                           % (os.path.basename(HIGH), sum(len(p.vertices) - 2 for p in _hi.data.polygons)))
else:
    _hi = base_obj.copy()
    _hi.data = base_obj.data.copy()
    bpy.context.scene.collection.objects.link(_hi)
_hi.name = "__highpoly__"
bpy.ops.object.select_all(action="DESELECT")
base_obj.select_set(True)
bpy.context.view_layer.objects.active = base_obj

decimate(base_obj, budget)
fix_backfacing(base_obj)                  # LODs are copied from base_obj below
smooth_normals(base_obj, SMOOTH_ANGLE)   # before the bake, so the bake matches them

BAKED_NORMAL = bake_normal_map(base_obj, _hi, out_tex, base)
bpy.data.objects.remove(_hi, do_unlink=True)

after = (tri_count(base_obj), len(base_obj.data.vertices))
if after != before:
    report["decimated"].append({"was": before, "now": after})

# The engine prefixes "mesh_" to the OBJECT name when it reports a mesh: object
# `namaqualand_boulder_02_LOD0` came back as `mesh_namaqualand_boulder_02_LOD0`,
# and the guide's sample tree shows a node `mesh` whose geometry displays as
# `mesh_mesh`. So MeshName="mesh" resolves against an object named `mesh`.
base_obj.name = MESH_NAME
base_obj.data.name = MESH_NAME
base_obj.parent = rootnode
kept = [base_obj]
report["meshes"].append({"name": MESH_NAME, "tris": after[0], "verts": after[1]})

# Regenerate the LOD chain ourselves at fixed ratios.
for i, ratio in enumerate(lod_ratios, start=1):
    dup = base_obj.copy()
    dup.data = base_obj.data.copy()
    bpy.context.scene.collection.objects.link(dup)
    dup.name = "%s_lod%d" % (MESH_NAME, i)
    dup.data.name = "%s_lod%d" % (MESH_NAME, i)
    dup.parent = rootnode
    m = dup.modifiers.new("dec", "DECIMATE")
    m.decimate_type = "COLLAPSE"
    m.ratio = ratio
    bpy.context.view_layer.objects.active = dup
    bpy.ops.object.modifier_apply(modifier=m.name)
    clean_mesh(dup)
    smooth_normals(dup, SMOOTH_ANGLE)
    kept.append(dup)
    report["meshes"].append({"name": dup.name, "tris": tri_count(dup),
                             "verts": len(dup.data.vertices)})

# Collision mesh. 2.2.1 requires at least one, named with the `cdt_` prefix, and
# recommends "primitive shapes (boxes, cylinders, etc.) that contain as few
# triangles as possible".
#
# A convex hull is the closer fit and works for a single-material scan, but it
# makes ResourceConverter assert on a multi-material model. Measured on
# forest_cabin, 21 materials, everything else held constant:
#
#     hull as cdt_<base> under RootNode   ASSERT
#     hull as cdt_mesh under a `cdt` node ASSERT
#     hull as an object named `cdt`       ASSERT
#     12-triangle box                     ok
#
# So `auto` tries the hull and falls back to the box when the converter rejects
# it, which is decided by running the converter, not by guessing.
#
# THE OBJECT IS NAMED `cdt`. ITS MESH DATA IS NAMED `cdt_mesh`. THEY ARE NOT
# THE SAME NAME AND THE DIFFERENCE IS THE WHOLE BUG.
#
# 2026-09-28: the converter passed bob_lazar_ufo clean, twice, and the EDITOR
# died on place both times with a NULL-pointer WRITE (0xC0000005 at 0x0,
# ExpeditionsEditor.exe+0xC45261), logged one second after
#
#     XML,ERROR: Model|Loading 'TerrainModel models_bob_lazar_ufo_Media'.
#     Can't create body for frame 'RootNode': no cdt. (Body)
#
# Body creation fails, returns NULL, the engine writes through it. (The same
# message from arizona_stalagmite_06 is harmless because it fails a CHILD frame,
# 'Bone.003'; failing the ROOT body is what crashes.)
#
# Node strings in file order prove the layout. Cube_3m_01:
#     RootNode / cdt / cdt_mesh / Cube_3m_mesh / mesh_Cube_3m_mesh
# ancient_building_part_06_dlc_05:
#     RootNode / cdt / cdt_mesh / mesh / mesh_mesh
# So a frame `X` carries a mesh `mesh_X`, and the collider is the exception:
# frame `cdt`, mesh `cdt_mesh`. `Body` with no ModelFrame resolves against
# RootNode and looks for a CHILD FRAME literally named `cdt`.
#
# Naming the OBJECT `cdt_<base>` or `cdt_mesh` both fail identically: the frame
# is then called something the engine never looks for. Counting `cdt_mesh`
# across shared.pak and concluding "it is in all 922" measured the mesh-data
# name, not the frame, and produced exactly one wasted fix.
src_obj = kept[0]
cdt_mesh = bpy.data.meshes.new("cdt_mesh")
cdt = bpy.data.objects.new("cdt", cdt_mesh)
bpy.context.scene.collection.objects.link(cdt)
bm = bmesh.new()
if collider == "box":
    bb = [mathutils.Vector(c) for c in src_obj.bound_box]
    lo = mathutils.Vector((min(v.x for v in bb), min(v.y for v in bb), min(v.z for v in bb)))
    hi = mathutils.Vector((max(v.x for v in bb), max(v.y for v in bb), max(v.z for v in bb)))
    if GIVEN_BOX is not None:
        lo, hi = GIVEN_BOX
        report["notes"].append("collider: the glTF's own collision_* box")
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x = lo.x + (v.co.x + 0.5) * (hi.x - lo.x)
        v.co.y = lo.y + (v.co.y + 0.5) * (hi.y - lo.y)
        v.co.z = lo.z + (v.co.z + 0.5) * (hi.z - lo.z)
else:
    # Build the hull from POINTS ONLY, welded, and keep only its shell.
    # LOD0 is no longer welded (its seams carry the source's normals). Built the
    # old way -- the whole mesh loaded, hull faces added beside the originals --
    # that soup crashed the converter (`IsValidIdx(index)`, ds_vector_new.h:314,
    # measured 2026-10-24; a box collider on the same mesh converted clean), and
    # once welded it still shipped 5,693 triangles because the original faces
    # stayed in the collider and disconnected triangles cannot collapse.
    for v in src_obj.data.vertices:
        bm.verts.new(v.co)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
    try:
        bmesh.ops.convex_hull(bm, input=bm.verts[:], use_existing_faces=False)
    except Exception as e:
        report["notes"].append("convex hull failed: %s" % e)
    # interior points are left behind as loose vertices; a loose vertex in the
    # collider is exactly what the converter indexes off the end of
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
bm.to_mesh(cdt_mesh)
bm.free()
# len(loop_triangles) reads 0 straight after bmesh.to_mesh, so the cap never fired
# and hulls were shipping at thousands of triangles. polygons is populated.
if collider != "box" and len(cdt_mesh.polygons) > 255:
    m = cdt.modifiers.new("dec", "DECIMATE")
    m.decimate_type = "COLLAPSE"
    m.ratio = max(0.01, 255 / len(cdt_mesh.polygons))
    bpy.context.view_layer.objects.active = cdt
    bpy.ops.object.modifier_apply(modifier=m.name)
cdt.parent = rootnode
if collider == "none":
    bpy.data.objects.remove(cdt, do_unlink=True)
    report["cdt"] = None
    report["notes"].append("no collision mesh built")
else:
    # Applying a modifier swaps the object's data, so the original cdt_mesh
    # handle is stale and reporting from it shows the pre-decimation count.
    cdt.data.calc_loop_triangles()
    report["cdt"] = {"name": cdt.name, "tris": len(cdt.data.loop_triangles),
                     "kind": collider}

# Textures, PER MATERIAL. A scan has one material; a cabin has 24, each with its
# own maps, and collapsing them to one set paints the whole building in roof
# shingles. glTF packs metallic/roughness/occlusion into one image, which is the
# nearest thing it has to the engine's "shading" map.
os.makedirs(out_tex, exist_ok=True)

def pot_le(v, cap):
    """Largest power of two <= v, capped. 2.2.3 wants 2^N x 2^M, max 2048."""
    p = 1
    while p * 2 <= min(v, cap):
        p *= 2
    return max(p, 1)

def slugify(s):
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_").lower() or "mat"

def alpha_used(mat, img):
    """True when this image drives the material's Alpha input, i.e. a cutout."""
    if not mat.use_nodes:
        return False
    for n in mat.node_tree.nodes:
        if n.type != "TEX_IMAGE" or n.image != img:
            continue
        for out in n.outputs:
            if out.name != "Alpha":
                continue
            for link in out.links:
                if link.to_socket.name == "Alpha":
                    return True
    return bool(getattr(mat, "blend_method", "OPAQUE") not in ("OPAQUE", None)
                and img.depth == 32)

suf = {"albedo": "__d", "normal": "__n_d", "shading": "__sh_d"}
saved = {}   # blender image name -> written filename, so shared maps write once
# Only materials actually used by the exported meshes matter; the join leaves
# unused slots behind and writing their textures would be dead weight.
used = []
for o in kept:
    for s in o.material_slots:
        if s.material and s.material not in used:
            used.append(s.material)

# 2.1.1's naming rule applies to material names too, and the XML has to carry the
# EXACT name from the FBX (2.2.4). Renaming the datablock before export keeps both
# sides identical while dropping the spaces and dots the library ships, e.g.
# "Pine Wood Texture.003" -> "pine_wood_texture_003".
taken = set()
for mat in used:
    nm = slugify(mat.name)
    cand, i = nm, 2
    while cand in taken:
        cand, i = "%s_%d" % (nm, i), i + 1
    taken.add(cand)
    if mat.name != cand:
        report["notes"].append("material '%s' -> '%s'" % (mat.name, cand))
    mat.name = cand

for mat in used:
    entry = {"name": mat.name, "maps": {}}
    if mat.use_nodes:
        roles = {}
        for n in mat.node_tree.nodes:
            if n.type != "TEX_IMAGE" or not n.image:
                continue
            # an image wired to nothing is not one of this material's maps (the
            # bake's temporary node was read as albedo this way; see bake_normal_map)
            if not any(o.links for o in n.outputs):
                continue
            # 🚨 FOLLOW THE LINK THROUGH INTERMEDIATE NODES.
            # Looking only one hop silently DROPPED every shading map imported
            # from glTF: Blender's glTF importer wires a metallicRoughness
            # texture through a SEPARATE_COLOR node, so the image's only direct
            # link lands on "Color" and never on "Roughness". The map then fell
            # through to "albedo", lost the setdefault race to the real albedo,
            # and vanished -- with no warning, and a model that installed with
            # two textures instead of three. Found 2026-10-08 on the Wingate
            # cliff blocks, where __sh_d carries the authored cavity occlusion.
            # ⛔ NO `nonlocal` HERE. This whole block is executed as a MODULE
            # inside Blender, not inside a function, so `nonlocal role` is a
            # SyntaxError -- and the failure surfaces only as
            # "!! Blender produced no report", which reads like a Blender or
            # asset problem rather than a syntax error in this file.
            # Iterative walk, no closure needed.
            role = "albedo"
            PASS_THROUGH = ("SEPARATE_COLOR", "SEPRGB", "REROUTE",
                            "MIX", "MATH", "GAMMA", "CURVE_RGB")
            frontier = list(n.outputs[0].links)
            seen_nodes = set()
            hops = 0
            while frontier and hops < 6 and role == "albedo":
                hops += 1
                nxt = []
                for link in frontier:
                    to_node, to_sock = link.to_node, link.to_socket.name
                    if to_node.type == "NORMAL_MAP" or "Normal" in to_sock:
                        role = "normal"
                        break
                    if to_sock in ("Metallic", "Roughness", "Specular"):
                        role = "shading"
                        break
                    if (to_node.type in PASS_THROUGH
                            and id(to_node) not in seen_nodes):
                        seen_nodes.add(id(to_node))
                        for out in to_node.outputs:
                            nxt.extend(out.links)
                frontier = nxt
            roles.setdefault(role, n.image)
        for role, img in roles.items():
            key = (img.name, role)
            if key in saved:
                entry["maps"][role] = saved[key]
                continue
            w, h = img.size
            if w == 0 or h == 0:
                continue
            tw, th = pot_le(w, tex_max), pot_le(h, tex_max)
            if (tw, th) != (w, h):
                img.scale(tw, th)
                report["notes"].append("%s %s resized %dx%d -> %dx%d"
                                       % (mat.name, role, w, h, tw, th))
            # A single-channel source saves as an 8-bit greyscale TGA and the
            # converter cannot build a DDS from it:
            #   "Unable to load 'textures/models/..__n_d.tga'"
            # save_render forces the scene's colour mode, so every file leaves
            # here as 24-bit RGB or 32-bit RGBA regardless of the source.
            suffix = suf[role]
            mode = "RGB"
            if role == "albedo" and alpha_used(mat, img):
                # 2.2.3: diffuse WITH alpha is __d_a, not __d. Foliage cutouts
                # need this or the leaf cards render as opaque rectangles.
                suffix, mode = "__d_a", "RGBA"
            fn = "%s_%s%s.tga" % (base, slugify(mat.name), suffix)
            try:
                st = bpy.context.scene.render.image_settings
                st.file_format = "TARGA"
                st.color_mode = mode
                st.color_depth = "8"
                img.save_render(filepath=os.path.join(out_tex, fn))
                saved[key] = fn
                entry["maps"][role] = fn
            except Exception as e:
                report["notes"].append("save failed %s: %s" % (fn, e))
    # A COLOUR-ONLY material (a glTF baseColorFactor, no image) still needs an
    # albedo: the engine reads colour from AlbedoMap only. Write its own colour,
    # converted linear -> sRGB, at tex_max so it matches the size the normal and
    # shading stand-ins are given below. The sign backs are this case: plain grey
    # galvanised metal (0.42, 0.43, 0.44 linear).
    if mat.use_nodes and not entry["maps"].get("albedo"):
        _bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if _bsdf is not None and not _bsdf.inputs["Base Color"].links:
            _lin = list(_bsdf.inputs["Base Color"].default_value)[:3]
            _srgb = [(12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055) for c in _lin]
            _sz = min(512, tex_max)
            _solid = bpy.data.images.new("solid_" + mat.name, _sz, _sz, alpha=False)
            # a byte image stores what it is given; save() writes those bytes with
            # no view transform (save_render would encode them a second time)
            _solid.pixels = [v for _ in range(_sz * _sz) for v in (_srgb[0], _srgb[1], _srgb[2], 1.0)]
            fn = "%s_%s__d.tga" % (base, slugify(mat.name))
            _solid.filepath_raw = os.path.join(out_tex, fn)
            _solid.file_format = "TARGA_RAW"
            _solid.save()
            entry["maps"]["albedo"] = fn
            report["notes"].append("%s: colour-only material, wrote a solid albedo %s"
                                   % (mat.name, [round(v * 255) for v in _srgb]))
    # A material with an albedo but no normal map makes the converter assert.
    # mossy_boulder_rock ships colour only and dies; every asset that converted
    # had both. A flat normal is the standard stand-in and costs 4x4 pixels.
    if entry["maps"].get("albedo") and not entry["maps"].get("normal") \
            and BAKED_NORMAL:
        # a real baked normal beats the flat stand-in, which is what shipped on
        # every Doc Windsor build and is why the second one rendered faceted
        entry["maps"]["normal"] = BAKED_NORMAL
    elif entry["maps"].get("albedo") and not entry["maps"].get("normal"):
        fn = "%s_flat__n_d.tga" % base
        if "__FLAT__" not in saved:
            flat = bpy.data.images.new("flat_normal", 4, 4, alpha=False)
            flat.pixels = [c for _ in range(16) for c in (0.5, 0.5, 1.0, 1.0)]
            st = bpy.context.scene.render.image_settings
            st.file_format = "TARGA"; st.color_mode = "RGB"; st.color_depth = "8"
            flat.save_render(filepath=os.path.join(out_tex, fn))
            saved["__FLAT__"] = fn
            report["notes"].append("generated a flat normal map for materials without one")
        entry["maps"]["normal"] = saved["__FLAT__"]
    # A material with no SHADING map renders FLAT RED in game. Measured
    # 2026-10-17 on Doc Windsor EMT: his jeep and trailer declared albedo +
    # normal only, every declared .pct resolved, and both models still came up
    # red. Control: 14 of 14 vanilla `models_fob_module_*` meshes in shared.pak
    # declare ShadingMap -- 4 carry exactly [Albedo, Normal, Shading], so three
    # maps is the FLOOR, not a nicety. glTF packs occlusion/roughness/metalness
    # into one image and Meshy often supplies none, so the role is simply absent
    # and the attribute was never written.
    #
    # The stand-in is not invented. PebbleGas is the one asset confirmed
    # rendering correctly in game, and its own __sh_d measures UNIFORM
    # R 0, G 204, B 255 across all 2048x2048 pixels -- already a flat map. The
    # same constant at 4x4 is therefore a measured copy of a working asset.
    # (The --fix-shading note elsewhere in this file says PebbleGas "still
    # carries B 0.0"; that file now measures B 255, so the note is stale.)
    if entry["maps"].get("albedo") and not entry["maps"].get("shading"):
        fn = "%s_flat__sh_d.tga" % base
        if "__FLATSH__" not in saved:
            fsh = bpy.data.images.new("flat_shading", 4, 4, alpha=False)
            fsh.pixels = [c for _ in range(16) for c in (0.0, 0.8, 1.0, 1.0)]
            st = bpy.context.scene.render.image_settings
            st.file_format = "TARGA"; st.color_mode = "RGB"; st.color_depth = "8"
            fsh.save_render(filepath=os.path.join(out_tex, fn))
            saved["__FLATSH__"] = fn
            report["notes"].append("generated a flat shading map (0,204,255) for "
                                   "materials without one; without it the surface "
                                   "renders flat red")
        entry["maps"]["shading"] = saved["__FLATSH__"]
    report["materials"].append(entry)

# ---------------------------------------------------------------- orientation
#
# ROTATE THE MESH DATA OURSELVES AND EXPORT WITH NO AXIS CONVERSION.
#
# Blender is Z-up, the engine is Y-up (its own data says so: `TwistAxisLocal=
# "(0; 1; 0)"` is a tree trunk's long axis and the plant brands carry
# `LocalYOcclusionOffset`). Letting the exporter convert looks right and is not:
# it encodes the conversion as `Lcl Rotation = (-90, 0, 0)` on every node and
# leaves the vertex arrays Z-up. The converter NORMALISES frame transforms --
# it announces this on every run, `Frame 'plant' has non-identity scale,
# resetting...` -- so the rotation is discarded and the Z-up vertices are read
# as Y-up. Every asset arrives rotated 90 degrees about X.
#
# `bake_space_transform=True` fixes the rotation and silently bakes Blender's
# metre-to-centimetre unit conversion as well, exporting a 1 m plant 101 units
# tall. Doing it here keeps both under control: one rotation, no unit scaling,
# identity node transforms, which is what guide 2.2.4 asks for anyway.
# NOTHING TO ROTATE HERE. See the comment above: the converter performs the
# Z-up -> Y-up conversion itself on import, so it wants Blender's own Z-up
# vertex data with identity node transforms. Rotating here produced a plant
# lying on its side, confirmed from the engine's own billboard bake.

if WANT_H > 0:
    # Measure LOD0 (`mesh`) only: the decimated LODs and the hull overshoot it, and
    # sizing on them shrank Doc Windsor's base to 11.79 m against --length 12.5.
    _ref = [o for o in bpy.data.objects if o.type == "MESH" and o.name == "mesh"] \
        or [o for o in bpy.data.objects if o.type == "MESH"]
    _mv = [o.matrix_world @ v.co for o in _ref for v in o.data.vertices]
    if _mv:
        if WANT_AXIS == "x":
            _h = max(v.x for v in _mv) - min(v.x for v in _mv)
        elif WANT_AXIS == "y":
            _h = max(v.y for v in _mv) - min(v.y for v in _mv)
        else:
            _h = max(v.z for v in _mv) - min(v.z for v in _mv)
        if _h > 1e-9:
            _k = WANT_H / _h
            for _o in bpy.data.objects:
                if _o.type == "MESH":
                    _o.data.transform(mathutils.Matrix.Scale(_k, 4))
                    _o.data.update()
            report["notes"].append("scaled %s %.4g -> %.4g m"
                                   % (WANT_AXIS, _h, WANT_H))

if YAW:
    # Every mesh, collider included, so physics stays aligned with what is seen.
    # A rigid turn leaves the tangent-space normal map valid.
    _r = mathutils.Matrix.Rotation(math.radians(YAW), 4, "Z")
    for _o in bpy.data.objects:
        if _o.type == "MESH":
            _o.data.transform(_r)
            _o.data.update()
    report["notes"].append("turned %g degrees about the vertical axis" % YAW)

# GROUND THE MODEL. Its base must sit at Z=0 or the engine buries it.
#
# A generator centres its output on the origin. Measured on the gas station:
# after scaling to 5.5 m the mesh spanned Z -2.71 .. +2.79, so 2.71 m -- 49% of
# its height -- was below the origin. The engine places the origin on the
# terrain, so the model came up buried to the waist. "Buried in the terrain
# about halfway", exactly.
#
# This affects EVERY model this tool has ever produced; it scaled but never
# translated. The collider moves with it, so physics stays aligned.
# ⛔ READ REAL VERTICES, NOT bound_box. `o.bound_box` is CACHED and is NOT
# refreshed by `o.data.transform()`, so after the scale above it still reports
# the pre-scale box. Grounding from it moved this model 0.41 m when it needed
# 2.71 m, and the result measured Z -2.30 .. +3.20 instead of 0 .. 5.5 --
# still 42% buried, while the log line claimed success.
#
# 🚨 GROUND ON THE VISIBLE MESH, NOT THE COLLIDER. The hull is decimated to 255
# faces with COLLAPSE, which moves vertices off the surface. Measured 2026-10-24
# on Doc Windsor's base: cdt spanned 0 .. 6.02 m while the visible mesh spanned
# 1.33 .. 5.49 m, so grounding on everything floated the building 1.33 m up.
# The decimated LODs overshoot the same way (mesh_lod* reached 0.57 m below LOD0
# on the same model), so the reference is LOD0 alone: the node named `mesh`.
_all = [o for o in bpy.data.objects if o.type == "MESH"]
_vis = ([o for o in _all if o.name == "mesh"]
        or [o for o in _all if o.name != "cdt" and not o.name.startswith("mesh_lod")]
        or _all)
if _all and not KEEP_ORIGIN:
    _zmin = min((o.matrix_world @ v.co).z for o in _vis for v in o.data.vertices)
    if abs(_zmin) > 1e-6:
        for _o in _all:
            _o.data.transform(mathutils.Matrix.Translation((0.0, 0.0, -_zmin)))
            _o.data.update()
        report["notes"].append("grounded: base was %.3f m from the origin" % _zmin)
    if SINK > 0:
        for _o in _all:
            _o.data.transform(mathutils.Matrix.Translation((0.0, 0.0, -SINK)))
            _o.data.update()
        report["notes"].append("sunk %.2f m into the terrain" % SINK)
    # and keep the collider and the LODs inside LOD0's box, so a decimated node
    # cannot stand proud of (or hang below) what the player sees
    if _vis is not _all:
        _pts = [o.matrix_world @ v.co for o in _vis for v in o.data.vertices]
        _lo = [min(p[i] for p in _pts) for i in range(3)]
        _hi = [max(p[i] for p in _pts) for i in range(3)]
        for _o in [o for o in _all if o not in _vis]:
            _inv = _o.matrix_world.inverted()
            _moved = 0
            for v in _o.data.vertices:
                w = _o.matrix_world @ v.co
                c = mathutils.Vector([min(max(w[i], _lo[i]), _hi[i]) for i in range(3)])
                if (c - w).length > 1e-6:
                    v.co = _inv @ c; _moved += 1
            _o.data.update()
            if _moved:
                report["notes"].append("%s clamped to LOD0's bounds (%d vertices)" % (_o.name, _moved))

# ⛔ DO NOT RE-ADD SMOOTH SHADING HERE WITHOUT MEASURING FIRST.
#
# A 30-degree auto-smooth pass was added here 2026-10-17 and REVERTED the same
# hour. Two reasons, both measured:
#   1. The hypothesis was wrong. Forcing FLAT shading offline produced something
#      that LOOKED like the in-game fault, but the emitted FBX was already 100%
#      smooth hours earlier (PebbleGas control 85.7%). A LOOKALIKE IS NOT A
#      REPRODUCTION.
#   2. It BROKE conversion. Auto-smooth marks edges over the angle sharp, and a
#      sharp edge SPLITS vertices. The trailer went to 102,445 vertices against
#      the engine's 65,534 ceiling and the converter refused the file:
#        "Number of vertices (102445) or faces (56999) is exceeded limit (65534)"
#      Faces were unchanged at 56,999 -- it was purely normal-seam splitting.
# ⇒ Any smoothing change must be checked against the VERTEX count, not the face
#   budget, because the budget only counts faces.

bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.fbx(
    filepath=out_fbx, use_selection=True, path_mode="STRIP",
    embed_textures=False, bake_anim=False, add_leaf_bones=False,
    # Blender's own axes: ask the exporter for NO conversion, because the mesh
    # data was rotated into Y-up above. Any conversion here would be a second
    # one, and would come back as a node rotation the converter then discards.
    axis_forward="Y", axis_up="Z", apply_unit_scale=False, global_scale=1.0,
    object_types={"MESH", "EMPTY"}, mesh_smooth_type=SMOOTHING,
)
print("BLENDER_REPORT " + json.dumps(report))
'''


def slug(s: str) -> str:
    """Latin, digits and underscore only, lowercase -- 2.1.1."""
    out = "".join(c if (c.isalnum() or c == "_") else "_" for c in s).strip("_")
    while "__" in out:
        out = out.replace("__", "_")
    return out.lower()


def run_converter(base: str, folder: str = "models") -> tuple[int, str]:
    """Ask ResourceConverter whether the installed mesh is acceptable.

    The invocation is not obvious and getting it wrong produces errors that
    point at the asset instead of at the harness -- `saber.convert` owns it and
    documents all three traps. This used to write the output to a temp
    directory, which silently broke the converter's own resource mount.
    """
    r = convert.convert(base, folder)
    return r["code"] if r["size"] else (r["code"] or -1), r["log"]


# Lines ResourceConverter prints because it is running outside the editor's
# full environment, not because anything is wrong with the model.
#
# `PHYSFS_mount(`, `_templates/models.xml` and `Template 'DefaultNarrow'` USED
# TO BE LISTED HERE AND ARE NOT NOISE. They mean the output path or the working
# directory is wrong, and filtering them is why models reported a clean --check
# and then failed inside the editor.
NOISE = convert.NOISE

# The log of the most recent converter run, so main() can act on what the
# converter measured instead of on what Blender measured.
LAST_CONVERTER_LOG = ""

over_budget = convert.over_budget


def main():
    # `--help` CRASHED outright on a cp1252 console, and had before this line
    # was added: the description IS the module docstring, the docstring carries
    # ⛔ and 🚨, and argparse writes help straight to stdout. Nobody could read
    # the options for the tool. Pre-existing, found 2026-10-11.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("glb")
    ap.add_argument("--name", default=None, help="base name; default from the file")
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                    help=f"max faces and vertices per mesh (hard engine limit {HARD_LIMIT})")
    ap.add_argument("--tex-max", type=int, default=2048)
    ap.add_argument("--mode", default="auto", choices=["auto", "lods", "parts"],
                    help="auto decides by bounding box: overlapping boxes at falling "
                         "density are a scan's LOD chain, separate boxes are the parts "
                         "of a building and get joined into one visual mesh")
    ap.add_argument("--lods", default="0.5,0.25,0.12",
                    help="decimation ratios for the generated LOD chain")
    ap.add_argument("--distances", default=None)
    ap.add_argument("--hide", type=float, default=550.0)
    ap.add_argument("--collisions", default="Dynamic",
                    choices=["Static", "Dynamic", "None"],
                    help="2.3.2.1 rule 2: most models, static included, want Dynamic")
    ap.add_argument("--two-sided", action="store_true",
                    help="emit TwoSided=\"true\" on every material. For a "
                         "photogrammetry shell, which has zero thickness: with "
                         "backface culling you see straight through it from one "
                         "side. 618 shipped models use this, including thin "
                         "geometry like airdrop_big_objective. NOT applied "
                         "automatically: openness does not predict the need for "
                         "it. bob_lazar_ufo has 28.2%% boundary edges against "
                         "ufo_crashste_brno's 4.3%% and renders correctly "
                         "without it, so any auto-rule keyed on that is wrong.")
    ap.add_argument("--no-instancing", action="store_true",
                    help="2.3.1.3 case 1: a model used once on the map, e.g. a unique building")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="run ResourceConverter over the installed files")
    ap.add_argument("--height", type=float, default=0.0,
                    help="scale the model so it is this many metres tall. "
                         "Imported models arrive at arbitrary scale and a "
                         "brush multiplies rather than normalises.")
    ap.add_argument("--length", type=float, default=0.0,
                    help="scale by LENGTH (X) instead of height. Use this for "
                         "anything with a roof rack, mast or awning: "
                         "normalising height lets the accessory eat the budget "
                         "and the body comes out small. Doc Windsor's jeep was "
                         "built with --height 2.1 and landed 2.86 m long "
                         "against a working module's 4.95 m.")
    ap.add_argument("--smoothing", choices=["FACE", "EDGE", "OFF"], default="FACE",
                    help="FBX smoothing data written beside the normals (default FACE)")
    ap.add_argument("--high", type=Path, default=None,
                    help="the full-detail model to bake the normal map from (use with a "
                         "low-poly made by rebake_from_hd.py)")
    ap.add_argument("--smooth-angle", type=float, default=0.0,
                    help="average corner normals over faces within this many degrees "
                         "at the same position. Meshy normals follow each triangle, "
                         "and non-coplanar facades shade as light/dark wedges; 40 "
                         "smooths them and keeps 90-degree corners hard. 0 = off")
    ap.add_argument("--sink", type=float, default=0.0,
                    help="metres to set the model into the terrain after grounding. "
                         "Vanilla FOB modules extend 0.19-0.63 m below zero; 0.3 "
                         "keeps uneven ground from showing a gap.")
    ap.add_argument("--keep-converted", action="store_true",
                    help="leave --check's converted mesh/textures in Media\\prebuild "
                         "(for repacking a FOB pak; the editor shows these white)")
    ap.add_argument("--keep-origin", action="store_true",
                    help="do not ground the model: keep its authored height above "
                         "the origin. For one part of a kit, e.g. a sign plate "
                         "that sits at mounting height over a separate post.")
    ap.add_argument("--yaw", type=float, default=0.0,
                    help="degrees to turn the model about the vertical axis after "
                         "--length scales it; positive is counter-clockwise seen "
                         "from above. Use it to face a module's working side at "
                         "its interaction zone.")
    ap.add_argument("--mesh-name", default="mesh",
                    help="name of the visual mesh object and of MeshName in the "
                         "XML. Plants must use 'plant' -- the converter looks "
                         "for the billboard's source LOD in an object of that "
                         "name and fails the whole model if it is absent.")
    ap.add_argument("--need-preload", action="store_true",
                    help="add ClipCamera and NeedPreload to the ModelBrand. "
                         "REQUIRED for any model a FOB module uses.")
    # ON by default since 2026-10-11, when the owner's headlight test confirmed
    # an empty blue channel IS the black render: the model showed its artwork
    # under a headlight and was ink black in ambient light, and filling blue
    # fixed it in game at all hours.
    # ⛔ The old default was OFF, reasoned from "the one model confirmed
    # rendering in game has an empty blue channel". That model was never
    # rendering correctly -- both sightings behind that claim were taken under
    # direct light, the only condition that masks this defect.
    ap.add_argument("--no-fix-shading", dest="fix_shading",
                    action="store_false", default=True,
                    help="do NOT fill an empty blue channel in a __sh_d map. "
                         "Filling is ON by default: 0 of 500 shipped __sh_d "
                         "maps have an empty blue channel, and an empty one "
                         "renders the model black except under direct light "
                         "(confirmed in game 2026-10-11). See "
                         "saber/shading_map.py.")
    ap.add_argument("--folder", default="models",
                    choices=["models", "landmarks"],
                    help="landmarks for a mesh a ModelBrand names through "
                         "<Landmark Mesh=...>; models for one resolved "
                         "implicitly by matching the class name")
    ap.add_argument("--collider", default="auto",
                    choices=["auto", "hull", "box", "none"],
                    help="auto walks hull -> box -> none, keeping the first the "
                         "converter accepts (requires --check)")
    args = ap.parse_args()
    # The back-face gate compares 8 views 45 degrees apart, and the diagonal views
    # sit higher than the side views, so only quarter turns map view onto view.
    if args.yaw % 90:
        ap.error("--yaw must be a multiple of 90 degrees")
    # Fail loudly rather than silently preferring one. Both normalise the same
    # uniform scale against different axes, so passing both is a real mistake
    # about intended size, not a harmless redundancy.
    if args.height and args.length:
        ap.error("--height and --length both set; pick one axis to normalise")
    set_folder(args.folder)

    if args.budget > HARD_LIMIT:
        print(f"  !! budget {args.budget} exceeds the engine limit {HARD_LIMIT}")
        return 2
    src = Path(args.glb).resolve()
    if not src.is_file():
        print(f"  !! {src} not found")
        return 2
    if not BLENDER.is_file():
        print(f"  !! Blender not at {BLENDER}")
        return 2

    base = slug(args.name or src.stem)
    print(f"{src.name}  ->  {base}")
    lic = src.with_suffix(".json")
    if lic.is_file():
        d = json.loads(lic.read_text(encoding="utf8"))
        print(f"   licence {d.get('license')}  author {d.get('author')}")
        if d.get("license") != "cc_zero":
            print("   !! NOT public domain. Check the terms before shipping this.")

    # hull -> box only. "none" was tried and never rescued an asset that both
    # colliders failed, and it would leave a model with no collision anyway, so
    # the third attempt was pure cost on a model that is going to be skipped.
    order = ["hull", "box"] if args.collider == "auto" else [args.collider]
    if args.collider == "auto" and not args.check:
        order = ["hull"]
    last = 2
    for i, collider in enumerate(order):
        if i:
            print(f"\n=> converter rejected the {order[i-1]} collider; "
                  f"rebuilding with a {collider}")
        last = attempt(args, src, base, collider)
        if last == BACKFACE_FAIL:
            # Not a collider or budget problem: another attempt would only rebuild
            # the same faces. Left installed so the renders can be compared;
            # install_model_to_mod.py refuses it.
            print("\n=> REFUSED: the build shows the player faces from behind. "
                  "Do not ship it.")
            return last
        # Three reductions is enough: each one is measured off the converter's
        # own figure rather than guessed, so it converges instead of creeping.
        for _ in range(3):
            if last == 0:
                break
            tighter = over_budget(LAST_CONVERTER_LOG, args.budget)
            if tighter is None or tighter >= args.budget:
                break
            print(f"\n=> the FBX exceeded the engine's 65,534 ceiling even "
                  f"though Blender's counts fitted (vertex splitting at UV and "
                  f"normal seams). Rebuilding at budget {tighter}.")
            args.budget = tighter
            last = attempt(args, src, base, collider)
        if last == 0:
            return 0
    # A rejected model must not be left in Media. The editor re-runs the converter
    # over everything on every launch, so a model that cannot convert becomes a
    # permanent block of errors in the log -- the exact noise we are trying to
    # reduce -- and it is not usable anyway.
    if args.apply and args.check:
        removed = uninstall(base)
        if removed:
            print(f"   removed {removed} installed file(s): a model the converter "
                  f"rejects would spam the editor log on every launch")
    return last


def texture_refs(mesh_xml: Path) -> set[str]:
    if not mesh_xml.is_file():
        return set()
    t = mesh_xml.read_text(encoding="utf8", errors="replace")
    return set(re.findall(r'Map="models/([^"]+)"', t))


def uninstall(base: str) -> int:
    """Remove one model's files.

    Textures are resolved from the model's own mesh XML, NOT by globbing the base
    name. A prefix glob is wrong whenever one model's name is a prefix of
    another's: uninstalling the failed `pine_tree` matched `pine_tree_double_*`,
    `pine_tree_monterey_*` and `pine_tree_sylvestris_*` and stripped the textures
    off three models that had converted perfectly well.

    A texture still referenced by another installed model is left alone, because
    identical source images are deduplicated across models.
    """
    mine = texture_refs(MESHES / f"{base}.xml")
    others: set[str] = set()
    for x in MESHES.glob("*.xml"):
        if x.stem != base:
            others |= texture_refs(x)

    n = 0
    for p in (MESHES / f"{base}.fbx", MESHES / f"{base}.xml", CLASSES / f"{base}.xml"):
        if p.is_file():
            p.unlink()
            n += 1
    for fn in sorted(mine - others):
        p = TEXTURES / fn
        if p.is_file():
            p.unlink()
            n += 1
    return n


BACKFACE_FAIL = 3


def attempt(args, src: Path, base: str, collider: str) -> int:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        script = td / "conv.py"
        script.write_text(BLENDER_SCRIPT, encoding="utf8")
        out_fbx = td / f"{base}.fbx"
        out_tex = td / "tex"
        r = subprocess.run(
            [str(BLENDER), "--background", "--factory-startup",
             "--python", str(script), "--",
             str(src), str(out_fbx), str(out_tex), base,
             str(args.budget), str(args.tex_max), args.mode, args.lods, collider,
             args.mesh_name, str(args.length or args.height or 0),
             "x" if args.length else "z", str(args.sink), args.smoothing,
             str(args.smooth_angle), str(args.high or ""),
             "1" if args.keep_origin else "0", str(args.yaw)],
            capture_output=True, text=True, timeout=1800)
        line = next((l for l in r.stdout.splitlines()
                     if l.startswith("BLENDER_REPORT ")), None)
        if not line:
            print("  !! Blender produced no report")
            print(r.stdout[-1500:])
            print(r.stderr[-800:])
            return 2
        rep = json.loads(line[len("BLENDER_REPORT "):])
        if rep.get("error"):
            print(f"  !! {rep['error']}")
            return 2

        over = [m for m in rep["meshes"] if max(m["tris"], m["verts"]) > HARD_LIMIT]
        print(f"\n   mode: {rep.get('mode')}")
        print(f"   {len(rep['meshes'])} visual mesh(es), budget {args.budget}:")
        for m in rep["meshes"]:
            flag = "  OVER LIMIT" if max(m["tris"], m["verts"]) > HARD_LIMIT else ""
            print(f"      {m['name']:<22}{m['tris']:>9,} tris {m['verts']:>9,} verts{flag}")
        for d in rep["decimated"]:
            print(f"      decimated {d['was'][0]:,} tris -> {d['now'][0]:,}")
        if rep.get("cdt"):
            print(f"      {rep['cdt']['name']:<22}{rep['cdt']['tris']:>9,} tris  "
                  f"(collision, {rep['cdt'].get('kind')})")
        print(f"   {len(rep['materials'])} material(s):")
        for m in rep["materials"][:12]:
            maps = ", ".join(f"{k}" for k in m["maps"]) or "NO TEXTURES"
            print(f"      {m['name'][:34]:<34} {maps}")
        if len(rep["materials"]) > 12:
            print(f"      ... and {len(rep['materials']) - 12} more")
        for n in rep["notes"][:12]:
            print(f"   note: {n}")
        if len(rep["notes"]) > 12:
            print(f"   ... and {len(rep['notes']) - 12} more notes")
        if over:
            print(f"  !! {len(over)} mesh(es) still over the {HARD_LIMIT} limit")
            return 2
        if not out_fbx.is_file():
            print("  !! no FBX produced")
            return 2
        print(f"\n   {base}.fbx  {out_fbx.stat().st_size/1e6:.1f} MB")

        nlod = max(1, len(rep["meshes"]))
        if args.distances:
            dist = args.distances
        else:
            # "Number of these distances = Number of LODs of the geometry - 1."
            # With a single mesh that means NO distances; supplying one gets
            # "Incorrect LOD distances setup. LOD count: 0, distances count: 1".
            steps = [round(args.hide * (i + 1) / (nlod + 1) / 5) * 5
                     for i in range(nlod - 1)]
            dist = ", ".join(str(int(s)) for s in steps)

        # 2.2.4: Name must be the EXACT material name from the FBX, and each
        # material carries its own maps.
        mats = []
        for m in rep["materials"]:
            # ⚠ I REMOVED `_template="DefaultNarrow"` HERE AND PUT IT BACK.
            #
            # The argument for removing it: across 600 shipped `models_` meshes,
            # 1,162 Material tags carry a `_template` exactly twice.
            #
            # THAT COMPARISON WAS INVALID. Shipped XML is embedded inside a
            # CONVERTED mesh -- it is the converter's OUTPUT, where a template
            # has already been expanded and inlined. Comparing our SOURCE xml to
            # their OUTPUT xml says nothing about what source should contain.
            #
            # What a valid comparison looks like: the two custom models already
            # installed here, built before this tool changed, both carry it:
            #     wingate_wall_a   _template="DefaultNarrow"
            #     desert_cliff_6   _template="DefaultNarrow"
            #
            # Removing it did NOT fix the white model, so it was never the
            # cause either way. Restored so a new asset matches the ones already
            # on disk rather than differing from everything.
            attrs = ['_template="DefaultNarrow"', f'Name="{m["name"]}"']
            for role, key in (("albedo", "AlbedoMap"), ("normal", "NormalMap"),
                              ("shading", "ShadingMap")):
                fn = m["maps"].get(role)
                if fn:
                    attrs.append(f'{key}="models/{fn}"')
            if args.two_sided:
                attrs.append('TwoSided="true"')
            mats.append("\t<Material " + " ".join(attrs) + " />")
        # Restored for the same reason as the _template above: the shipped
        # meshes that lack this line are CONVERTER OUTPUT, and both custom
        # models already installed here (wingate_wall_a, desert_cliff_6) carry
        # it in their source.
        mesh_xml = ('<_templates Include="models" />\n'
                    '<CombineXMesh Type="Model">\n'
                    f'\t<MeshLod Distances="{dist}" '
                    f'HidingDistance="{args.hide:g}" MeshName="{args.mesh_name}" />\n'
                    + "\n".join(mats) + "\n</CombineXMesh>\n")

        # 2.3.1.1: static models that repeat get both attributes; a one-off building
        # gets neither.
        inst = "" if args.no_instancing else ' Instanced="true" InstancedAsModel="true"'
        # NeedPreload is MANDATORY for a model used by a FOB module, per
        # docs/custom_gameplay_entities__fob_modules__3d_models_of_fob_modules.md:
        # "XML classes of all used custom models should contain the special
        # NeedPreload attribute in the <ModelBrand> tag and this attribute should
        # be equal to true. This is necessary for the system to be able to
        # preload these models before starting the level."
        # It was being added by hand after every build and lost on the next
        # --apply, three times.
        pre = ' ClipCamera="true" NeedPreload="true"' if args.need_preload else ""
        # DynamicModel is declared by 25 of 25 vanilla FOB module classes in
        # initial.pak [media]/classes/models/fob_module_*.xml (15 false, 12
        # true). This generator emitted it in NEITHER state, which left the one
        # structural difference between our class and the vanilla medium fuel
        # station -- the direct analogue -- besides <Landmark>.
        # ⚠ Saber documents the attribute NOWHERE: zero hits in docs/_ALL.md and
        # zero in meta_desc_ssl.ps, both with passing positive controls. So what
        # an ABSENT one defaults to is unknown, and "absent == false" is an
        # assumption, not a fact. We emit false because that is what the vanilla
        # medium fuel station sets and these models are static one-piece meshes.
        dyn = ' DynamicModel="false"'
        class_xml = (f'<ModelBrand{inst}{dyn}{pre}>\n'
                     '\t<PhysicsModel>\n'
                     f'\t\t<Body Collisions="{args.collisions}" />\n'
                     '\t</PhysicsModel>\n'
                     '</ModelBrand>\n')

        print(f"\n   {MESHES / (base + '.xml')}")
        print(mesh_xml)
        print(f"   {CLASSES / (base + '.xml')}")
        print(class_xml)

        if not args.apply:
            print("(dry run -- pass --apply)")
            return 0

        for d in (MESHES, CLASSES, TEXTURES):
            d.mkdir(parents=True, exist_ok=True)
        (MESHES / f"{base}.fbx").write_bytes(out_fbx.read_bytes())
        (MESHES / f"{base}.xml").write_text(mesh_xml, encoding="utf8")
        (CLASSES / f"{base}.xml").write_text(class_xml, encoding="utf8")
        for p in sorted(out_tex.glob("*.tga")):
            # ⛔ RE-ENCODE THROUGH THE ONE CORRECT WRITER. Blender's TARGA
            # exporter is NOT it.
            #
            # Measured against the game's own TGAs in its sample mod:
            #     game:  type  2 UNCOMPRESSED truecolour, descriptor 0x00
            #     ours:  type 10 RLE          truecolour, descriptor 0x20
            #
            # The CJ7 port cost two days and six failed fixes to a TGA writer,
            # after which eight duplicate writers were collapsed into
            # `vehicle_pak.write_tga`. That audit swept the VEHICLE tools; this
            # one writes through Blender and was never in it, so it has been
            # emitting a format the shipped data never uses.
            #
            # PIL honours the origin bit on read, so loading Blender's file
            # gives the intended image; write_tga then stores it the way the
            # engine's own files are stored -- uncompressed, bottom-up,
            # descriptor clear, with the 26-byte footer.
            # 🚨 AND REPORT AN EMPTY SHADING CHANNEL ON THE WAY THROUGH.
            #
            # glTF packs occlusion/roughness/metalness into one image and this
            # tool passes it through as __sh_d. Meshy fills only green, leaving
            # a __sh_d measuring R 0.0 G 204.0 B 0.0 -- and across 500 shipped
            # __sh_d textures sampled from the game's own editor.pak, ZERO have
            # an empty blue channel. (R 0.0 is ordinary: 291 of the 500 do. An
            # earlier note called R and B both anomalous; half of that was a
            # false lead.)
            #
            # ⛔ DO NOT "FIX" THIS BY DEFAULT, AND DO NOT CALL IT THE TEXTURE
            # FIX. Measured 2026-10-11: the PebbleGas station that is CONFIRMED
            # RENDERING CORRECTLY IN THE GAME still carries B 0.0. The blue
            # channel is therefore NOT what cured the black render, and a
            # silent rewrite would alter the one asset ever confirmed working
            # in exchange for a hypothesis the evidence already refutes.
            #
            # What survives is narrower and still worth saying: the channel
            # shape differs from every shipped asset, so it is worth a line of
            # output. --fix-shading fills it for anyone who wants to test the
            # difference deliberately.
            #
            # saber/shading_map.py carries the measurement; check_shading_map.py
            # --self-test carries the controls, including a dark-but-live
            # shipped map that must survive untouched.
            from PIL import Image as _Im
            import vehicle_pak as _vp
            from saber import shading_map as _sm
            with _Im.open(p) as _img:
                _img.load()
                if _sm.is_shading_map(p.name) and not p.stem.endswith("_flat__sh_d"):
                    # a passed-through glTF metallic-roughness image is in glTF
                    # channel order; the stamped stand-in is already in Saber's
                    _img, _note = _sm.from_gltf_orm(_img)
                    print(f"   .. {p.name}: {_note}")
                if _sm.is_shading_map(p.name):
                    _ok, _why = _sm.verdict(_img)
                    if not _ok and args.fix_shading:
                        _img, _notes = _sm.repair(_img)
                        for _n in _notes:
                            print(f"   !! {p.name}: {_n}")
                    elif not _ok:
                        for _w in _why:
                            print(f"   ⚠  {p.name}: {_w}")
                        print(f"   ⚠  left as-is. The one model confirmed "
                              f"rendering in game has this too; "
                              f"--fix-shading to fill it.")
                # 🚨 THE GENERATED STAND-INS MUST BE EXACT, NOT APPROXIMATE.
                #
                # A flat normal is RGB(128,128,255) BY DEFINITION -- it is the
                # unit +Z vector encoded. Anything else tilts and weakens every
                # surface normal on the model. The flat shading stand-in is a
                # measured copy of PebbleGas's own __sh_d, (0,204,255), the one
                # asset confirmed rendering correctly in game.
                #
                # Blender produced (133,144,203) and (111,177,200) for these,
                # because save_render applies the view transform. The transform
                # is forced to Standard above, but these two carry a FIXED,
                # KNOWN-CORRECT value, so there is no reason to let a colour
                # pipeline near them at all. Stamp them and verify.
                _const = None
                if p.stem.endswith("_flat__n_d"):
                    _const = (128, 128, 255)
                elif p.stem.endswith("_flat__sh_d"):
                    _const = (0, 204, 255)
                if _const is not None:
                    # 🚨 A STAND-IN MUST MATCH THE ALBEDO'S DIMENSIONS.
                    #
                    # These were generated 4x4 because a uniform colour needs no
                    # more pixels than that. Measured 2026-10-17: every material
                    # that renders correctly has ALL its maps at ONE size --
                    # PebbleGas is 2048x2048 across albedo, normal and shading,
                    # and vanilla `billboard_plants_arizona_bush_a` is 512x256
                    # across all three. Doc Windsor paired a 2048 albedo with
                    # 4x4 maps and was the only material rendering wrong.
                    #
                    # Cheap in pixels is not cheap if the engine samples a
                    # material's maps on one shared assumption.
                    _sib = sorted(
                        q for q in p.parent.glob("*__d.tga")
                        if "_flat__" not in q.name)
                    if _sib:
                        with _Im.open(_sib[0]) as _a:
                            _size = _a.size
                    else:
                        _size = _img.size
                    _img = _Im.new("RGB", _size, _const)
                _vp.write_tga(str(TEXTURES / p.name), _img,
                              alpha_from_image=p.stem.endswith("__d_a"))
                if _const is not None:
                    with _Im.open(str(TEXTURES / p.name)) as _chk:
                        _got = _chk.convert("RGB").getpixel((0, 0))
                    if _got != _const:
                        raise SystemExit(
                            f"{p.name}: stand-in wrote {_got}, wanted {_const}")
                    print(f"   {p.name}: stamped exact {_const}, verified")
            print(f"   textures/models/{p.name}  "
                  f"{(TEXTURES / p.name).stat().st_size/1e6:.1f} MB  "
                  f"(re-encoded uncompressed)")

    print(f"\ninstalled {base}")

    # ⛔ WARN ABOUT SIBLINGS. --apply ADDS an asset; it never removes one.
    #
    # Rebuilding the same model under a different --name leaves every earlier
    # attempt installed, and they are indistinguishable in the editor's asset
    # browser. Three builds of one gas station left three assets, two of them
    # from a pipeline that scrambled the UVs. He deleted his placements and
    # never saved, so he reasonably assumed nothing could persist -- but
    # deleting a PLACEMENT does not touch an ASSET, and he spent an afternoon
    # placing the two broken ones because the good one did not exist yet.
    #
    # This does not delete anything: another asset sharing a prefix may be
    # deliberate. It says what is there, which is all that was missing.
    stem = base.rsplit("_", 1)[0] if "_" in base else base
    siblings = sorted({p.stem for p in MESHES.glob(f"{stem}*.fbx")} - {base})
    if siblings:
        print(f"\n   ⚠ {len(siblings)} other installed asset(s) share this prefix "
              f"and will appear beside it in the asset browser:")
        for s in siblings:
            fbx = MESHES / f"{s}.fbx"
            when = time.strftime("%m-%d %H:%M", time.localtime(fbx.stat().st_mtime))
            print(f"       {s:44s} installed {when}")
        print("     They are indistinguishable by name alone. Remove the ones "
              "you do not want before placing anything.")

    if args.check:
        global CHECKED
        CHECKED = None if args.keep_converted else (base, FOLDER)
        code, out = run_converter(base, FOLDER)
        global LAST_CONVERTER_LOG
        LAST_CONVERTER_LOG = out
        real = [l for l in out.splitlines()
                if l.strip() and not any(n in l for n in NOISE)]
        print(f"\nResourceConverter --mod-convert: exit {code}")
        for l in real[:20]:
            print("   " + l)
        if code == 0 and not real:
            print("   clean")
        if code != 0:
            return 1

        # A clean converter run is NOT an acceptance test. It reports that the
        # files parse; it cannot see a hierarchy fault, because the fault lives
        # in the CONVERTED artifact this function throws away. bob_lazar_ufo
        # passed here three times and crashed the editor three times. So the
        # structural check runs too, and its verdict is the exit code.
        import verify_model
        ok, fails, notes = verify_model.verify(base)
        print("\nStructural check (verify_model.py):")
        for n in notes:
            print(f"   - {n}")
        if not ok:
            print("   FAIL")
            for f in fails:
                print(f"     !! {f}")
            return 1
        print("   PASS -- structure matches a shipped model")

        # ⛔ THE BACK-FACE GATE. Faces the player sees from behind were the
        # three-week "scrambled texture" (Doc Windsor, Platt's Gate Works), and
        # every check above PASSED those builds. This compares what the player
        # sees of our FBX against Meshy's original and fails if we made it worse.
        # install_model_to_mod.py refuses any model without a pass on record.
        import check_backfacing
        print("\nBack-face check (check_backfacing.py, 8 views):")
        ok, msg = check_backfacing.gate(src, MESHES / f"{base}.fbx",
                                        check_backfacing.REPORTS / base, args.yaw)
        print(f"   {'PASS' if ok else 'FAIL'}  {msg}")
        print(f"   red renders: {check_backfacing.REPORTS / base}")
        if not ok:
            return BACKFACE_FAIL

        # ⛔ THE NORMAL-MAP GATE. A normal map baked off a separate model can
        # point backward on its own surface; the back-face gate cannot see it,
        # since the geometry is fine. Chuck-It passed everything above at 30%.
        # Gate the maps the mesh XML NAMES, not `_baked__n_d.tga` by filename: a
        # material that brings its own normal (Meshy's remesh does) ships that one
        # and leaves the bake unused. The homestead cabins failed at 10-24% on the
        # unused bake while the shipped map measured 0.0% (2026-10-27).
        shipped = re.findall(r'NormalMap="([^"]+)"', (MESHES / f"{base}.xml").read_text(encoding="utf8"))
        if src.suffix.lower() == ".glb":
            import check_normal_map
            for rel in sorted(set(shipped)):
                nmap = TEXTURES.parent / rel
                if not nmap.exists() or nmap.name.endswith("_flat__n_d.tga"):
                    continue
                ok, msg = check_normal_map.gate(src, nmap)
                print(f"\nNormal-map check (check_normal_map.py, {nmap.name}):\n"
                      f"   {'PASS' if ok else 'FAIL'}  {msg}")
                if not ok:
                    return 1
        print("   Still unproven until it is seen in the game.")
        return 0

    print("Pass --check to run ResourceConverter AND the structural check "
          "before you trust it.")
    return 0


# (base, folder) of the model --check converted, so its outputs can be released
CHECKED = None


def release_to_editor(base: str, folder: str) -> int:
    """Delete what --check's converter run left in Media\prebuild.

    ⛔ The editor does not convert a model whose prebuild output already exists,
    and the output our converter run leaves there renders WHITE in the editor:
    measured 2026-10-28 on all eight Homestead cabins. With those files moved
    aside, the editor converted each cabin itself on the next load (a
    RebuildLog entry per cabin) and they rendered textured. The models that had
    always worked in the editor (bob_lazar_ufo, corrugated_culvert) were the ones
    the editor had converted itself. The check keeps its verdict; the editor
    builds the output it uses.
    """
    pre = MEDIA / "prebuild"
    gone = 0
    targets = [pre / "meshes" / f"{folder}_{base}"]
    for sub in ("dds", "pct", "nx"):
        targets += (pre / "textures" / sub).glob(f"{folder}_{base}_*")
    for t in targets:
        if t.is_file():
            t.unlink()
            gone += 1
    return gone


if __name__ == "__main__":
    code = main()
    if CHECKED:
        n = release_to_editor(*CHECKED)
        if n:
            print(f"   released {n} converted file(s) from Media\prebuild: the editor "
                  f"converts the model itself on first load (ours renders white)")
    sys.exit(code)

