"""verify_model.py -- acceptance test for a custom model, run against the
artifact the ENGINE reads.

WHY THIS EXISTS
---------------
`ResourceConverter --mod-convert` returning `exit 0, clean` means the files
parse. It does not mean the model works. On 2026-09-20 twenty-nine models passed
it and the editor crashed on place. On 2026-09-28 bob_lazar_ufo passed it three
times and took the editor down three times with

    XML,ERROR: Model|Loading 'TerrainModel models_bob_lazar_ufo_Media'.
    Can't create body for frame 'RootNode': no cdt. (Body)
    -> 0xC0000005 ACCESS_VIOLATION, WRITE at 0x0, ExpeditionsEditor.exe+0xC45261

The converter cannot see that fault because it is a HIERARCHY fault, and the old
`--check` converted into a TemporaryDirectory and threw the output away. This
keeps the output and inspects it.

THE FAULT IT WAS WRITTEN TO CATCH
---------------------------------
FBX already contains a root node called `RootNode`. Building another one nests
the whole model one level too deep, and Blender exports every object left in the
scene -- including the glTF importer's own `Sketchfab_model` / `Ufo_low`
scaffolding. The result:

    RootNode -> bob_lazar_ufo -> RootNode(ours) -> mesh, lods, cdt     11 nodes

`<Body>` with no `ModelFrame` resolves against the real RootNode and looks at its
DIRECT children. It never saw a cdt. Fixed shape:

    RootNode -> cdt, mesh, mesh_lod1..n                                 6 nodes

SCOPE OF THE NODE-COUNT RULE -- READ THIS BEFORE "FIXING" IT
------------------------------------------------------------
`nodes == 2 + visual_meshes` is a contract on OUR OWN generator's output, which
is deliberately flat. It is NOT a law of the engine. Measured across all 1,876
shipped model meshes in shared.pak, exact equality holds for only 361 (19.2%),
because Saber's models legitimately carry bones, sub-parts and multi-part
hierarchies. Do not "correct" this test by loosening it to match the library --
a custom prop coming out of glb_to_fbx.py has no bones and no reason to be deep.

USAGE
    python verify_model.py bob_lazar_ufo
    python verify_model.py bob_lazar_ufo --keep      # leave the converted file
    python verify_model.py --all                     # every installed model
"""
from __future__ import annotations

import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from saber import gamepaths

MEDIA = gamepaths.media_root("expeditions")
GAME = gamepaths.game_dir("expeditions")
RESOURCE_CONVERTER = GAME / "Sources" / "Bin" / "ResourceConverter.exe"
BIN_EDITOR = GAME / "Sources" / "BinEditor"

CEIL = 65534          # 16-bit index buffer, applies to BOTH faces and verts
TEX_MAX = 2048        # engine texture limit

# Names that mean an importer's scaffolding survived into the export.
SCAFFOLDING = re.compile(
    r"^(Sketchfab_model|GLTF_SceneRootNode|Scene|Ufo_low|.*_low|Empty(\.\d+)?)$", re.I)

ASCII = re.compile(rb"[ -~]{2,}")
NAMEISH = re.compile(r"[A-Za-z0-9_.\-]{2,}")

# ResourceConverter prints these because it runs outside the editor, not because
# the model is wrong. Same list as glb_to_fbx.py.
NOISE = (
    "ShaderCacheEditor.sdc",
    "_templates/models.xml",
    "Template 'DefaultNarrow'",
    "Cant open file 'textures/dds/",
    "Failed to load texture textures/dds/",
)


# ---------------------------------------------------------------- conversion

def convert(base: str, outdir: Path) -> tuple[int, str, Path]:
    """Run the real converter, KEEPING the output so it can be inspected."""
    if not RESOURCE_CONVERTER.is_file():
        return -1, f"ResourceConverter not at {RESOURCE_CONVERTER}", Path()
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / base
    r = subprocess.run(
        # -s or an assert opens a modal on the user's desktop and waits.
        [str(RESOURCE_CONVERTER), "--mod-convert", "-s",
         "-i", f"meshes/models/{base}", "-o", str(out), "-r", str(MEDIA)],
        capture_output=True, text=True, timeout=900, cwd=str(BIN_EDITOR))
    return r.returncode, (r.stdout or "") + (r.stderr or ""), out


def real_messages(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or any(n in line for n in NOISE):
            continue
        out.append(line)
    return out


# ------------------------------------------------------------------ parsing

def parse_converted(path: Path) -> dict:
    """A converted model is <u32 xml_len><xml><u32 magic><u32 node_count>..."""
    d = path.read_bytes()
    xlen = struct.unpack_from("<I", d, 0)[0]
    xml = d[4:4 + xlen].decode("utf8", "ignore")
    rest = d[4 + xlen:]
    magic, node_count = struct.unpack_from("<II", rest, 0)

    names, seen = [], set()
    for r in ASCII.findall(rest[:8000]):
        s = r.decode("ascii", "ignore")
        if NAMEISH.fullmatch(s) and s not in seen:
            seen.add(s)
            names.append(s)
    return {"xml": xml, "node_count": node_count, "names": names,
            "size": len(d)}


def tga_size(p: Path) -> tuple[int, int]:
    with open(p, "rb") as f:
        h = f.read(18)
    return struct.unpack_from("<HH", h, 12)


def is_pow2(v: int) -> bool:
    return v > 0 and (v & (v - 1)) == 0


# ------------------------------------------------------------------ checking

def verify(base: str, keep: bool = False) -> tuple[bool, list[str], list[str]]:
    """Return (ok, failures, notes)."""
    fails: list[str] = []
    notes: list[str] = []

    mesh_xml = MEDIA / "meshes" / "models" / f"{base}.xml"
    cls_xml = MEDIA / "classes" / "models" / f"{base}.xml"
    fbx = MEDIA / "meshes" / "models" / f"{base}.fbx"
    for p in (mesh_xml, cls_xml, fbx):
        if not p.is_file():
            fails.append(f"missing installed file: {p.name}")
    if fails:
        return False, fails, notes

    # --- textures referenced by the mesh XML must exist and fit the engine
    xml = mesh_xml.read_text(encoding="utf8", errors="ignore")
    refs = re.findall(r'\w*Map="models/([^"]+)"', xml)
    for ref in sorted(set(refs)):
        tga = MEDIA / "textures" / "models" / ref
        if not tga.is_file():
            fails.append(f"texture referenced but not installed: {ref}")
            continue
        try:
            w, h = tga_size(tga)
        except Exception as e:
            notes.append(f"could not read TGA header for {ref}: {e}")
            continue
        if w > TEX_MAX or h > TEX_MAX:
            fails.append(f"texture {ref} is {w}x{h}, over the {TEX_MAX} limit")
        elif not (is_pow2(w) and is_pow2(h)):
            fails.append(f"texture {ref} is {w}x{h}, not power-of-two")

    # --- every material must DECLARE the full map floor, not merely resolve
    # what it declares. This check was absent, and its absence is why Doc
    # Windsor EMT passed with "2 texture(s)" and then rendered FLAT RED in
    # game: both his materials declared albedo + normal and no ShadingMap.
    # Control (2026-10-17): 14 of 14 vanilla `models_fob_module_*` meshes in
    # shared.pak declare ShadingMap, 4 of them carrying exactly
    # [Albedo, Normal, Shading]. Three maps is the floor.
    for mat in re.findall(r"<Material\b[^>]*>", xml):
        name = (re.search(r'Name="([^"]*)"', mat) or [None, "?"])[1]
        declared = set(re.findall(r"(\w+Map)=", mat))
        if "AlbedoMap" not in declared:
            continue                      # nothing to shade; not this rule's business
        for required in ("NormalMap", "ShadingMap"):
            if required not in declared:
                fails.append(f"material '{name}' declares no {required}; a material "
                             f"with an albedo needs albedo+normal+shading or the "
                             f"surface renders flat red")

    # --- LOD distances count must be (LOD count - 1)
    m = re.search(r'MeshLod[^>]*Distances="([^"]*)"', xml)
    n_dist = len([x for x in m.group(1).split(",") if x.strip()]) if m else 0
    visual = n_dist + 1          # mesh + one per distance

    # --- convert for real and inspect what the engine will read
    tmp = Path(tempfile.mkdtemp(prefix="verify_model_"))
    code, text, out = convert(base, tmp)
    msgs = real_messages(text)
    if code != 0:
        fails.append(f"ResourceConverter exit {code}")
    for msg in msgs[:8]:
        # "...resetting..." means the converter corrected it itself. That is a
        # deviation worth knowing about, not a failure: bob_lazar_ufo places and
        # renders with non-identity scale on every frame, because the converter
        # normalises it. Failing on it would fail a model proven to work.
        # The guide (2.2.4) still wants identity scale/rotation at export, so
        # this stays visible rather than being filtered into NOISE.
        (notes if "resetting" in msg else fails).append(f"converter: {msg}")
    if not out.is_file():
        fails.append("converter produced no output file")
        return False, fails, notes

    info = parse_converted(out)
    names = info["names"]

    # --- the hierarchy checks that the converter cannot make
    if "cdt" not in names:
        fails.append("no node named `cdt` -- the engine will fail to create a "
                     "body for frame 'RootNode' and then write through NULL")
    elif "RootNode" in names and names.index("cdt") != names.index("RootNode") + 1:
        fails.append(f"`cdt` is not the first child of RootNode "
                     f"(RootNode at {names.index('RootNode')}, "
                     f"cdt at {names.index('cdt')}) -- it is nested too deep")

    left = [n for n in names if SCAFFOLDING.match(n)]
    if left:
        fails.append(f"importer scaffolding survived into the export: {left}")

    if base in names:
        fails.append(f"an object named `{base}` is in the export -- that is the "
                     f"wrapper empty; meshes must sit directly under RootNode")

    expected = 2 + visual        # RootNode + cdt + (mesh + lods)
    if info["node_count"] != expected:
        fails.append(f"node count {info['node_count']}, expected {expected} "
                     f"(RootNode + cdt + {visual} visual mesh(es))")
    notes.append(f"node count {info['node_count']}, "
                 f"{visual} visual mesh(es), {len(set(refs))} texture(s)")

    if keep:
        notes.append(f"converted artifact kept at {out}")
    else:
        try:
            out.unlink()
            tmp.rmdir()
        except OSError:
            pass

    return (not fails), fails, notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", nargs="?", help="installed model base name")
    ap.add_argument("--all", action="store_true", help="verify every installed model")
    ap.add_argument("--keep", action="store_true", help="keep the converted artifact")
    a = ap.parse_args()

    if a.all:
        d = MEDIA / "meshes" / "models"
        bases = sorted(p.stem for p in d.glob("*.fbx")) if d.is_dir() else []
    elif a.base:
        bases = [a.base]
    else:
        ap.error("give a model name or --all")

    if not bases:
        print("no installed models found")
        return 0

    bad = 0
    for b in bases:
        ok, fails, notes = verify(b, keep=a.keep)
        print(f"\n{b}")
        for n in notes:
            print(f"   - {n}")
        if ok:
            print("   PASS -- structure matches a shipped model")
        else:
            bad += 1
            print("   FAIL")
            for f in fails:
                print(f"     !! {f}")

    print(f"\n{len(bases) - bad}/{len(bases)} passed")
    print("A PASS is a structural check, NOT proof it places. The editor is the\n"
          "arbiter: open it, search the bare name, and place one.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
