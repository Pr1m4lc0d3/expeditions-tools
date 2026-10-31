"""Run one FOB model through the recorded recipe, step by step, the same way every time.

    python model_pipeline.py gen    <brand> <prompts.md>   Codex image -> models/source/<brand>_01.png
    python model_pipeline.py back   <brand>                 Codex rear view -> <brand>_02.png (for lettered carts)
    python model_pipeline.py meshy  <brand>                 Meshy full detail (both views if _02 exists) + remesh
    python model_pipeline.py review <brand>                 base check + three renders to look at
    python model_pipeline.py rebake <brand>                 low-poly + colour from the full-detail model
    python model_pipeline.py build  <brand> <length> <sink> [yaw] glb_to_fbx --check (the back-face gate)

`build` uses <brand>_rb.glb with --high when `rebake` has made one (the path for
any model with lettering), else Meshy's remesh <brand>_rm.glb.

Each step stops on failure and prints what to look at. Look at the image after
`gen` and the renders after `review` before going on: the gate measures faces,
not whether a sign reads.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOLKIT = Path(__file__).resolve().parents[1]
SOURCE = TOOLKIT / "models" / "source"
sys.path.insert(0, str(TOOLKIT))
from saber import gamepaths  # noqa: E402

MESHY = gamepaths.meshy_store()
BLENDER = gamepaths.blender()
MEDIA = gamepaths.media_root("expeditions")
REVIEW = TOOLKIT / "build_reports"
ENV = {"PYTHONIOENCODING": "utf-8"}


def run(cmd, timeout=3600, cwd=TOOLKIT):
    import os
    # subprocess.run's timeout kills only the npm .cmd shim; codex.exe under it keeps
    # the pipes open. The hang itself (11 hours, 2026-10-26) was codex waiting on an
    # inherited stdin ("Reading additional input from stdin"): give it none.
    p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace", env={**os.environ, **ENV})
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        out, err = p.communicate()
        return 1, (out or "") + (err or "") + f"\n!! timed out after {timeout}s, process tree killed"
    return p.returncode, out + err


def prompt_for(brand: str, prompts: Path) -> str:
    text = prompts.read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(brand)}\s*\n+```text\n(.*?)\n```", text, re.S | re.M)
    if not m:
        raise SystemExit(f"no prompt for {brand} in {prompts}")
    return m.group(1).strip()


def gen(brand: str, prompts: Path) -> int:
    out_dir = SOURCE / "gen"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{brand}_01.png"
    if target.exists():
        target.unlink()
    ask = ("Use your image generation tool to create ONE image from the prompt below. "
           "Landscape 1536x1024. It must have a transparent background (PNG with alpha). "
           f"Save it as {target.name} in the current directory and reply with the saved path only. "
           f"Prompt: {prompt_for(brand, prompts)}")
    codex = shutil.which("codex") or "codex"   # an npm .cmd shim on Windows
    rc, out = run([codex, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write",
                   "-C", out_dir, ask], timeout=1200)
    if not target.exists():
        # Codex sometimes generates the image but cannot copy it out of its own
        # folder ("Unable to save to the requested directory"). Take it from there.
        found = re.findall(r"[A-Za-z]:\\[^\s'\"]*generated_images[^\s'\"]*\.png", out)
        if found and Path(found[-1]).exists():
            shutil.copy2(found[-1], target)
    if not target.exists():
        print(out[-1500:])
        print(f"!! no image at {target}")
        return 1
    from PIL import Image
    import numpy as np
    im = Image.open(target)
    a = np.array(im.convert("RGBA"))[:, :, 3]
    clear = (a == 0).mean()
    print(f"  image {target}  {im.size}  transparent {100 * clear:.0f}%")
    if clear < 0.05:
        print("  !! no transparent background; Meshy needs the object isolated")
    shutil.copy2(target, SOURCE / f"{brand}_01.png")
    return 0


BACK_ASK = ("Use your image generation tool to create ONE image of EXACTLY the same object as the "
            "attached image, same model, same colours, same wear, same proportions, seen from the "
            "opposite side: a three-quarter REAR view from slightly above, showing its back and the "
            "far side. Any panel on the back or far side is plain painted metal with NO lettering, "
            "NO words, NO logos. Landscape 1536x1024, fully transparent background (PNG with "
            "alpha), no floor, no shadow. Save it as {name} in the current directory and reply "
            "with the saved path only.")


def back(brand: str) -> int:
    """A rear view of the same object, so Meshy sees the back instead of inventing it.

    With one picture Meshy invents the far side, and on a symmetric cart it invents a
    mirrored copy of the lettering: "KPWADEEL", "CH CK-IT", "IDHM SWEET OHM" (2026-10-25).
    """
    out_dir = SOURCE / "gen"
    target = out_dir / f"{brand}_02.png"
    if target.exists():
        target.unlink()
    codex = shutil.which("codex") or "codex"
    rc, out = run([codex, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write",
                   "-C", out_dir, BACK_ASK.format(name=target.name),
                   # -i takes several files, so it goes AFTER the prompt or it eats it
                   "-i", SOURCE / f"{brand}_01.png"],
                  timeout=1200)
    if not target.exists():
        found = re.findall(r"[A-Za-z]:\\[^\s'\"]*generated_images[^\s'\"]*\.png", out)
        if found and Path(found[-1]).exists():
            shutil.copy2(found[-1], target)
    if not target.exists():
        print(out[-1500:]); print(f"!! no back view at {target}"); return 1
    shutil.copy2(target, SOURCE / f"{brand}_02.png")
    print(f"  back view {target}")
    return 0


def meshy(brand: str) -> int:
    for ext in (".glb", ".json", "_tex0_base_color.png"):
        p = MESHY / f"{brand}{ext}"
        if p.exists():
            p.replace(MESHY / f"{brand}_prev{ext}")   # rename() fails on Windows once a _prev exists
    views = [SOURCE / f"{brand}_01.png"]
    if (SOURCE / f"{brand}_02.png").exists():
        views.append(SOURCE / f"{brand}_02.png")
    rc, out = run([sys.executable, "meshy.py", *views, "--name", brand, *(["--multi"] if len(views) > 1 else [])])
    print("\n".join(l for l in out.splitlines() if " ok " in l or "!!" in l or "rror" in l))
    if not (MESHY / f"{brand}.glb").exists():
        print(out[-1500:]); return 1
    task = json.loads((MESHY / f"{brand}.json").read_text())["task_id"]
    rc, out = run([sys.executable, "meshy.py", "--remesh", task, "--name", f"{brand}_rm",
                   "--polycount", "30000"])
    print("\n".join(l for l in out.splitlines() if " ok " in l or "!!" in l or "rror" in l))
    return 0 if (MESHY / f"{brand}_rm.glb").exists() else 1


def review(brand: str) -> int:
    rc, out = run([sys.executable, "check_model_base.py", MESHY / f"{brand}_rm.glb"])
    print("\n".join(out.strip().splitlines()[-6:]))
    d = REVIEW / brand
    d.mkdir(parents=True, exist_ok=True)
    shots = []
    for tag, az, el in (("front", -30, 18), ("side", 35, 22), ("back", 160, 25)):
        p = d / f"review_{tag}.png"
        run([BLENDER, "-b", "--factory-startup", "-P", TOOLKIT / "fob_projects" / "render_cutout.py",
             "--", MESHY / f"{brand}_rm.glb", p, az, el], timeout=900)
        shots.append(p)
    from PIL import Image
    ims = [Image.open(p).convert("RGBA") for p in shots if p.exists()]
    if not ims:
        print("!! no renders"); return 1
    w, h = ims[0].width // 2, ims[0].height // 2
    sheet = Image.new("RGB", (w * len(ims), h), (200, 200, 200))
    for i, im in enumerate(ims):
        j = im.resize((w, h)); bg = Image.new("RGBA", j.size, (200, 200, 200, 255))
        bg.alpha_composite(j); sheet.paste(bg.convert("RGB"), (i * w, 0))
    sheet.save(d / "review_sheet.png")
    print(f"  review sheet: {d / 'review_sheet.png'}")
    return 0


def rebake(brand: str) -> int:
    rc, out = run([sys.executable, "rebake_from_hd.py", MESHY / f"{brand}.glb", MESHY / f"{brand}_rb.glb"],
                  timeout=3600)
    print(out.strip().splitlines()[-1] if out.strip() else "!! no output")
    return rc


def build(brand: str, length: str, sink: str, yaw: str = "0") -> int:
    pre = MEDIA / "prebuild" / "meshes" / f"models_{brand}"
    if pre.is_dir():
        shutil.rmtree(pre)
    elif pre.exists():
        pre.unlink()
    rb = MESHY / f"{brand}_rb.glb"
    src = [rb, "--high", MESHY / f"{brand}.glb"] if rb.exists() else [MESHY / f"{brand}_rm.glb"]
    rc, out = run([sys.executable, "-W", "ignore", "glb_to_fbx.py", src[0],
                   "--name", brand, *src[1:], "--apply", "--check", "--collider", "hull",
                   "--length", length, "--sink", sink, "--yaw", yaw,
                   "--need-preload", "--two-sided",
                   # Meshy's corners bend 10+ degrees off the face over ~90% of every
                   # model; the bake then tilts the map to undo it, which only holds if
                   # the game builds Blender's exact tangents. It doesn't: wedges across
                   # Meg's sign and the Chuck-It crates (2026-10-29). 40 keeps flat
                   # panels flat and 90-degree corners hard; bent area fell 96% -> 13%.
                   "--smooth-angle", "40"], timeout=2400)
    keep = [l for l in out.splitlines() if re.search(r"PASS|FAIL|REFUSED|seen from behind|rror", l)]
    print("\n".join(keep[-6:]))
    return 0 if any("PASS  seen from behind" in l for l in keep) else 1


if __name__ == "__main__":
    step, args = sys.argv[1], sys.argv[2:]
    sys.exit({"gen": lambda: gen(args[0], Path(args[1])), "meshy": lambda: meshy(args[0]),
              "review": lambda: review(args[0]), "rebake": lambda: rebake(args[0]),
              "back": lambda: back(args[0]),
              "build": lambda: build(*args[:4])}[step]())
