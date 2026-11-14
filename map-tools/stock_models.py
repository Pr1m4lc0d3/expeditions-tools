"""stock_models.py -- fill the editor's asset library from BlendKit, in bulk.

`fetch_blendkit.py` finds and downloads one model at a time. `glb_to_fbx.py`
converts and installs one model at a time and has ResourceConverter check it.
This drives both across a whole category and writes down what happened.

    python stock_models.py --plan                 # see the shopping list
    python stock_models.py rocks --apply
    python stock_models.py rocks trees houses --apply

WHAT IT WILL AND WILL NOT TAKE

  * glTF only. Many library entries ship `.blend` alone, which needs a Blender
    round trip this pipeline does not do, so they are skipped and counted.
  * A size ceiling, because a 136 MB download is one asset, not thirty.
  * CC0 by default. `--any-licence` adds BlendKit's royalty-free pool, which is
    far larger. Their terms allow commercial use and forbid re-selling the asset
    "in the same form (eg. a 3D model sold as a 3D model or part of assetpack or
    game level on a marketplace)" -- https://www.blenderkit.com/docs/licenses/
    A map given away free is fine. A map sold on a storefront is not.

Every installed model is verified by running the real converter. Nothing is
reported as installed on the strength of the files existing.

The manifest lands in models/blendkit/manifest.json and records licence and
author per asset, which is what makes the attribution question answerable later.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fetch_blendkit as fb
import saber  # noqa: F401  (puts ../fob-modules on the path)
import glb_to_fbx as gf

OUT = Path(__file__).parent / "models" / "blendkit"
MANIFEST = OUT / "manifest.json"

# Ordered so the first hits are the ones a desert canyon map actually wants.
CATEGORIES = {
    "rocks": [
        "category_subtree:nature rock", "category_subtree:nature boulder",
        "category_subtree:nature cliff", "category_subtree:nature sandstone",
        "category_subtree:nature stone",
    ],
    "trees": [
        "category_subtree:nature tree", "category_subtree:nature pine",
        "category_subtree:nature juniper", "category_subtree:nature dead tree",
    ],
    "plants": [
        "category_subtree:nature bush", "category_subtree:nature shrub",
        "category_subtree:nature grass", "category_subtree:nature cactus",
        "category_subtree:nature desert plant",
    ],
    "houses": [
        "category_subtree:building house", "category_subtree:building cabin",
        "category_subtree:building shack", "category_subtree:building barn",
        "category_subtree:building ruin",
    ],
}


def slug(s: str) -> str:
    return gf.slug(s)


def candidates(queries, want, free, max_mb, seen):
    """Rows that are actually usable: glTF present, within the size ceiling."""
    out, skipped = [], {"no_gltf": 0, "too_big": 0, "dupe": 0}
    for q in queries:
        if len(out) >= want:
            break
        try:
            res = fb.search(q, free=free).get("results", [])
        except Exception as e:                    # noqa: BLE001
            print(f"   search failed ({q}): {e}")
            continue
        for a in res:
            if len(out) >= want:
                break
            name = slug(a.get("name", ""))
            if not name or name in seen:
                skipped["dupe"] += 1
                continue
            # The field is fileUploadSize, not fileSize; fileSize reads 0 and
            # silently disables the size ceiling.
            gltf = next((f for f in a.get("files", [])
                         if f.get("fileType") == "gltf"), None)
            if not gltf:
                skipped["no_gltf"] += 1
                continue
            mb = (gltf.get("fileUploadSize") or 0) / 1e6
            if mb > max_mb:
                skipped["too_big"] += 1
                continue
            seen.add(name)
            out.append({"name": name, "title": a.get("name"),
                        "licence": a.get("license"),
                        "author": (a.get("author") or {}).get("fullName"),
                        "uuid": a.get("assetBaseId"), "mb": round(mb, 1),
                        # The file record carries its own downloadUrl. Building one
                        # from assetBaseId 404s on every asset.
                        "download_url": gltf.get("downloadUrl"),
                        "query": q})
    return out, skipped


def download(row) -> Path | None:
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"{row['name']}.glb"
    if dest.is_file():
        return dest
    if not row.get("download_url"):
        print("      no downloadUrl on the glTF record")
        return None
    try:
        meta = fb.get(f"{row['download_url']}?scene_uuid={fb.SCENE}")
        blob = fb.get(meta["filePath"], raw=True)
    except Exception as e:                        # noqa: BLE001
        print(f"      download failed: {e}")
        return None
    dest.write_bytes(blob)
    dest.with_suffix(".json").write_text(json.dumps(
        {"license": row["licence"], "author": row["author"],
         "title": row["title"], "source": "blendkit.com",
         "assetBaseId": row["uuid"]}, indent=2), encoding="utf8")
    return dest


def convert(glb: Path, no_instancing: bool) -> tuple[bool, str]:
    cmd = [sys.executable, str(Path(__file__).parent / "glb_to_fbx.py"), str(glb),
           "--apply", "--check"]
    if no_instancing:
        cmd.append("--no-instancing")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,
                       cwd=str(Path(__file__).parent))
    tail = [l for l in (r.stdout or "").splitlines() if l.strip()]
    if r.returncode == 0:
        kind = next((l.split("collision, ")[1].rstrip(")")
                     for l in tail if "collision," in l), "?")
        return True, f"clean ({kind} collider)"
    # The licence notice also starts with "!!" and is not a failure; matching it
    # reports every royalty-free asset as "not public domain" whatever went wrong.
    why = next((l.strip() for l in tail
                if ("Assertion" in l or "exceeded" in l
                    or ("!!" in l and "public domain" not in l))), "")
    return False, (why or f"exit {r.returncode}")[:90]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # No choices= here: argparse validates the empty default against it and
    # rejects "no categories given" as an invalid choice.
    ap.add_argument("categories", nargs="*",
                    help="rocks trees plants houses; default is all of them")
    ap.add_argument("--per", type=int, default=8, help="models per category")
    ap.add_argument("--max-mb", type=float, default=40.0)
    ap.add_argument("--any-licence", action="store_true",
                    help="include royalty_free; fine for a free mod, not for a paid one")
    ap.add_argument("--plan", action="store_true", help="list what would be fetched")
    ap.add_argument("--retry", action="store_true",
                    help="re-convert everything the manifest records as failed, using "
                         "the .glb already on disk. Use after fixing the converter.")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if args.retry:
        man = json.loads(MANIFEST.read_text(encoding="utf8")) if MANIFEST.is_file() else {}
        todo = [k for k, v in man.items() if not v.get("installed")]
        print(f"retrying {len(todo)} previously failed model(s)")
        ok = fail = 0
        for name in todo:
            glb = OUT / f"{name}.glb"
            if not glb.is_file():
                print(f"-- {name}: no .glb on disk, skipped")
                continue
            print(f"\n-- {name}")
            good, why = convert(glb, no_instancing=(man[name].get("category") == "houses"))
            print(f"   {'OK  ' if good else 'FAIL'}  {why}")
            man[name]["installed"] = good
            man[name]["verdict"] = why
            man[name]["stamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
            ok += good
            fail += (not good)
            MANIFEST.write_text(json.dumps(man, indent=2), encoding="utf8")
        print(f"\n{ok} newly installed, {fail} still failing")
        return 0

    bad = [c for c in args.categories if c not in CATEGORIES]
    if bad:
        print(f"  !! unknown category: {', '.join(bad)}. "
              f"Choose from {', '.join(CATEGORIES)}.")
        return 2
    cats = args.categories or list(CATEGORIES)
    manifest = json.loads(MANIFEST.read_text(encoding="utf8")) if MANIFEST.is_file() else {}
    seen = {slug(k) for k in manifest}
    rows_by_cat = {}

    for cat in cats:
        rows, skipped = candidates(CATEGORIES[cat], args.per,
                                   not args.any_licence, args.max_mb, seen)
        rows_by_cat[cat] = rows
        print(f"\n== {cat}: {len(rows)} usable "
              f"(skipped {skipped['no_gltf']} with no glTF, "
              f"{skipped['too_big']} over {args.max_mb:g} MB)")
        for r in rows:
            print(f"   {r['mb']:>6.1f} MB  {r['licence']:<14} {r['title'][:42]}")

    if not args.apply:
        print("\n(plan only -- pass --apply to download, convert and verify)")
        return 0

    ok = fail = 0
    for cat, rows in rows_by_cat.items():
        for r in rows:
            print(f"\n-- [{cat}] {r['title']}  ({r['mb']} MB, {r['licence']})")
            glb = download(r)
            if not glb:
                fail += 1
                continue
            # A house is usually placed once or twice; 2.3.1.3 case 1 says a rarely
            # used model needs no instancing. Rocks and plants repeat everywhere.
            good, why = convert(glb, no_instancing=(cat == "houses"))
            print(f"   {'OK  ' if good else 'FAIL'}  {why}")
            manifest[r["name"]] = {**r, "category": cat, "installed": good,
                                   "verdict": why,
                                   "stamp": time.strftime("%Y-%m-%d %H:%M:%S")}
            ok += good
            fail += (not good)
            MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf8")

    print(f"\n{ok} installed and verified, {fail} failed")
    print(f"manifest: {MANIFEST}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
