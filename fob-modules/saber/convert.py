"""Run the editor's own `ResourceConverter` the way the editor runs it.

Only this converter's verdict counts. A mesh the editor cannot convert does not
appear in the asset browser, and clicking its entry takes the editor down.

WHY THIS MODULE EXISTS: THE OBVIOUS INVOCATION IS WRONG IN THREE WAYS

Calling `ResourceConverter --mod-convert -i <mesh> -o <somewhere> -r <Media>`
looks complete and is not. Each of the three mistakes below produces an error
that points somewhere else entirely, and all three were diagnosed as asset
defects before the harness was examined.

1. THE OUTPUT PATH IS LOAD-BEARING.
   The converter mounts `<output>/../../prebuild/` as a resource root. Write the
   output to a temp directory and that mount fails:

       System| PHYSFS_mount('<tmp>/../../prebuild/') failed! Reason: File not found.
       IO| Cant open file 'textures/dds/plants_x_material_0__d_a.dds'

   Nothing is wrong with the textures. The output has to land where the editor
   puts it -- `<Media>/prebuild/meshes/<folder>_<base>` -- or every resource the
   mesh references becomes unreachable.

2. TEMPLATES NEED THE WORKING DIRECTORY **AND** THE FILES IN THE MEDIA TREE.
   Run from anywhere but `BinEditor` and `_templates/models.xml` is not found, so
   `_template="DefaultNarrow"` does not resolve. That is a real failure, not
   noise -- it was previously filtered out as noise, which is how models that
   "passed" the check still behaved differently in the editor.

   ⚠ THE WORKING DIRECTORY WAS NEVER THE WHOLE CAUSE, AND BELIEVING IT WAS LEFT
   THIS BROKEN FOR MONTHS. `convert()` has always run with cwd=BinEditor, and
   every model STILL reported the template missing. Measured on this install:

       plants/bamboo          template missing: no
       models/bob_lazar_ufo   template missing: YES
       models/desert_cliff_6  template missing: YES

   The Media tree simply had no `_templates` directory. `DefaultNarrow` lives in
   `[media]/_templates/models.xml` inside `initial.pak` and had never been
   extracted, so no working directory could have reached it. `ensure_templates()`
   now extracts the four vanilla files on demand.

   It hid because PLANTS NEVER REQUEST A TEMPLATE, so the only assets that could
   expose it were models -- which made it look like a models problem rather than
   a mount problem.

3. PLANTS CANNOT CONVERT IN ONE PASS.
   The converter BAKES a plant's billboard into `<Media>/textures/billboard/`
   and LOADS it from `<Media>/prebuild/billboard/`. Nothing moves it between the
   two, so the first pass always ends:

       IO| Cant open file 'billboard/plants_x__d_a.dds'
       Terrain| Cant create plant billboard

   The pass that reports this is the pass that produced the bake. Stage the
   baked files into the prebuild location and convert again and it succeeds.
   `convert()` does this automatically for plants.
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

from . import gamepaths

# The engine's 16-bit index buffer. Checked against BOTH counts, per mesh.
HARD_LIMIT = 65534
DEFAULT_BUDGET = 60000          # margin under the ceiling

# "Number of vertices (77486) or faces (57084) is exceeded limit (65534)"
OVER_LIMIT = re.compile(
    r"Number of vertices \((\d+)\) or faces \((\d+)\) is exceeded limit \((\d+)\)")


def over_budget(log: str, budget: int = DEFAULT_BUDGET) -> int | None:
    """A budget that would have fitted, read off the converter's own numbers.

    BLENDER'S VERTEX COUNT IS NOT THE FBX'S VERTEX COUNT. The exporter splits a
    vertex wherever UVs or normals disagree across an edge, so a mesh Blender
    calls 41,559 verts can reach 77,486 in the file -- over the ceiling while
    every count printed during the build looked comfortable. The split factor
    depends on the seams of the individual asset and cannot be guessed, so this
    scales the budget by how far over the converter says the file actually is.

    Returns None when the log carries no over-limit complaint.
    """
    m = OVER_LIMIT.search(log)
    if not m:
        return None
    verts, faces, limit = (int(g) for g in m.groups())
    worst = max(verts, faces)
    if worst <= limit:
        return None
    return max(5000, int(budget * limit / worst * 0.9))

# The three maps the converter bakes per plant billboard: albedo+alpha, normal,
# and the occlusion/depth pair. All three are staged; the mesh asks for the
# albedo first, so a partial stage fails on the next map instead.
BILLBOARD_SUFFIXES = ("__d_a", "__n_d", "__zo_d")

# Printed because the converter is running outside the editor's full environment
# and not because anything is wrong with the asset.
NOISE = ("ShaderCacheEditor.sdc", "Could not load effects")


def ensure_templates(media: Path, game: str | None = None) -> list[str]:
    """Put the vanilla `_templates/*.xml` in the Media tree if they are absent.

    ⛔ WITHOUT THESE, EVERY MODEL CONVERTS WITH ITS MATERIAL TEMPLATE MISSING.

    The converter says so plainly and the run still exits 0:

        IO| Cant open file '_templates/models.xml'
        XML,System| Template 'DefaultNarrow' (type Material) not found

    `DefaultNarrow` is defined in `[media]/_templates/models.xml` inside
    `initial.pak`, and the Media tree the converter mounts had no `_templates`
    directory at all, so the file was simply unreachable. Extracting the four
    vanilla template files makes it resolve, and the converted output grows --
    proof the template is now actually applied rather than silently skipped.

    It hid for a long time because PLANTS NEVER REQUEST A TEMPLATE. Measured on
    this install: bamboo and banana_plant_x convert with no such message, while
    every single model -- bob_lazar_ufo, corrugated_culvert, desert_cliff_6 --
    carried it. "Only models are affected" reads like a models problem; it was
    a mount problem that only models could expose.

    Returns the names extracted, empty if they were already present.
    """
    d = media / "_templates"
    if d.is_dir() and (d / "models.xml").is_file():
        return []
    # The game root is BinEditor's grandparent: <game>/Sources/BinEditor.
    cwd = gamepaths.bin_editor(game)
    if cwd is None:
        return []
    initial = Path(cwd).parent.parent / "preload" / "paks" / "client" / "initial.pak"
    if not initial.is_file():
        return []
    out = []
    try:
        with zipfile.ZipFile(initial) as z:
            for n in z.namelist():
                if "_templates/" in n and n.endswith(".xml"):
                    d.mkdir(parents=True, exist_ok=True)
                    (d / Path(n).name).write_bytes(z.read(n))
                    out.append(Path(n).name)
    except Exception:
        return []
    return out


def prebuild_target(base: str, folder: str, media: Path) -> Path:
    """Where the editor writes the converted mesh. See reason 1 in the module
    docstring -- this is not a free choice."""
    return media / "prebuild" / "meshes" / f"{folder}_{base}"


def stage_billboards(base: str, folder: str, media: Path) -> list[str]:
    """Publish a baked plant billboard to BOTH places that consume it.

    TWO CONSUMERS, TWO PATHS, AND MISSING EITHER ONE LOOKS LIKE A DIFFERENT BUG.

    1. The CONVERTER loads it as `billboard/<folder>_<base>__d_a.dds`, resolved
       under the prebuild mount -- so `<Media>/prebuild/billboard/`. Without it
       the mesh conversion fails outright.
    2. The EDITOR AND GAME load it at distance from the converted texture cache,
       under the FLATTENED name the paks use:
       `<Media>/prebuild/textures/dds/billboard_<folder>_<base>__d_a.dds`.
       Shipped billboards are all there -- 1,149 of them in editor.pak. Without
       it the plant renders correctly up close and the engine substitutes a
       SOLID RED PLACEHOLDER CONE once it drops to the billboard LOD.

    The bake is already in the right format for both: DXT3 for `__d_a`, DXT1 for
    `__n_d` and `__zo_d`, full mip chains, matching the shipped files exactly.
    Only the location and the name differ.

    Returns the names staged. Empty means the converter has not baked them yet,
    which is normal before the first pass.
    """
    src = media / "textures" / "billboard"
    dst = media / "prebuild" / "billboard"
    cache = media / "prebuild" / "textures" / "dds"
    staged = []
    for suffix in BILLBOARD_SUFFIXES:
        f = src / f"{folder}_{base}{suffix}.dds"
        if f.is_file():
            dst.mkdir(parents=True, exist_ok=True)
            cache.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst / f.name)
            shutil.copy2(f, cache / f"billboard_{f.name}")
            staged.append(f.name)
    return staged


def _run_once(base: str, folder: str, media: Path, converter: Path,
              cwd: Path, timeout: int) -> tuple[int, str]:
    out = prebuild_target(base, folder, media)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    r = subprocess.run(
        # -s: "Report crash report automatically skipping any dialogs". Without
        # it an assertion opens a modal on the user's desktop and waits, which
        # is not acceptable from a batch tool.
        [str(converter), "--mod-convert", "-s",
         "-i", f"meshes/{folder}/{base}",
         "-o", str(out),
         "-r", str(media)],
        capture_output=True, text=True, timeout=timeout, cwd=str(cwd))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def convert(base: str, folder: str = "models", *, game: str | None = None,
            timeout: int = 900) -> dict:
    """Convert one installed mesh and report what the converter said.

    `folder` is `models` or `plants` and selects both the input path and the
    name of the converted output. Plants get the two-pass billboard treatment
    described in reason 3 above.

    Returns {"ok", "code", "log", "output", "size", "passes", "staged"}.
    """
    media = gamepaths.media_root(game)
    ensure_templates(media, game)
    converter = gamepaths.resource_converter(game)
    cwd = gamepaths.bin_editor(game)
    if converter is None or not converter.is_file():
        return {"ok": False, "code": -1, "log": "ResourceConverter not found; "
                "set SABER_GAME or toolkit.json", "output": None, "size": 0,
                "passes": 0, "staged": []}
    if cwd is None or not cwd.is_dir():
        return {"ok": False, "code": -1, "log": "BinEditor directory not found "
                "-- templates cannot resolve from anywhere else (reason 2)",
                "output": None, "size": 0, "passes": 0, "staged": []}

    out = prebuild_target(base, folder, media)
    staged: list[str] = []

    if folder == "plants":
        # DROP A STALE BILLBOARD FIRST. The billboard is a RENDER of the mesh,
        # so it goes out of date the moment the mesh is rebuilt -- and because
        # a staged billboard lets pass 1 succeed, a stale one is never noticed:
        # the plant converts, reports OK, and shows the OLD shape at distance.
        # Rebuilding eight plants to fix their orientation reused eight
        # billboards of the sideways versions.
        src = media / "meshes" / folder / f"{base}.fbx"
        if src.is_file():
            for suffix in BILLBOARD_SUFFIXES:
                f = media / "prebuild" / "billboard" / f"{folder}_{base}{suffix}.dds"
                if f.is_file() and f.stat().st_mtime < src.stat().st_mtime:
                    f.unlink()

    code, log = _run_once(base, folder, media, converter, cwd, timeout)
    passes = 1

    if folder == "plants" and not out.is_file():
        # The failed pass is the pass that baked the billboard. Stage it and
        # go again; one retry is enough because the bake is deterministic.
        staged = stage_billboards(base, folder, media)
        if staged:
            code, log = _run_once(base, folder, media, converter, cwd, timeout)
            passes = 2

    size = out.stat().st_size if out.is_file() else 0
    armature_fixed = _normalise_armature(out) if size else False
    return {"ok": code == 0 and size > 0, "code": code, "log": log,
            "output": out if size else None, "size": size,
            "passes": passes, "staged": staged,
            "armature_fixed": armature_fixed}


def _normalise_armature(out: Path) -> bool:
    """Force a converted mesh's `Armature` node basis to identity.

    The converter announces `Frame '<x>' has non-identity scale, resetting...`
    for mesh frames and does NOT do it for the armature node, so a skinned
    `_objective` mesh leaves here with a 100x basis -- Blender's metre-to-
    centimetre FBX unit factor, which `apply_unit_scale=True` does not remove.
    The skinned geometry inherits the node scale and the model draws 100x.

    This was fixed once by hand (`fix_armature_scale.py`) and came straight
    back on the next reconvert, because nothing in the pipeline applied it.
    Measured 2026-10-17: a clean reconvert of both Doc Windsor objective
    meshes reintroduced 100,100,100 on each. Doing it here means every path
    that converts gets it, not just the one that remembers.

    Only the 3x3 basis is touched. The translation row is left alone: vanilla's
    own armature carries a real translation and a blanket identity moves the
    model. No-ops when there is no armature or the basis is already identity.
    """
    TOKEN = b"Armature"
    try:
        data = bytearray(out.read_bytes())
    except OSError:
        return False
    i = data.find(TOKEN)
    if i < 0:
        return False
    off = i + len(TOKEN) + 1          # name, then its NUL terminator
    try:
        m = struct.unpack_from("<16f", data, off)
    except struct.error:
        return False
    if all(abs(m[r * 4 + r] - 1.0) < 1e-4 for r in range(3)):
        return False
    fixed = list(m)
    for r in range(3):
        for c in range(3):
            fixed[r * 4 + c] = 1.0 if r == c else 0.0
    struct.pack_into("<16f", data, off, *fixed)
    tmp = out.with_suffix(out.suffix + ".armtmp")
    tmp.write_bytes(bytes(data))
    os.replace(tmp, out)
    # read back from the file on disk, not the buffer we just built
    got = struct.unpack_from("<16f", out.read_bytes(), off)
    return all(abs(got[r * 4 + r] - 1.0) < 1e-6 for r in range(3))


def billboard_published(base: str, folder: str, media: Path) -> tuple[bool, str]:
    """Are all three billboard maps in BOTH places the engine reads them from?

    A pure file-existence check, so it cannot be argued with or fooled. Missing
    from the runtime cache is the failure that renders a solid red placeholder
    cone at distance while the plant looks perfect up close.
    """
    missing = []
    for suffix in BILLBOARD_SUFFIXES:
        name = f"{folder}_{base}{suffix}.dds"
        if not (media / "prebuild" / "billboard" / name).is_file():
            missing.append(f"prebuild/billboard/{name}")
        if not (media / "prebuild" / "textures" / "dds" / f"billboard_{name}").is_file():
            missing.append(f"prebuild/textures/dds/billboard_{name}")
    if missing:
        return False, f"{len(missing)} billboard file(s) missing: {missing[0]} ..."
    return True, "all 3 maps published to both the converter and runtime paths"


def interesting(log: str) -> list[str]:
    """The lines of a converter log worth showing a human."""
    return [l.strip() for l in log.splitlines()
            if l.strip() and not any(n in l for n in NOISE)]


# --------------------------------------------------------------- mod textures

def convert_texture(tga: Path, mod_root: Path, *, game: str | None = None,
                    timeout: int = 300) -> dict:
    """Convert one of a MOD's source TGAs into the .pct the runtime loads.

    🚨 THE RUNTIME NEVER READS A .tga. A mod that ships only source textures
    has, as far as the engine is concerned, no texture at all -- and this
    engine draws an absent texture as SOLID, DETAIL-FREE RED. That is the same
    placeholder documented above for billboards, and it is what a recoloured
    FOB module looked like in game on 2026-10-16: flat red bodywork, while the
    decals, tyres and cargo it did NOT repoint rendered perfectly, because
    those still pointed at vanilla names present in the shipped paks.

    ⛔ Do not rely on Mod Manager's CONVERT to do this. Measured on the failed
    build: the mod folder gained no `prebuild/textures/` at all and no
    `RebuildLog`, while every working mod on disk has both. Producing the .pct
    here makes the mod self-sufficient, which is also how Gimbo's Resource
    Scanner and PebbleGas are shipped.

    Verified: the output matches a working mod's byte-for-byte in size
    (pct 5,592,598 and nx 2,497,806 for a 2048 source) and decodes back to the
    intended hue.
    """
    # ⛔ ABSOLUTE, ALWAYS. `-r` and `-o` are resolved against the converter's
    # CWD, which is BinEditor, not ours. A relative mod root fails with
    # "Unable to find texture file textures/models/<name>.tga" -- a message
    # that reads like a missing file and is really a wrong working directory.
    mod_root = Path(mod_root).resolve()
    stem = tga.stem
    out = mod_root / "prebuild" / "textures" / "pct" / f"models_{stem}.pct"
    out.parent.mkdir(parents=True, exist_ok=True)
    converter = gamepaths.resource_converter(game)
    cwd = gamepaths.bin_editor(game)
    if not converter or not Path(converter).is_file():
        return {"ok": False, "log": "ResourceConverter not found", "out": str(out)}

    r = subprocess.run(
        [str(converter), "--mod-convert", "-s", "--all-pct", "--make-pct-headers",
         "-i", f"textures/models/{stem}.tga",
         "-o", str(out),
         "-r", str(mod_root)],
        capture_output=True, text=True, timeout=timeout, cwd=str(cwd))
    header = out.with_suffix(".pct_header")
    return {"ok": out.is_file() and header.is_file(),
            "code": r.returncode,
            "pct": out.stat().st_size if out.is_file() else 0,
            "out": str(out),
            "log": (r.stdout or "") + (r.stderr or "")}


def textures_published(mod_root: Path, stems: list[str]) -> tuple[bool, str]:
    """Every recoloured map must have a .pct AND its .pct_header beside it.

    A pure file-existence check. This is the gate that would have caught the
    red module before it ever reached the game.
    """
    missing = []
    for stem in stems:
        base = mod_root / "prebuild" / "textures" / "pct" / f"models_{stem}"
        for p in (base.with_suffix(".pct"), base.with_suffix(".pct_header")):
            if not p.is_file():
                missing.append(p.name)
    if missing:
        return False, f"{len(missing)} runtime texture file(s) missing: {missing[0]} ..."
    return True, f"all {len(stems)} maps published as .pct + .pct_header"
