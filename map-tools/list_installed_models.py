"""list_installed_models.py -- what custom models are in the editor, and what to type.

The Select Asset window searches by the model's base name. A custom model is shown
with a "\\Media" suffix so you can tell it from a shipped asset of the same name
(Custom_Level_Entities 2.2.6), but you still SEARCH for the bare name.

Source of truth is the Media folder, not the manifest: a model counts as installed
when all three files exist.

    Media\\meshes\\models\\<name>.fbx
    Media\\meshes\\models\\<name>.xml
    Media\\classes\\models\\<name>.xml

The manifest adds provenance (title, licence, author) for anything fetched by
`stock_models.py`. Models installed by hand before the manifest existed still get
listed, marked with an unknown source, because the editor will show them either way.

    python list_installed_models.py                 # print
    python list_installed_models.py --write         # also write F:\\Expeditions\\installed-models.md
    python list_installed_models.py --verify        # re-run ResourceConverter on each (slow)
"""
from __future__ import annotations

import argparse
import json
import re
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from saber import project
import saber  # noqa: F401  (puts ../fob-modules on the path)
import glb_to_fbx as gf

MEDIA = project.MEDIA
MESHES = MEDIA / "meshes" / "models"
CLASSES = MEDIA / "classes" / "models"
TEXTURES = MEDIA / "textures" / "models"
MANIFEST = Path(__file__).parent / "models" / "blendkit" / "manifest.json"
OUT_MD = Path(os.environ.get("INSTALLED_MODELS_MD", "installed-models.md"))

ORDER = ["rocks", "trees", "plants", "houses", "other"]
PRETTY = {"rocks": "Rocks and boulders", "trees": "Trees", "plants": "Plants and bushes",
          "houses": "Buildings, houses and cabins", "other": "Other"}


def installed_names() -> list[str]:
    out = []
    for fbx in sorted(MESHES.glob("*.fbx")):
        base = fbx.stem
        if (MESHES / f"{base}.xml").is_file() and (CLASSES / f"{base}.xml").is_file():
            out.append(base)
    return out


def read_mesh_xml(base: str) -> dict:
    p = MESHES / f"{base}.xml"
    t = p.read_text(encoding="utf8", errors="replace") if p.is_file() else ""
    mats = re.findall(r'<Material\b[^>]*Name="([^"]+)"', t)
    lods = re.search(r'Distances="([^"]*)"', t)
    n_lod = len([x for x in (lods.group(1).split(",") if lods else []) if x.strip()]) + 1
    return {"materials": len(mats), "lods": n_lod}


def read_class_xml(base: str) -> dict:
    p = CLASSES / f"{base}.xml"
    t = p.read_text(encoding="utf8", errors="replace") if p.is_file() else ""
    col = re.search(r'Collisions="([^"]+)"', t)
    return {"collisions": col.group(1) if col else "-",
            "instanced": 'Instanced="true"' in t}


def texture_count(base: str) -> int:
    return len(list(TEXTURES.glob(f"{base}_*.tga"))) + len(list(TEXTURES.glob(f"{base}__*.tga")))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help=f"write {OUT_MD}")
    ap.add_argument("--verify", action="store_true",
                    help="re-run ResourceConverter over every model (slow but definitive)")
    args = ap.parse_args()

    man = json.loads(MANIFEST.read_text(encoding="utf8")) if MANIFEST.is_file() else {}
    names = installed_names()
    rows = []
    GLB = Path(__file__).parent / "models" / "blendkit"
    KEYWORD = [("houses", r"cabin|house|shack|barn|cottage|hut|building|ruin"),
               ("trees", r"tree|pine|fir|spruce|palm|juniper|oak"),
               ("plants", r"bush|shrub|plant|grass|cactus|fern|flower"),
               ("rocks", r"rock|boulder|stone|cliff|sandstone")]

    for base in names:
        m = dict(man.get(base, {}))
        if not m:
            # Installed by hand before the manifest existed: the .json sidecar that
            # fetch_blendkit writes beside each download still has the provenance.
            # Look in EVERY source folder, not just blendkit/ -- sketchfab/ models
            # are CC-BY, so losing the sidecar here loses a credit that legally has
            # to ship with the mod, and it reported "unknown" until this was fixed.
            side = next((p for p in GLB.parent.glob(f"*/{base}.json")), None)
            if side and side.is_file():
                try:
                    d = json.loads(side.read_text(encoding="utf8"))
                    m = {"title": d.get("name") or d.get("title"),
                         "licence": d.get("license"), "author": d.get("author")}
                except Exception:                 # noqa: BLE001
                    pass
        if not m.get("category"):
            m["category"] = next((c for c, pat in KEYWORD
                                  if re.search(pat, base, re.I)), "other")
        row = {"name": base, "title": m.get("title") or base.replace("_", " ").title(),
               "category": m.get("category") or "other",
               "licence": m.get("licence") or "unknown",
               "author": m.get("author") or "unknown",
               "verdict": m.get("verdict") or "not recorded",
               **read_mesh_xml(base), **read_class_xml(base),
               "textures": texture_count(base)}
        if args.verify:
            code, out = gf.run_converter(base)
            real = [l for l in out.splitlines()
                    if l.strip() and not any(n in l for n in gf.NOISE)]
            row["verdict"] = ("clean" if code == 0 and not real
                              else ("ASSERT" if any("Assertion" in l for l in real)
                                    else f"exit {code}"))
        rows.append(row)

    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)

    L = []
    A = L.append
    A("# Custom models installed in the editor")
    A("")
    A(f"**{len(rows)} models.** Type the name into the editor's Select Asset search box. "
      "A custom model appears there with a `\\Media` suffix, so `forest_cabin` shows as "
      "`forest_cabin\\Media`. Search the bare name.")
    A("")
    A("Files live in `Documents\\My Games\\Expeditions\\Media\\` under `meshes\\models`, "
      "`classes\\models` and `textures\\models`.")
    A("")
    for cat in ORDER + [c for c in by_cat if c not in ORDER]:
        rs = sorted(by_cat.get(cat, []), key=lambda r: r["name"])
        if not rs:
            continue
        A(f"## {PRETTY.get(cat, cat.title())}  ({len(rs)})")
        A("")
        A("| Search for | What it is | Materials | LODs | Textures | Collision | Licence |")
        A("|---|---|---|---|---|---|---|")
        for r in rs:
            A(f"| `{r['name']}` | {r['title']} | {r['materials']} | {r['lods']} | "
              f"{r['textures']} | {r['collisions']} | {r['licence']} |")
        A("")

    unverified = [r for r in rows if r["verdict"] not in ("clean",) and
                  not r["verdict"].startswith("clean")]
    if unverified:
        A("## Not confirmed clean")
        A("")
        A("These are on disk but the converter has not signed them off in this run. "
          "They may still load; they have not been proven to.")
        A("")
        for r in unverified:
            A(f"- `{r['name']}` -- {r['verdict']}")
        A("")

    A("## Attribution")
    A("")
    A("Everything here came from blendkit.com. `cc_zero` is public domain and needs "
      "nothing. `royalty_free` allows commercial use but forbids re-selling the asset "
      "in the same form, including as part of a game level on a marketplace "
      "(<https://www.blenderkit.com/docs/licenses/>). A free mod is fine; a paid one is not.")
    A("")
    A("| Model | Author | Licence |")
    A("|---|---|---|")
    for r in sorted(rows, key=lambda r: (r["licence"], r["name"])):
        A(f"| `{r['name']}` | {r['author']} | {r['licence']} |")
    A("")

    text = "\n".join(L)
    print(text if not args.write else text[:1200] + "\n...")
    if args.write:
        OUT_MD.parent.mkdir(parents=True, exist_ok=True)
        OUT_MD.write_text(text, encoding="utf8")
        print(f"\nwrote {OUT_MD}  ({len(rows)} models)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
