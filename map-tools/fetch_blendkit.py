"""fetch_blendkit.py -- search and download CC0 models from BlendKit / BlenderKit.

blendkit.com is a front on the BlenderKit library: its download URLs redirect to
`assets.blenderkit.com`, and its API speaks BlenderKit's vocabulary
(`assetBaseId`, `scene_uuid`). Everything needed is open.

    GET /api/v1/search/?query=<terms>          no auth, no key, no account
    GET /api/v1/downloads/<uuid>/?scene_uuid=  returns a signed URL for the file

`scene_uuid` is the one non-obvious part. Without it the download endpoint
answers 403 "Parameter scene_uuid not set" -- it is the addon's session
identifier and any UUID satisfies it. No API key was needed for a CC0 asset.

TAKE THE glTF, NOT THE .blend. Every model here offers both, and the .blend is
an order of magnitude larger (214 MB against 16 MB for the same boulder) and
requires Blender to open. The glTF is a self-contained .glb with geometry,
materials and textures embedded, which is a short step from the FBX the
Expeditions guide asks for.

LICENCE IS FILTERED, NOT ASSUMED. Two values appear: `cc_zero` (public domain,
use anywhere) and `royalty_free` (BlenderKit's own terms, which are NOT public
domain and carry conditions). This defaults to CC0 only. `--any-licence` lifts
that and prints what each asset actually is, so the choice is visible rather
than buried.

    python fetch_blendkit.py "namaqualand boulder"
    python fetch_blendkit.py "desert shrub" --limit 30
    python fetch_blendkit.py "namaqualand boulder" --get 1,2,3
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://www.blendkit.com/api/v1"
OUT = Path(__file__).parent / "models" / "blendkit"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36",
      "Accept": "application/json",
      "Referer": "https://www.blendkit.com/"}
# any UUID satisfies the endpoint; it identifies an addon session, not a user
SCENE = "00000000-0000-0000-0000-000000000001"


def get(url, raw=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    return data if raw else json.loads(data)


def search(terms, free=True, asset_type="model", page_size=None):
    q = terms
    if asset_type:
        q += f" asset_type:{asset_type}"
    if free:
        q += " is_free:true"
    url = f"{BASE}/search/?query={urllib.parse.quote(q)}"
    return get(url)


def triangles_and_scale(glb: bytes):
    """Read the glTF JSON chunk for a triangle count and the mesh names.

    Mesh names carry real-world scale on this library -- `boulder_02_5m` is a
    five-metre boulder -- which matters more than it sounds: a rock imported at
    the wrong scale is the difference between a kerbstone and a cliff.
    """
    import struct
    if glb[:4] != b"glTF":
        return None
    clen = struct.unpack_from("<I", glb, 12)[0]
    js = json.loads(glb[20:20 + clen].decode("utf8"))
    tris = 0
    for m in js.get("meshes", []):
        for p in m.get("primitives", []):
            acc = js.get("accessors", [])[p["indices"]] if "indices" in p else None
            if acc:
                tris += acc["count"] // 3
    return {"meshes": [m.get("name") for m in js.get("meshes", [])],
            "triangles": tris,
            "materials": len(js.get("materials", [])),
            "images": len(js.get("images", []))}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("terms")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--any-licence", action="store_true",
                    help="include royalty_free. NOT public domain -- BlenderKit's "
                         "own terms apply and they carry conditions.")
    ap.add_argument("--type", default="model",
                    help="model, material, brush, scene, hdr; empty for all")
    ap.add_argument("--get", default=None,
                    help="comma-separated row numbers from the listing to download")
    args = ap.parse_args()

    d = search(args.terms, asset_type=args.type or None)
    rows = d.get("results", [])
    if not args.any_licence:
        rows = [a for a in rows if a.get("license") == "cc_zero"]
    rows = rows[:args.limit]
    print(f'"{args.terms}": {d.get("count", 0)} matches, '
          f'{len(rows)} shown{"" if args.any_licence else " (CC0 only)"}\n')
    if not rows:
        print("   nothing CC0 here. --any-licence shows the royalty_free ones,")
        print("   which are usable but are NOT public domain.")
        return 0

    print(f"{'#':>3}  {'name':<34}{'licence':<14}{'gltf':>9}  tags")
    for i, a in enumerate(rows, 1):
        gl = next((f for f in a.get("files", []) if f["fileType"] == "gltf"), None)
        sz = f"{gl['fileUploadSize']/1e6:.1f} MB" if gl else "-"
        print(f"{i:>3}  {a['name'][:33]:<34}{a.get('license',''):<14}{sz:>9}  "
              f"{', '.join((a.get('tags') or [])[:5])[:60]}")

    if not args.get:
        print("\n(nothing downloaded -- pass --get 1,2 to fetch by row number)")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    want = [int(v) for v in args.get.split(",")]
    print()
    for i in want:
        if not (1 <= i <= len(rows)):
            print(f"   !! no row {i}")
            continue
        a = rows[i - 1]
        gl = next((f for f in a.get("files", []) if f["fileType"] == "gltf"), None)
        if not gl:
            print(f"   !! {a['name']}: no glTF, only .blend -- skipped")
            continue
        meta = get(f"{gl['downloadUrl']}?scene_uuid={SCENE}")
        blob = get(meta["filePath"], raw=True)
        slug = "".join(c if c.isalnum() else "_" for c in a["name"]).strip("_").lower()
        p = OUT / f"{slug}.glb"
        p.write_bytes(blob)
        info = triangles_and_scale(blob) or {}
        # provenance beside the asset. A CC0 claim that lives only in a chat
        # message is a CC0 claim that is gone by next session.
        (OUT / f"{slug}.json").write_text(json.dumps({
            "name": a["name"], "license": a.get("license"),
            "author": (a.get("author") or {}).get("fullName"),
            "assetBaseId": a.get("assetBaseId"),
            "source": f"https://www.blendkit.com/asset-gallery-detail/{a.get('assetBaseId')}/",
            "tags": a.get("tags"), "fetched_from": BASE,
            **info}, indent=2), encoding="utf8")
        print(f"   {p.name}  {len(blob)/1e6:.1f} MB  "
              f"{info.get('triangles', 0):,} tris  "
              f"{info.get('materials', 0)} material(s)  "
              f"{a.get('license')}")
        if info.get("meshes"):
            print(f"      meshes: {', '.join(m for m in info['meshes'] if m)[:90]}")

    print(f"\n   {OUT}")
    print("   glTF is not yet an Expeditions mesh -- the guide wants FBX.")
    print("   Each .glb has a .json beside it recording licence and author.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
