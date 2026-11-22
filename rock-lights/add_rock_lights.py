"""Add a Rock Lights kit (eight 2 in LED pods, five colours) to any truck in Expeditions: A MudRunner Game.

TWO WAYS IN

  Your own truck, in the editor's Media folder (creators):
      python add_rock_lights.py --truck my_truck
  An installed truck mod, on your own PC (players):
      python add_rock_lights.py --truck their_truck --mod-dir "<Documents>\\My Games\\Expeditions\\base\\Mods\\.modio\\mods\\<id>"

  --plan     print where the pods go and change nothing
  --remove   take the kit out again (only what this tool added)

WHAT IT DOES
  1. Reads the truck class (wheel positions) and its converted mesh (frame and floor geometry).
  2. Places four pods a side from the wheels and mounts each one on the truck's own steel (pods.py).
  3. Builds the pod mesh in Blender and runs the game's own converter on it, plus the pod texture.
  4. Writes five add-ons, Rock Lights - White / Amber / Red / Blue / Green (650 each, Miscellaneous menu,
     on whenever the engine runs), and adds one empty RockLights<Truck> slot to the truck class.

  Creator mode writes into Media (classes/trucks, meshes/trucks, textures/trucks, prebuild); pack your mod
  as usual. Mod-dir mode edits that mod's paks in place, after one backup of each (*.rocklights_bak).

SHARING: changing a truck on your own PC is yours to do. Uploading someone else's truck with your changes
needs their permission or their licence. A mod.io update of that mod replaces the paks: run this again.

Needs: Python 3.10+, numpy, Pillow, Blender 4.x/5.x, and the Expeditions map editor installed (its
ResourceConverter does the conversion). Close the game while it runs.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pods  # noqa: E402
from saber import convert as cv, gamepaths, tga  # noqa: E402

MARK = "<!-- rock lights (add_rock_lights.py) -->"
BOM = b"\xef\xbb\xbf"
TEX = "rock_lights_pod"
MAT = f"{TEX}_mat"
PRICE, ATTEN, INNER, OUTER, GROUP = 650, 7.0, 60, 150, "Ignition"
COLOURS = {   # key: (ui name, colour, multiplier) -- the values proven in game on every vanilla truck
    "white": ("White", (235, 242, 255), 25.2),
    "amber": ("Amber", (255, 160, 30), 26.4),
    "red": ("Red", (255, 25, 15), 30.0),
    # 1.0.1: blue 30 -> 80. Pure blue reads darkest to the eye; at 30 it was the dimmest kit by far.
    "blue": ("Blue", (40, 90, 255), 80.0),
    "green": ("Green", (40, 255, 70), 25.2),
}
ATLAS = [(24, 24, 26), (214, 218, 222)]   # 0 black powder coat, 1 frosted lens


# ---------------------------------------------------------------- names
def camel(name: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[_\-\s]+", name) if p)


def socket(truck: str) -> str:
    return f"RockLights{camel(truck)}"


def mesh_name(truck: str) -> str:
    return f"rock_lights_pods_{truck}"


def kit_name(truck: str, key: str) -> str:
    return f"{truck}_rock_lights_{key}"


def f3(v) -> str:
    return "(" + "; ".join(f"{x:.3f}".rstrip("0").rstrip(".") if abs(x) > 1e-9 else "0" for x in v) + ")"


# ---------------------------------------------------------------- the class XML
def truck_mesh_ref(xml: str) -> str:
    m = re.search(r'<PhysicsModel[^>]*Mesh="([^"]+)"', xml) or re.search(r'Mesh="([^"]+)"', xml)
    if not m:
        raise SystemExit("the truck class has no PhysicsModel Mesh=...")
    return m.group(1)


def add_socket(xml: str, truck: str, frame: str | None) -> str:
    if MARK in xml:
        return xml
    i = xml.find("<Truck")
    j = xml.find("</GameData>", i)
    if i < 0 or j < 0:
        raise SystemExit("no <Truck> ... </GameData> in the truck class")
    nl = "\r\n" if "\r\n" in xml else "\n"
    pf = f' ParentFrame="{frame}"' if frame else ""
    block = (f"\t{MARK}{nl}\t\t<AddonSockets>{nl}\t\t\t<Socket Names=\"{socket(truck)}\"{pf} />{nl}"
             f"\t\t</AddonSockets>{nl}\t")
    return xml[:j] + block + xml[j:]


def remove_socket(xml: str) -> str:
    i = xml.find(MARK)
    if i < 0:
        return xml
    start = xml.rfind("\t", 0, i)
    start = i if start < 0 or "\n" in xml[start:i] else start
    end = xml.find("</AddonSockets>", i) + len("</AddonSockets>")
    tail = re.match(r"[\r\n\t ]*", xml[end:]).end()
    return xml[:start] + xml[end:][tail:] if tail else xml[:start] + xml[end:]


def kit_xml(truck: str, key: str, frame: str | None, lights) -> str:
    ui, (r, g, b), k = COLOURS[key]
    pf = f' ParentFrame="{frame}"' if frame else ""
    rows = [f'\t\t\t<Light _template="IgnitionSignalWhite" Color="g({r}; {g}; {b}) x {k}" Pos="{f3(p)}" '
            f'Dir="{f3(d)}" AttenEnd="{ATTEN}" InnerCone="{INNER}" OuterCone="{OUTER}"{pf} />' for p, d in lights]
    rows += [f'\t\t\t<Flare _template="IgnitionSignalWhite" Color="g({r}; {g}; {b}) x 0.9" Size="0.05" '
             f'Pos="{f3(p - d * 0.011)}" Dir="{f3(d)}" DirAngle="170" ColorMultAtDay="0.4"{pf} />' for p, d in lights]
    return f"""<TruckAddon>
\t<PhysicsModel Mesh="trucks/{mesh_name(truck)}" />
\t<ModelAttachments>
\t\t<{GROUP}>
{chr(10).join(rows)}
\t\t</{GROUP}>
\t</ModelAttachments>
\t<GameData
\t\tCameraPreset="default"
\t\tCategory="miscellenious"
\t\tPrice="{PRICE}"
\t\tUnlockByExploration="false"
\t\tUnlockByRank="1"
\t>
\t\t<UiDesc UiDesc="Eight 2 in {ui.lower()} LED rock-light pods lighting the ground round the truck: one in each wheel well and four under the frame. On whenever the engine runs." UiIcon30x30="" UiIcon40x40="" UiName="Rock Lights - {ui}" />
\t\t<InstallSocket Type="{socket(truck)}" />
\t</GameData>
</TruckAddon>
"""
# NO <Body> under the PhysicsModel: on these kits a Body made the garage fit fail silently (the kit is
# listed, highlights, and Buy does nothing). If a highlighted kit does not show on the truck, that is it.


# ---------------------------------------------------------------- building the pod mesh + texture
def atlas() -> np.ndarray:
    a = np.zeros((256, 256, 4), np.uint8)
    a[..., 3] = 255
    a[:, :] = (*ATLAS[0], 255)
    for i, c in enumerate(ATLAS):
        a[(i // 4) * 64:(i // 4) * 64 + 64, (i % 4) * 64:(i % 4) * 64 + 64] = (*c, 255)
    return a


def write_textures(root: Path) -> list[Path]:
    d = root / "textures" / "trucks"
    d.mkdir(parents=True, exist_ok=True)
    flat = np.zeros((256, 256, 4), np.uint8)
    flat[:] = (128, 128, 255, 255)
    return [tga.create(d / f"{TEX}__d.tga", atlas()), tga.create(d / f"{TEX}__n_d.tga", flat)]


def convert_textures(root: Path) -> list[Path]:
    """TGA -> .pct with the game's converter (absolute paths: it resolves -r/-o against its own folder)"""
    root = root.resolve()
    conv, cwd = gamepaths.resource_converter(), gamepaths.bin_editor()
    if not conv or not Path(conv).is_file():
        raise SystemExit("ResourceConverter not found: install the Expeditions editor (or set SABER_GAME)")
    out = []
    for stem in (f"{TEX}__d", f"{TEX}__n_d"):
        o = root / "prebuild" / "textures" / "pct" / f"trucks_{stem}.pct"
        o.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(conv), "--mod-convert", "-s", "--all-pct", "--make-pct-headers",
                        "-i", f"textures/trucks/{stem}.tga", "-o", str(o), "-r", str(root)],
                       capture_output=True, text=True, timeout=300, cwd=str(cwd))
        if not o.is_file():
            raise SystemExit(f"texture conversion failed for {stem}")
        out += [o] + ([o.with_suffix(".pct_header")] if o.with_suffix(".pct_header").is_file() else [])
    return out


def build_mesh(truck: str, mounts, media: Path) -> Path:
    """pods -> FBX (Blender) -> converted mesh in Media/prebuild/meshes/trucks_<name>"""
    name = mesh_name(truck)
    fbx_dir = media / "meshes" / "trucks"
    fbx_dir.mkdir(parents=True, exist_ok=True)
    write_textures(media)
    (fbx_dir / f"{name}.xml").write_text(
        f'<CombineXMesh>\n\t<Material Name="{MAT}" AlbedoMap="trucks/{TEX}__d.tga" '
        f'NormalMap="trucks/{TEX}__n_d.tga" />\n</CombineXMesh>\n', encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        npz, png, sp = Path(tmp) / "pods.npz", Path(tmp) / "atlas.png", Path(tmp) / "spec.json"
        pods.pod_mesh(mounts).save(npz)
        from PIL import Image
        Image.fromarray(atlas()[..., :3]).save(png)
        sp.write_text(json.dumps({"parts": str(npz), "atlas_png": str(png), "material": MAT, "name": name,
                                  "fbx_dir": str(fbx_dir)}), encoding="utf-8")
        r = subprocess.run([str(gamepaths.blender()), "-b", "--factory-startup", "-P",
                            str(HERE / "pods_blender.py"), "--", str(sp)],
                           capture_output=True, text=True, timeout=1800)
    if r.returncode or "REPORT " not in r.stdout:
        print(r.stdout[-3000:], r.stderr[-2000:])
        raise SystemExit("Blender export of the pods failed")
    res = cv.convert(name, "trucks")
    if not res["ok"]:
        print("\n".join(cv.interesting(res["log"])) or res["log"][-3000:])
        raise SystemExit(f"the game's converter refused {name} (code {res['code']})")
    print(f"  pods converted: {res['output']} ({res['size']:,d} B)")
    return Path(res["output"])


# ---------------------------------------------------------------- planning
def show(truck: str, P: dict) -> None:
    names = [n for n, *_ in pods.LAYOUT]
    print(f"{truck}: lights and slot on frame {P['frame'] or '(truck root: no BoneChassis_cdt)'}")
    for n, (p, _a, d, l, found) in zip(names, P["mounts"]):
        how = f"bracket {'up' if d[1] > 0.5 else 'in'} {l * 100:.0f} cm" if found else "NO STEEL in reach: short stem"
        print(f"  {n:12s} at {np.round(p, 3)}  {how}")


def game_running() -> bool:
    out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout if sys.platform == "win32" else ""
    return bool(re.search(r"^Expeditions\.exe\s", out, re.M | re.I))


# ---------------------------------------------------------------- creator mode (Media)
def media_mode(truck: str, media: Path, a) -> None:
    real = gamepaths.media_root()
    if media.resolve() != real.resolve():
        raise SystemExit(f"the game's converter only works in the editor's own Media folder ({real}); "
                         "point the tools at another install with SABER_GAME or toolkit.json instead of --media")
    cls = media / "classes" / "trucks" / f"{truck}.xml"
    if not cls.is_file():
        raise SystemExit(f"no {cls}")
    raw = cls.read_bytes()
    bom = raw.startswith(BOM)
    xml = raw.decode("utf-8-sig")
    save = lambda text: cls.write_bytes((BOM if bom else b"") + text.encode("utf-8"))   # as it was
    kits = media / "classes" / "trucks" / "rock_lights"
    if a.remove:
        save(remove_socket(xml))
        for k in COLOURS:
            (kits / f"{kit_name(truck, k)}.xml").unlink(missing_ok=True)
        for f in (media / "meshes" / "trucks" / f"{mesh_name(truck)}.fbx",
                  media / "meshes" / "trucks" / f"{mesh_name(truck)}.xml",
                  media / "prebuild" / "meshes" / f"trucks_{mesh_name(truck)}"):
            f.unlink(missing_ok=True)
        print(f"removed the Rock Lights kit from {truck} (the shared pod texture stays)")
        return
    ref = truck_mesh_ref(xml)
    blob_path = media / "prebuild" / "meshes" / ref.replace("/", "_")
    if not blob_path.is_file():
        raise SystemExit(f"{blob_path} is missing: open the truck in the editor once so it converts it")
    P = pods.plan(xml, blob_path.read_bytes())
    show(truck, P)
    if a.plan:
        return
    build_mesh(truck, P["mounts"], media)
    convert_textures(media)
    kits.mkdir(parents=True, exist_ok=True)
    L = pods.lights(P["mounts"])
    for k in COLOURS:
        (kits / f"{kit_name(truck, k)}.xml").write_bytes(kit_xml(truck, k, P["frame"], L).encode("utf-8"))
    bak = cls.with_name(cls.name + ".rocklights_bak")
    if not bak.exists():
        shutil.copy2(cls, bak)
    save(add_socket(xml, truck, P["frame"]))
    print(f"done: 5 kits in {kits}, slot {socket(truck)} on {cls.name}. Pack the mod as usual.")


# ---------------------------------------------------------------- installed-mod mode (paks)
def find_member(names: list[str], tail: str) -> str | None:
    hits = [n for n in names if n == tail or n.endswith("/" + tail)]
    return hits[0] if hits else None


def rewrite(pak: Path, put: dict[str, bytes], drop: set[str]) -> None:
    """copy the pak with members replaced/added/removed (DEFLATE, like every mod pak); swap in at the end"""
    tmp = pak.with_name(pak.name + ".rocklights_tmp")
    with zipfile.ZipFile(pak) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if info.filename in put or info.filename in drop:
                continue
            dst.writestr(info.filename, src.read(info), compress_type=info.compress_type)
        for n, b in put.items():
            dst.writestr(n, b, compress_type=zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(tmp) as z:
        if z.testzip() is not None or any(z.read(n) != b for n, b in put.items()):
            tmp.unlink()
            raise SystemExit(f"{pak.name} did not read back -- nothing changed")
    tmp.replace(pak)


def mod_mode(truck: str, mod: Path, a) -> None:
    paks = [p for p in mod.glob("*.pak") if p.name.lower() != "pc.pak"]
    main = next((p for p in paks if find_member(zipfile.ZipFile(p).namelist(), f"classes/trucks/{truck}.xml")), None)
    if not main:
        raise SystemExit(f"no classes/trucks/{truck}.xml in any pak in {mod}")
    pc = mod / "pc.pak"
    names = zipfile.ZipFile(main).namelist()
    cls = find_member(names, f"classes/trucks/{truck}.xml")
    kit_dir = cls.rsplit("/", 1)[0] + "/rock_lights"
    kit_members = {f"{kit_dir}/{kit_name(truck, k)}.xml" for k in COLOURS}
    mesh_member = f"prebuild/meshes/trucks_{mesh_name(truck)}"
    xml = zipfile.ZipFile(main).read(cls).decode("utf-8-sig")
    if a.remove:
        rewrite(main, {cls: remove_socket(xml).encode("utf-8")}, kit_members | {mesh_member})
        with zipfile.ZipFile(main) as z:
            others = [n for n in z.namelist() if "/rock_lights/" in n and n.endswith(".xml")]
        if not others and pc.is_file():   # the pod texture is shared by every kit in this mod: last one out
            with zipfile.ZipFile(pc) as z:
                tex = {n for n in z.namelist() if f"/trucks_{TEX}__" in n}
            if tex:
                rewrite(pc, {}, tex)
        print(f"removed the Rock Lights kit from {truck} in {main.name}")
        return
    ref = truck_mesh_ref(xml)
    blob = None
    for p in [main, pc] + paks:
        if p.is_file():
            m = find_member(zipfile.ZipFile(p).namelist(), "prebuild/meshes/" + ref.replace("/", "_"))
            if m:
                blob = zipfile.ZipFile(p).read(m)
                break
    if blob is None:
        sh = gamepaths.game_dir() and Path(gamepaths.game_dir()) / "preload" / "paks" / "client" / "shared.pak"
        if sh and sh.is_file() and ("[meshes]/" + ref.replace("/", "_")) in zipfile.ZipFile(sh).namelist():
            blob = zipfile.ZipFile(sh).read("[meshes]/" + ref.replace("/", "_"))   # a mod on a vanilla body
    if blob is None:
        raise SystemExit(f"the truck's converted mesh ({ref}) is not in the mod or the game")
    P = pods.plan(xml, blob)
    show(truck, P)
    if a.plan:
        return
    media = gamepaths.media_root()
    tgas = [media / "textures" / "trucks" / f"{TEX}{k}.tga" for k in ("__d", "__n_d")]
    had = {t for t in tgas if t.exists()}
    out = build_mesh(truck, P["mounts"], media)
    put = {mesh_member: out.read_bytes(), cls: add_socket(xml, truck, P["frame"]).encode("utf-8")}
    L = pods.lights(P["mounts"])
    for k in COLOURS:
        put[f"{kit_dir}/{kit_name(truck, k)}.xml"] = kit_xml(truck, k, P["frame"], L).encode("utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        write_textures(Path(tmp))
        tex = {f"prebuild/textures/pct/{f.name}": f.read_bytes() for f in convert_textures(Path(tmp))}
    for p in (main, pc):
        bak = p.with_name(p.name + ".rocklights_bak")
        if p.is_file() and not bak.exists():
            shutil.copy2(p, bak)
    rewrite(main, put, set())
    if pc.is_file():
        rewrite(pc, tex, set())
    else:
        with zipfile.ZipFile(pc, "w", zipfile.ZIP_DEFLATED) as z:
            for n, b in tex.items():
                z.writestr(n, b)
    # leave nothing behind in the editor's Media
    for f in (media / "meshes" / "trucks" / f"{mesh_name(truck)}.fbx", media / "meshes" / "trucks" / f"{mesh_name(truck)}.xml", out,
              *[t for t in tgas if t not in had]):
        Path(f).unlink(missing_ok=True)
    print(f"done: 5 kits + slot {socket(truck)} in {main.name}, pod texture in pc.pak (backups *.rocklights_bak)")


def trucks_in(media: Path | None, mod: Path | None) -> list[str]:
    """truck classes a user can pick from (not add-ons)"""
    out = []
    if mod:
        for p in mod.glob("*.pak"):
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    m = re.search(r"classes/trucks/([^/]+)\.xml$", n)
                    if m and b"<Truck" in z.read(n)[:4000] and b"<TruckAddon" not in z.read(n)[:4000]:
                        out.append(m.group(1))
    elif media:
        for f in (media / "classes" / "trucks").glob("*.xml"):
            head = f.read_text(encoding="utf-8-sig", errors="ignore")[:4000]
            if "<Truck" in head and "<TruckAddon" not in head:
                out.append(f.stem)
    return sorted(set(out))


def ask(prompt: str, options: list[str]) -> str:
    for i, o in enumerate(options, 1):
        print(f"  {i:2d}. {o}")
    while True:
        v = input(f"{prompt} (number, or Enter to quit): ").strip()
        if not v:
            raise SystemExit("nothing changed")
        if v.isdigit() and 1 <= int(v) <= len(options):
            return options[int(v) - 1]


def interactive(a) -> None:
    """no arguments: ask, step by step (double-click friendly)"""
    print("Rock Lights for Expeditions -- adds a five-colour rock-light kit to one truck.\n")
    where = ask("Which truck?", ["my own truck, in the editor's Media folder",
                                 "an installed truck mod in my mods folder"])
    if where.startswith("an installed"):
        mods = gamepaths.media_root().parent / "base" / "Mods" / ".modio" / "mods"
        dirs = [d for d in sorted(mods.glob("*")) if d.is_dir() and trucks_in(None, d)] if mods.is_dir() else []
        if not dirs:
            raise SystemExit(f"no truck mods found in {mods}; run with --mod-dir <folder>")
        label = {f"{d.name}  ({', '.join(trucks_in(None, d))})": d for d in dirs}
        a.mod_dir = str(label[ask("Which mod?", list(label))])
        names = trucks_in(None, Path(a.mod_dir))
    else:
        names = trucks_in(Path(a.media) if a.media else gamepaths.media_root(), None)
        if not names:
            raise SystemExit("no truck classes found in Media\\classes\\trucks")
    a.truck = names[0] if len(names) == 1 else ask("Which truck?", names)
    a.remove = ask("Add or remove the kit?", ["add Rock Lights", "remove Rock Lights"]).startswith("remove")
    if not a.remove:
        a.plan = True
        run(a)                                    # show the placement first
        if input("\nBuild and install it now? (y/N): ").strip().lower() != "y":
            raise SystemExit("nothing changed")
        a.plan = False
    run(a)


def run(a) -> None:
    if not a.plan and game_running():
        raise SystemExit("Expeditions is running: close the game first")
    if a.mod_dir:
        mod_mode(a.truck, Path(a.mod_dir), a)
    else:
        media_mode(a.truck, Path(a.media) if a.media else gamepaths.media_root(), a)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Add a Rock Lights kit to a truck (Expeditions). "
                                             "Run with no arguments to be asked step by step.")
    ap.add_argument("--truck", help="the truck's class name (its classes/trucks/<name>.xml)")
    ap.add_argument("--media", help="the editor's Media folder (default: found automatically; must be the editor's own)")
    ap.add_argument("--mod-dir", help="an installed truck mod's folder (.modio\\mods\\<id>) instead of Media")
    ap.add_argument("--plan", action="store_true", help="print the pod placement and change nothing")
    ap.add_argument("--remove", action="store_true", help="take the kit out again")
    a = ap.parse_args(argv)
    try:
        interactive(a) if not a.truck else run(a)
    finally:
        if not a.truck or len(sys.argv) == 1:
            input("\nPress Enter to close.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
