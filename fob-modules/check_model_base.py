"""check_model_base.py -- find (and optionally remove) the two base defects a
Meshy model brings with it, BEFORE it goes through glb_to_fbx.

WHY THIS EXISTS

Measured on Doc Windsor's FOB, 2026-10-24, both in Meshy's own output:

  * A DOUBLED FLOOR PLANE. The concrete slab under the canopy is stored twice,
    an up-facing and a down-facing copy at the same height. Coplanar faces
    z-fight and drew as black triangles on the ground in game.
  * HANGING SLIVERS. A handful of near-zero-area faces dangling below the real
    base. glb_to_fbx grounds on the lowest vertex, so a sliver 4% of the
    model's height down props the whole building up off the terrain.

Both are invisible in Meshy's viewer and in a Blender preview. Run this on
every GLB before importing it.

    python check_model_base.py <model.glb>                  # report only
    python check_model_base.py <model.glb> --fix <out.glb>  # remove both

The fix deletes only the floor band's flat faces and its thin rim, and faces
lying wholly below the main base. UVs and materials are untouched. Render the
result (preview_models.py) and look before building.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from saber import gamepaths

BLENDER = gamepaths.blender()

SCRIPT = r'''
import bpy, bmesh, sys, json, collections
a = sys.argv[sys.argv.index("--") + 1:]
src, out = a[0], (a[1] if len(a) > 1 and a[1] != "-" else None)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
o = next(x for x in bpy.data.objects if x.type == "MESH")
bm = bmesh.new(); bm.from_mesh(o.data)
zs = [v.co.z for v in bm.verts]; z0, z1 = min(zs), max(zs); h = z1 - z0
total = sum(f.calc_area() for f in bm.faces) or 1.0
rel = lambda z: (z - z0) / h

# bottom profile in 0.5%-of-height bands over the lowest 15%
bands = collections.defaultdict(lambda: {"up": 0.0, "down": 0.0, "side": 0.0, "n": 0})
for f in bm.faces:
    r = rel(f.calc_center_median().z)
    if r > 0.15: continue
    k = round(r * 200) / 200
    kind = "up" if f.normal.z > 0.9 else "down" if f.normal.z < -0.9 else "side"
    bands[k][kind] += f.calc_area(); bands[k]["n"] += 1

# main base: the lowest band holding real surface (>= 0.5% of the model's area)
base = next((k for k in sorted(bands) if sum(bands[k][t] for t in ("up","down","side")) / total >= 0.005), 0.0)
slivers = [f for f in bm.faces if rel(max(v.co.z for v in f.verts)) < base - 0.004]

# Slivers matter only when they lift the model visibly: PebbleGas carries a few
# that lift it 1% and renders fine.
if base < 0.02:
    slivers = []

# doubled floor: up- and down-facing faces at the SAME height AND the same spot.
# A container's bottom (down) and its interior floor (up) sit close but not on one
# plane, and must not count -- that false positive is measured, on Doc's cleaned base.
cell = 0.10 * max(h, 1e-9)
ups, downs = collections.defaultdict(float), collections.defaultdict(float)
for f in bm.faces:
    c = f.calc_center_median(); r = rel(c.z)
    if r > 0.15 or abs(f.normal.z) <= 0.9: continue
    key = (round(c.x / cell), round(c.y / cell), round(r * 500))   # 0.2%-of-height bins
    (ups if f.normal.z > 0 else downs)[key] += f.calc_area()
paired = collections.defaultdict(float)
for (x, y, zb), area in ups.items():
    if any((x, y, zb + d) in downs for d in (-1, 0, 1)):
        paired[zb] += area
# CANDIDATES ONLY. Measured: this finds Doc's real doubled slab, but on other
# models it also ranks shelves and container floors. A face is deleted only for
# a band the operator names with --floor after rendering it.
cands = sorted(((zb / 5.0, area / total * 100) for zb, area in paired.items()
                if area / total >= 0.003), key=lambda t: -t[1])[:3]

rep = {"height_units": h, "main_base_pct": base * 100,
       "slivers": len(slivers), "sliver_lift_pct": base * 100 if slivers else 0.0,
       "floor_candidates": cands}
kill = set(slivers)
floor_band = (float(a[2]) / 100, float(a[3]) / 100) if len(a) > 3 else None
if floor_band is not None:
    lo, hi = z0 + floor_band[0] * h, z0 + floor_band[1] * h
    for f in bm.faces:
        c = f.calc_center_median().z; fz = [v.co.z for v in f.verts]
        if (abs(f.normal.z) > 0.9 and lo <= c <= hi) or (abs(f.normal.z) <= 0.9 and lo <= min(fz) and max(fz) <= hi):
            kill.add(f)
    rep["floor_faces"] = len(kill) - len(slivers)
if out:
    bmesh.ops.delete(bm, geom=list(kill), context="FACES")
    bm.to_mesh(o.data); o.data.update()
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB")
    rep["removed_faces"] = len(kill); rep["wrote"] = out
print("BASE_REPORT " + json.dumps(rep))
'''


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("glb", type=Path)
    ap.add_argument("--fix", type=Path, metavar="OUT.glb",
                    help="write a copy with hanging slivers removed (and the --floor band)")
    ap.add_argument("--floor", type=float, nargs=2, metavar=("LO_PCT", "HI_PCT"),
                    help="also remove the doubled floor lying in this band, in %% of the "
                         "model's height above its lowest vertex (e.g. 4.0 5.25). Take it "
                         "from the candidates, render, and confirm it IS the floor first.")
    a = ap.parse_args()
    if a.floor and not a.fix:
        ap.error("--floor needs --fix OUT.glb")
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as fh:
        fh.write(SCRIPT)
    cmd = [str(BLENDER), "-b", "-P", fh.name, "--", str(a.glb), str(a.fix) if a.fix else "-"]
    if a.floor:
        cmd += [str(a.floor[0]), str(a.floor[1])]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    line = next((l for l in r.stdout.splitlines() if l.startswith("BASE_REPORT ")), None)
    if not line:
        print(r.stdout[-2000:], r.stderr[-2000:]); return 2
    rep = json.loads(line.split(" ", 1)[1])
    print(f"  {a.glb.name}")
    print(f"    main base starts {rep['main_base_pct']:.1f}% of height above the lowest vertex")
    print(f"    hanging slivers: {rep['slivers']}"
          + (f"  -> would float the model {rep['sliver_lift_pct']:.1f}% of its height" if rep['slivers'] else ""))
    cands = rep["floor_candidates"]
    if cands:
        print("    doubled-plane CANDIDATES (up+down faces on one plane; a slab z-fights black):")
        for pct, share in cands:
            print(f"      ~{pct:.1f}% of height   {share:.2f}% of surface"
                  f"   -> try --floor {max(pct - 0.6, 0):.1f} {pct + 1.2:.1f}")
        print("      ⛔ candidates, not verdicts: render the model and confirm the band IS a floor")
    else:
        print("    doubled-plane candidates: none")
    if a.fix:
        if rep.get("floor_faces") is not None:
            print(f"    floor band removed: {rep['floor_faces']} faces")
        print(f"    removed {rep['removed_faces']} faces in all -> {rep['wrote']}")
        print("    ⛔ render it (preview_models.py) and LOOK before building")
    return 1 if (rep["slivers"] or cands) and not a.fix else 0


if __name__ == "__main__":
    raise SystemExit(main())
