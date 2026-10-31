"""meshy.py -- generate a model from image(s) and bring it back as GLB.

    python meshy.py image.png --name pebblegas
    python meshy.py views/*.png --name chalet --multi
    python meshy.py --collect                     # finish anything still running

WHY THIS AND NOT THE WEB APP

The web app and the API are SEPARATE TASK SPACES. Measured: with two API tasks
created and several models sitting in the web workspace, `GET
/openapi/v1/image-to-3d` returned exactly the two API tasks and none of the web
ones. A model made in the browser cannot be fetched through the API at all, and
a browser download that fails leaves no other route to it. Generating through
the API is the only reproducible path: the file lands on disk, the inputs are
recorded, and the same model can be rebuilt later.

WHAT THE API ACTUALLY RETURNS, as opposed to what the parameters suggest

* `target_polycount` IS IGNORED on the image-to-3d endpoint. Asked for 20,000,
  received 2,202,674 triangles -- thirty-three times the engine's entire
  per-mesh ceiling. Decimation downstream is not optional.
* Textured generation costs 30 credits, not the 5 the web app shows for a
  mesh-only preview.
* `model_urls` carries glb, fbx, obj, mtl, usdz and stl. TAKE THE GLB.
  `glb_to_fbx.py` imports glTF, and Meshy's own FBX has none of what the engine
  needs: one root node, a `cdt_`-prefixed collision mesh, identity transforms,
  a LOD chain, or TGA textures at powers of two. That FBX is not a shortcut.
* Signed asset URLs expire. Download immediately; do not store them.

The API key is read from %LOCALAPPDATA%\\ExpeditionsToolkit\\meshy_api_key or
$MESHY_API_KEY, never from this repository.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import unicodedata
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from saber import gamepaths  # noqa: E402

HERE = Path(__file__).parent
API = "https://api.meshy.ai/openapi/v1"
STATE = gamepaths.asset_store() / "meshy"
KEY_FILE = Path(os.environ.get("LOCALAPPDATA", "")) / "ExpeditionsToolkit" / "meshy_api_key"

TERMINAL = ("SUCCEEDED", "FAILED", "CANCELED")


def key() -> str:
    k = os.environ.get("MESHY_API_KEY")
    if not k and KEY_FILE.is_file():
        k = KEY_FILE.read_text(encoding="utf8").strip()
    if not k:
        raise SystemExit(f"no API key. Put it in {KEY_FILE} or set MESHY_API_KEY.")
    return k


def head() -> dict:
    return {"Authorization": f"Bearer {key()}", "Content-Type": "application/json"}


def slug(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = "".join(c.lower() if c.isalnum() else "_" for c in folded)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")[:48] or "model"


def data_uri(p: Path) -> str:
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def submit(images: list[Path], multi: bool, polycount: int = 30000,
           smart: bool = False) -> str:
    """Start a generation. Returns the task id."""
    if multi:
        if not 1 <= len(images) <= 4:
            raise SystemExit("multi-image takes 1 to 4 images, all of the SAME "
                             "object from different angles")
        url = f"{API}/multi-image-to-3d"
        body = {"image_urls": [data_uri(p) for p in images]}
    else:
        url = f"{API}/image-to-3d"
        body = {"image_url": data_uri(images[0])}
    body.update({"should_texture": True, "topology": "triangle",
                 "target_polycount": polycount, "symmetry_mode": "off"})
    if smart:
        # Smart Topology builds the low-poly mesh itself, with a UV layout made for
        # it. The default path's remesh leaves an atlas of thousands of unpadded
        # fragments that smears at the mip levels the game samples at distance.
        if polycount > 30000:
            raise SystemExit("smart-topology (meshy-t2.5) accepts at most 30,000 faces")
        body.update({"model_type": "smart-topology", "ai_model": "meshy-t2.5",
                     "enable_pbr": True, "texture_resolution": "4k"})
    r = requests.post(url, headers=head(), json=body, timeout=300)
    if r.status_code != 202:
        raise SystemExit(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["result"]


def submit_remesh(input_task_id: str, polycount: int,
                  topology: str = "triangle") -> str:
    """Retopologise an existing task to a real polygon budget.

    THIS IS THE FIX FOR A SCRAMBLED SKIN, AND IT IS NOT OPTIONAL ON MESHY OUTPUT.

    `target_polycount` is IGNORED by image-to-3d -- a run asking for 30,000 came
    back with 3,876,040 triangles, 59x the engine's 65,534 per-mesh ceiling. The
    only way down from there used to be decimation, which keeps the silhouette
    and DESTROYS THE UV ATLAS: the model arrives in game with the right shape and
    a smeared skin, which reads as a texture bug and is not one. Measured twice,
    on two different assets, and diagnosed from scratch the second time because
    this endpoint was documented in `glb_to_fbx.py` but never implemented here.

    `target_polycount` IS honoured by remesh, and remesh rebuilds the UVs rather
    than collapsing them.
    """
    r = requests.post(f"{API}/remesh", headers=head(), timeout=300,
                      json={"input_task_id": input_task_id,
                            "target_polycount": polycount,
                            "topology": topology})
    if r.status_code != 202:
        raise SystemExit(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["result"]


def poll(tid: str, kind, timeout: int = 3600, quiet: bool = False) -> dict:
    if kind is True or kind is False:                 # legacy multi= callers
        kind = "multi-image-to-3d" if kind else "image-to-3d"
    url = f"{API}/{kind}/{tid}"
    t0, last = time.time(), None
    while time.time() - t0 < timeout:
        r = requests.get(url, headers=head(), timeout=90)
        if not r.ok:
            raise SystemExit(f"HTTP {r.status_code}: {r.text[:200]}")
        d = r.json()
        st = (d.get("status"), d.get("progress"))
        if st != last and not quiet:
            print(f"      [{int(time.time()-t0):5d}s] {st[0]} {st[1]}", flush=True)
            last = st
        if d.get("status") in TERMINAL:
            return d
        time.sleep(10)
    raise SystemExit(f"task {tid} still running after {timeout}s")


def harvest(task: dict, name: str, dest: Path) -> Path | None:
    """Write the GLB, its textures, and a record of how it was made."""
    dest.mkdir(parents=True, exist_ok=True)
    if task.get("status") != "SUCCEEDED":
        print(f"   !! {name}: {task.get('status')} {task.get('task_error')}")
        return None
    glb_url = (task.get("model_urls") or {}).get("glb")
    if not glb_url:
        print(f"   !! {name}: no glb in model_urls {list((task.get('model_urls') or {}))}")
        return None
    r = requests.get(glb_url, timeout=1800)
    # Guard the payload before writing: a short body here is an expired signature,
    # and an 800-byte .glb that looks downloaded is worse than no file.
    if not r.ok or len(r.content) < 50_000:
        print(f"   !! {name}: GLB HTTP {r.status_code}, {len(r.content)} bytes")
        return None
    out = dest / f"{name}.glb"
    out.write_bytes(r.content)
    tex = []
    for i, t in enumerate(task.get("texture_urls") or []):
        for k, u in t.items():
            rr = requests.get(u, timeout=900)
            if rr.ok and len(rr.content) > 1000:
                p = dest / f"{name}_tex{i}_{k}.png"
                p.write_bytes(rr.content)
                tex.append(p.name)
    (dest / f"{name}.json").write_text(json.dumps({
        "name": name, "task_id": task.get("id"), "status": task.get("status"),
        "credits": task.get("consumed_credits"),
        "created_at": task.get("created_at"), "textures": tex,
        "source": "Meshy API", "licence": "per Meshy plan terms",
        # Signed URLs expire, so they are deliberately NOT recorded.
    }, indent=2), encoding="utf8")
    print(f"   ok  {name:22s} {len(r.content)/1048576:6.1f} MB  "
          f"{len(tex)} texture(s)  {task.get('consumed_credits')} credits")
    return out



def _read_ledger(ledger: Path) -> dict:
    """tasks.json, tolerating trailing junk from an old interleaved write."""
    if not ledger.is_file():
        return {}
    text = ledger.read_text(encoding="utf8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.JSONDecoder().raw_decode(text)[0]


def _record(ledger: Path, name: str, rec: dict) -> None:
    """Add one task under a lock, re-reading first and replacing the file whole.

    Several meshy.py runs at once each read the ledger at start and wrote it back
    later: entries were lost and the writes interleaved into invalid JSON, which
    stopped every later run (2026-10-25, six models in parallel).
    """
    lock = ledger.with_suffix(".lock")
    for _ in range(600):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            time.sleep(0.1)
    else:
        raise SystemExit(f"{lock} held for a minute; delete it if no meshy.py is running")
    try:
        tasks = _read_ledger(ledger)
        tasks[name] = rec
        tmp = ledger.with_suffix(".tmp")
        tmp.write_text(json.dumps(tasks, indent=2), encoding="utf8")
        os.replace(tmp, ledger)
    finally:
        os.close(fd)
        os.remove(lock)

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="*", type=Path)
    ap.add_argument("--name", default=None)
    ap.add_argument("--multi", action="store_true",
                    help="all images are the SAME object from different angles")
    ap.add_argument("--polycount", type=int, default=30000)
    ap.add_argument("--smart-topology", action="store_true",
                    help="Meshy's Smart Topology (meshy-t2.5): a game-ready mesh and UV "
                         "layout at --polycount (max 30,000), no separate remesh")
    ap.add_argument("--out", default=str(STATE))
    ap.add_argument("--collect", action="store_true",
                    help="poll and download every task recorded in tasks.json")
    ap.add_argument("--remesh", metavar="TASK_ID",
                    help="retopologise an existing task to --polycount. REQUIRED "
                         "for anything heading into the engine: image-to-3d "
                         "ignores target_polycount, and decimating the result "
                         "scrambles its UV atlas.")
    a = ap.parse_args()

    dest = Path(a.out)
    dest.mkdir(parents=True, exist_ok=True)
    ledger = dest / "tasks.json"
    tasks = _read_ledger(ledger)

    if a.collect:
        for name, rec in list(tasks.items()):
            tid = rec["id"] if isinstance(rec, dict) else rec
            multi = rec.get("multi", False) if isinstance(rec, dict) else False
            if (dest / f"{name}.glb").is_file():
                print(f"   --  {name}: already downloaded")
                continue
            print(f"   ..  {name} ({tid})")
            harvest(poll(tid, multi), name, dest)
        return 0

    if a.remesh:
        name = slug(a.name or f"remesh_{a.remesh[:8]}")
        tid = submit_remesh(a.remesh, a.polycount)
        print(f"   remeshing {a.remesh} -> {tid}  (target {a.polycount:,} tris)")
        _record(ledger, name, {"id": tid, "kind": "remesh", "from": a.remesh,
                               "target_polycount": a.polycount})
        harvest(poll(tid, "remesh"), name, dest)
        print(f"\n   next: python glb_to_fbx.py {dest / (name + '.glb')} "
              f"--name {name} --apply --check --collider hull --height <metres>")
        return 0

    if not a.images:
        ap.error("give at least one image, or use --collect")
    imgs = [p for p in a.images if p.is_file()]
    if not imgs:
        raise SystemExit("none of those image paths exist")
    name = slug(a.name or imgs[0].stem)
    tid = submit(imgs, a.multi, a.polycount, a.smart_topology)
    print(f"   submitted {name} -> {tid}")
    _record(ledger, name, {"id": tid, "multi": a.multi, "images": [str(p) for p in imgs]})
    harvest(poll(tid, a.multi), name, dest)
    print(f"\n   next: python glb_to_fbx.py {dest / (name + '.glb')} "
          f"--name {name} --apply --check --collider hull --height <metres>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
