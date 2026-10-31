"""check_normals.py -- do a model's shading normals survive the import?

Run INSIDE Blender. How far do the corner (loop) normals lean off their face normals?

On a flat panel the two triangles shade evenly only if their corner normals point
straight out. Normals averaged across a sharp edge lean, and the panel shades in
two diagonal tones. Reported for the largest faces, area-weighted.

    blender -b -P check_normals.py -- <model.glb|fbx> [...]

Compare the built FBX (Media\meshes\models\<brand>.fbx) against Meshy's GLB.
A healthy build matches the GLB (0-1% leaning >25 deg on the largest faces); the
weld-damaged builds before 2026-10-24 measured 36-60%, and looked sloppy in game.
"""
import bpy, sys, math
for path in sys.argv[sys.argv.index("--") + 1:]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    (bpy.ops.import_scene.fbx if path.lower().endswith(".fbx") else bpy.ops.import_scene.gltf)(filepath=path)
    objs = [o for o in bpy.data.objects if o.type == "MESH" and o.name not in ("cdt",) and not o.name.startswith("mesh_lod")]
    o = max(objs, key=lambda x: len(x.data.polygons)); me = o.data
    if hasattr(me, "calc_normals_split"): me.calc_normals_split()
    rows = []
    for p in me.polygons:
        fn = p.normal
        dev = max(math.degrees(fn.angle(me.loops[li].normal)) for li in p.loop_indices)
        rows.append((p.area, dev))
    rows.sort(reverse=True)
    big = rows[: max(1, len(rows) // 10)]          # largest 10% of faces
    tot = sum(a for a, _ in big)
    def share(th): return 100 * sum(a for a, d in big if d > th) / tot
    smooth = 100 * sum(1 for p in me.polygons if p.use_smooth) / len(me.polygons)
    print(f"NORMDEV {path.split('/')[-1]} [{o.name}] faces={len(me.polygons):,} verts={len(me.vertices):,} smooth-flag={smooth:.0f}%"
          f"  largest-10% faces: corner normal leans >10deg on {share(10):.0f}% of area, >25deg on {share(25):.0f}%, >45deg on {share(45):.0f}%, >90deg on {share(90):.0f}%")
