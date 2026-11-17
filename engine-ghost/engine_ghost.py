"""Place a truck's engine ghost: the see-through engine the game shows at start-up and on engine damage.

The game draws the ghost (EngineIconMesh, scaled by EngineIconScale) at the ENGINE SOUND emitter, the
truck's <Sounds Origin="..."> -- read in Expeditions.exe: the drawer takes the world transform of the
truck's engine-sound object, which the class parser builds from <Sounds Origin> and <Sounds ParentFrame>.
<Steam Origin> and the engine <DamageArea> do NOT move it (both tried in game). Carried over from another
truck, the sound origin is usually wrong for the new body, and the ghost floats out of the nose or off
to one side.

This tool reads the truck's own converted mesh, finds its engine bay (windshield base to radiator,
under the hood line), and fits the ghost in it:

    python engine_ghost.py --mod "<...>/Mods/.modio/mods/<id>" --truck my_truck            # report only
    python engine_ghost.py --mod ... --truck my_truck --preview bay.png                     # + a picture
    python engine_ghost.py --mod ... --truck my_truck --apply                               # write the suggestion
    python engine_ghost.py --mod ... --truck my_truck --apply --raise 0.04                  # then nudge
    python engine_ghost.py --mod ... --truck my_truck --apply --set 1.12 0.89 0             # or set it exactly

--truck is a class name: classes/trucks/<name>.xml, or a truck replacement (a suspension that swaps
the truck, e.g. a lift kit: classes/truck_replacements/<name>.xml), which carries its own ghost.
--media <editor Media folder> works on an unpacked project instead of an installed mod.

What it can and cannot judge: the front-to-back place is measured and reliable. The height is a
starting point (the ghost's top --gap under the hood line); how low an engine should sit is a look,
so check it in game and nudge with --raise / --lower. The game must be closed when you --apply.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import meshread  # noqa: E402

# generic ghost meshes in shared.pak, bounds about their origin (min xyz, max xyz), engine space
# (x forward, y up, z side). Read from the game when it is there; these are the measured fallback.
GHOSTS = {
    "env/engine_small": (-0.276, -0.33, -0.219, 0.38, 0.312, 0.336),
    "env/engine_default": (-0.4, -0.428, -0.354, 0.716, 0.23, 0.312),
    "env/engine_big": (-0.482, -0.428, -0.452, 0.985, 0.23, 0.493),
}
SKIP = ("glass", "windshield", "mirror", "cockpit", "gauge", "lp_cab", "driveline", "wheel", "tire", "tyre",
        "susp", "shadow", "cdt", "steering", "shaft")
BAND = 0.40          # half-width of the strip down the middle of the truck the hood line is read from (m)
BIN = 0.05           # profile step (m)
RISE = 0.08          # the windshield base: where the top rises this much above the hood (m)
FIREWALL = 0.10      # firewall this far ahead of the windshield base (m)
RADIATOR = 0.15      # radiator this far behind the nose (m)
GAP = 0.15           # suggested height: ghost top this far under the hood line (m)


# ------------------------------------------------------------------ XML
def fnum(v: float) -> str:
    return ("%.3f" % v).rstrip("0").rstrip(".") if abs(v) > 5e-4 else "0"


def fvec(v) -> str:
    return "(" + "; ".join(fnum(float(c)) for c in v) + ")"


def parse_vec(s: str) -> np.ndarray:
    return np.array([float(c) for c in s.strip("()").split(";")])


def read_xml(xml: str) -> dict:
    """the ghost's fields as the truck class has them"""
    g = lambda pat: (m.group(1) if (m := re.search(pat, xml, re.S)) else None)
    sounds = re.search(r"<Sounds\s[^>]*>", xml, re.S)
    out = {
        "mesh": g(r'EngineIconMesh="([^"]*)"') or "env/engine_default",
        "scale": float(g(r'EngineIconScale="([^"]*)"') or 1.0),
        "physics_mesh": g(r'<PhysicsModel[^>]*?\sMesh="([^"]*)"'),
        "steam": parse_vec(g(r'<Steam[^>]*?\sOrigin="([^"]*)"')) if g(r'<Steam[^>]*?\sOrigin="([^"]*)"') else None,
        "sound": None, "sound_parent": None,
    }
    if sounds:
        o = re.search(r'\sOrigin="([^"]*)"', sounds.group(0))
        p = re.search(r'\sParentFrame="([^"]*)"', sounds.group(0))
        out["sound"] = parse_vec(o.group(1)) if o else None
        out["sound_parent"] = p.group(1) if p else None
    return out


def set_sound(xml: str, at, steam: bool = False, scale: float | None = None, mesh: str | None = None) -> str:
    """the class text with the ghost moved (and optionally steam with it, a new scale or mesh)"""
    xml, n = re.subn(r'(<Sounds\s[^>]*?\sOrigin=")[^"]*(")', lambda m: m.group(1) + fvec(at) + m.group(2), xml, count=1)
    if n != 1:
        raise SystemExit("the truck has no <Sounds Origin=...> to move (add one to its <Sounds> element first)")
    if steam:
        xml, n = re.subn(r'(<Steam[^>]*?\sOrigin=")[^"]*(")', lambda m: m.group(1) + fvec(at) + m.group(2), xml, count=1)
        if n != 1:
            raise SystemExit("--steam: the truck has no <Steam Origin=...>")
    if scale is not None:
        xml, n = re.subn(r'EngineIconScale="[^"]*"', f'EngineIconScale="{fnum(scale)}"', xml, count=1)
        if n != 1:
            raise SystemExit("--scale: the truck has no EngineIconScale to change")
    if mesh is not None:
        xml, n = re.subn(r'EngineIconMesh="[^"]*"', f'EngineIconMesh="{mesh}"', xml, count=1)
        if n != 1:
            raise SystemExit("--ghost: the truck has no EngineIconMesh to change")
    return xml


# ------------------------------------------------------------------ the bay
def profile(P: np.ndarray, band: float = BAND, step: float = BIN) -> tuple[np.ndarray, np.ndarray]:
    """(x, top) of the strip down the middle of the truck, front to back"""
    c = P[np.abs(P[:, 2]) < band]
    if not len(c):
        raise SystemExit("no body geometry down the middle of the truck")
    xs = np.arange(np.floor(c[:, 0].min() / step) * step, c[:, 0].max() + step, step)
    X, T = [], []
    for a in xs:
        s = c[(c[:, 0] >= a) & (c[:, 0] < a + step)]
        if len(s):
            X.append(a + step / 2); T.append(s[:, 1].max())
    return np.array(X), np.array(T)


def bay(X: np.ndarray, T: np.ndarray, glass: np.ndarray | None = None, rise: float = RISE,
        firewall: float = FIREWALL, radiator: float = RADIATOR) -> dict:
    """the engine bay along the truck: nose, windshield base, firewall, radiator, hood line.

    hood line: the median top over the front 35 % of the truck.
    nose: the front-most body that reaches near hood height (a winch bumper or bull bar sits lower and
      further out: measured 11-17, one put a Jeep's front 18 cm ahead of its grille).
    windshield base: the front edge of the glass near the centre line from just under the hood line up
      (headlight lenses sit lower and further out);
      without glass, the first place behind the nose where the top rises `rise` above the hood."""
    order = np.argsort(-X); X, T = X[order], T[order]                  # front first
    span = X[0] - X[-1]
    hood = float(np.median(T[X > X[0] - 0.35 * span]))
    nose = float(X[np.nonzero(T >= hood - 0.20)[0][0]])
    base = None
    if glass is not None and len(glass):
        g = glass[(glass[:, 1] > hood - 0.02) & (glass[:, 0] < nose) & (np.abs(glass[:, 2]) < 0.45)]
        if len(g):
            base = float(g[:, 0].max())
    if base is None:
        for i in range(len(X) - 1):
            if X[i] > nose - 0.25:
                continue
            if T[i] > hood + rise and T[i + 1] > hood + rise:          # two bins in a row: not a vent or mirror
                base = float(X[i]); break
    if base is None:
        raise SystemExit("could not find the windshield base; give the bay with --bay X_FIREWALL X_RADIATOR")
    return {"nose": nose, "windshield": base, "firewall": base + firewall, "radiator": nose - radiator, "hood": hood}


def hood_line(X: np.ndarray, T: np.ndarray, x0: float, x1: float) -> float:
    s = T[(X >= x0) & (X <= x1)]
    return float(np.median(s)) if len(s) else float("nan")


def ghost_box(at, scale: float, gb) -> tuple[np.ndarray, np.ndarray]:
    gb = np.asarray(gb, float)
    return np.asarray(at, float) + gb[:3] * scale, np.asarray(at, float) + gb[3:] * scale


def suggest(X, T, B: dict, scale: float, gb, gap: float = GAP) -> np.ndarray:
    """the ghost centred between firewall and radiator, its top `gap` under the hood line, on the centre line"""
    gb = np.asarray(gb, float)
    mid = (B["firewall"] + B["radiator"]) / 2
    x = mid - scale * (gb[0] + gb[3]) / 2
    lo, hi = ghost_box((x, 0, 0), scale, gb)
    top = hood_line(X, T, lo[0], hi[0]) - gap
    return np.array([x, top - scale * gb[4], -scale * (gb[2] + gb[5]) / 2])   # the box, not the origin, centred


def fits(X, T, B: dict, at, scale: float, gb) -> list[str]:
    """what is wrong with the ghost at `at`, in words (empty: it sits in the bay)"""
    lo, hi = ghost_box(at, scale, gb)
    out = []
    if hi[0] > B["nose"]:
        out.append(f"pokes {hi[0] - B['nose']:.2f} m out of the nose")
    elif hi[0] > B["radiator"]:
        out.append(f"runs {hi[0] - B['radiator']:.2f} m past the radiator")
    if lo[0] < B["firewall"]:
        out.append(f"reaches {B['firewall'] - lo[0]:.2f} m behind the firewall")
    h = hood_line(X, T, lo[0], hi[0])
    if hi[1] > h:
        out.append(f"its top is {hi[1] - h:.2f} m above the hood line")
    if abs((lo[2] + hi[2]) / 2) > 0.05:
        out.append(f"sits {(lo[2] + hi[2]) / 2:+.2f} m off the centre line")
    return out


# ------------------------------------------------------------------ files
def game_running() -> bool:
    if sys.platform != "win32":
        return False
    out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout
    return bool(re.search(r"^Expeditions\.exe\s", out, re.M | re.I))


def ghost_bounds(mesh: str) -> tuple:
    """the ghost mesh's bounds: from the game's shared.pak when it can be found, else the measured table"""
    try:
        from saber import gamepaths
        sh = Path(gamepaths.game_dir()) / "preload" / "paks" / "client" / "shared.pak"
        name = "[meshes]/" + mesh.replace("/", "_")
        with zipfile.ZipFile(sh) as z:
            if name in z.namelist():
                return tuple(float(v) for v in meshread.bounds(z.read(name)))
    except Exception:
        pass
    if mesh in GHOSTS:
        return GHOSTS[mesh]
    raise SystemExit(f"{mesh}: not a generic ghost this tool knows, and the game's shared.pak was not found. "
                     "A truck-specific ghost (env/engine_<truck>) is modelled in its own truck's space: "
                     "switch to a generic one with --ghost env/engine_default")


class Source:
    """a truck class and its mesh, in an installed mod (paks) or an editor Media folder"""

    def __init__(self, truck: str, mod: Path | None = None, media: Path | None = None):
        self.truck, self.mod, self.media = truck, mod, media
        tails = (f"classes/trucks/{truck}.xml", f"classes/truck_replacements/{truck}.xml")
        if media:
            for t in tails:
                if (media / t).is_file():
                    self.xml_path = media / t
                    raw = self.xml_path.read_bytes()
                    self.bom = raw.startswith(b"\xef\xbb\xbf")
                    self.xml = raw.decode("utf-8-sig")
                    return
            raise SystemExit(f"no {' or '.join(tails)} under {media}")
        paks = sorted(mod.glob("*.pak")) if mod.is_dir() else [mod]
        for p in paks:
            with zipfile.ZipFile(p) as z:
                hit = next((n for n in z.namelist() for t in tails if n == t or n.endswith("/" + t)), None)
                if hit:
                    self.pak, self.member = p, hit
                    raw = z.read(hit)
                    self.bom = raw.startswith(b"\xef\xbb\xbf")
                    self.xml = raw.decode("utf-8-sig")
                    self.paks = paks if mod.is_dir() else [mod]
                    return
        raise SystemExit(f"no {' or '.join(tails)} in {mod}")

    def mesh_blob(self, ref: str) -> bytes:
        tail = "prebuild/meshes/" + ref.replace("/", "_")
        if self.media:
            p = self.media / tail
            if p.is_file():
                return p.read_bytes()
            raise SystemExit(f"{p} is missing: open the truck in the editor once so it converts its mesh")
        for p in [self.pak] + [q for q in self.paks if q != self.pak]:
            with zipfile.ZipFile(p) as z:
                hit = next((n for n in z.namelist() if n == tail or n.endswith("/" + tail)), None)
                if hit:
                    return z.read(hit)
        try:
            from saber import gamepaths
            sh = Path(gamepaths.game_dir()) / "preload" / "paks" / "client" / "shared.pak"
            with zipfile.ZipFile(sh) as z:
                return z.read("[meshes]/" + ref.replace("/", "_"))
        except Exception:
            raise SystemExit(f"the truck's mesh {ref} is not in the mod or the game")

    def write(self, xml: str) -> str:
        data = (b"\xef\xbb\xbf" if self.bom else b"") + xml.encode("utf-8")
        if self.media:
            bak = self.xml_path.with_name(self.xml_path.name + ".engine_ghost_bak")
            if not bak.exists():
                shutil.copy2(self.xml_path, bak)
            self.xml_path.write_bytes(data)
            return str(self.xml_path)
        tmp = self.pak.with_name(self.pak.name + ".engine_ghost_tmp")
        with zipfile.ZipFile(self.pak) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                body = data if info.filename == self.member else src.read(info)
                dst.writestr(info.filename, body, compress_type=info.compress_type)
        with zipfile.ZipFile(tmp) as z:
            if z.testzip() is not None or z.read(self.member) != data:
                tmp.unlink()
                raise SystemExit(f"{self.pak.name} did not read back -- nothing changed")
        tmp.replace(self.pak)
        return f"{self.pak.name}:{self.member}"


# ------------------------------------------------------------------ picture
def preview(path: Path, P: np.ndarray, X, T, B: dict, boxes: list[tuple[str, np.ndarray, np.ndarray, tuple]]) -> None:
    """side view (top) and plan view (bottom): body points grey, hood line blue, bay amber, ghosts as boxes"""
    from PIL import Image, ImageDraw
    W, H, pad = 1400, 1100, 40
    x0, x1 = P[:, 0].min() - 0.1, P[:, 0].max() + 0.1
    y0, y1 = P[:, 1].min() - 0.1, P[:, 1].max() + 0.1
    z0, z1 = P[:, 2].min() - 0.1, P[:, 2].max() + 0.1
    k = min((W - 2 * pad) / (x1 - x0), (H / 2 - 2 * pad) / max(y1 - y0, z1 - z0))
    img = Image.new("RGB", (W, H), (246, 246, 244)); d = ImageDraw.Draw(img)
    side = lambda x, y: (pad + (x1 - x) * k, H / 2 - pad - (y - y0) * k)            # nose to the left
    plan = lambda x, z: (pad + (x1 - x) * k, H - pad - (z - z0) * k)
    sub = P[np.random.default_rng(0).choice(len(P), min(len(P), 60000), replace=False)]
    for x, y, z in sub:
        d.point(side(x, y), fill=(150, 150, 150)); d.point(plan(x, z), fill=(170, 170, 170))
    d.line([side(x, t) for x, t in sorted(zip(X, T))], fill=(40, 90, 200), width=2)
    for xb, col in ((B["firewall"], (230, 150, 20)), (B["radiator"], (230, 150, 20)), (B["windshield"], (120, 120, 220))):
        d.line([side(xb, y0), side(xb, y1)], fill=col, width=1)
    for label, lo, hi, col in boxes:
        for f, a, b in ((side, 1, 1), (plan, 2, 2)):
            p, q = f(lo[0], lo[a]), f(hi[0], hi[b])
            d.rectangle([min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1])], outline=col, width=3)
        d.text((side(hi[0], hi[1])[0] + 4, side(hi[0], hi[1])[1] - 14), label, fill=col)
    d.text((pad, 10), "side view (nose left): grey body, blue hood line, amber bay (firewall | radiator)", fill=(40, 40, 40))
    d.text((pad, H / 2 + 6), "plan view (nose left)", fill=(40, 40, 40))
    img.save(path)


# ------------------------------------------------------------------ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--mod", type=Path, help="an installed mod's folder (or one .pak)")
    src.add_argument("--media", type=Path, help="the editor's Media folder (an unpacked project)")
    ap.add_argument("--truck", required=True, help="the truck class name (or a truck replacement's)")
    ap.add_argument("--apply", action="store_true", help="write the new place into the class (game closed)")
    ap.add_argument("--set", nargs=3, type=float, metavar=("X", "Y", "Z"), help="put the ghost exactly here")
    ap.add_argument("--raise", dest="up", type=float, default=0.0, help="move it up (m), after --set or the suggestion")
    ap.add_argument("--lower", type=float, default=0.0, help="move it down (m)")
    ap.add_argument("--forward", type=float, default=0.0, help="move it forward (m); negative: back")
    ap.add_argument("--gap", type=float, default=GAP, help=f"suggested height: ghost top this far under the hood (default {GAP})")
    ap.add_argument("--bay", nargs=2, type=float, metavar=("X_FIREWALL", "X_RADIATOR"), help="give the bay instead of measuring it")
    ap.add_argument("--scale", type=float, help="new EngineIconScale")
    ap.add_argument("--ghost", choices=sorted(GHOSTS), help="switch to this generic ghost mesh")
    ap.add_argument("--steam", action="store_true", help="move <Steam Origin> with it (the radiator steam)")
    ap.add_argument("--preview", type=Path, help="save a side + plan picture of the bay and the ghost")
    a = ap.parse_args(argv)

    s = Source(a.truck, mod=a.mod, media=a.media)
    f = read_xml(s.xml)
    mesh = a.ghost or f["mesh"]
    scale = a.scale or f["scale"]
    gb = ghost_bounds(mesh)
    blob = s.mesh_blob(f["physics_mesh"])
    parts = meshread.points(blob, tuple(k for k in SKIP if k not in ("glass", "windshield")))
    shield = [v for n, v in parts.items() if "windshield" in n.lower()]      # a named windshield beats lamp glass
    glassy = shield or [v for n, v in parts.items() if "glass" in n.lower() and "gauge" not in n.lower()]
    P = np.concatenate([v for n, v in parts.items() if not ("glass" in n.lower() or "windshield" in n.lower())])
    X, T = profile(P)
    B = bay(X, T, np.concatenate(glassy) if glassy else None)
    if a.bay:
        B["firewall"], B["radiator"] = a.bay
    print(f"{a.truck}: ghost {mesh} x{fnum(scale)} ({'%.2f x %.2f x %.2f m' % tuple((np.array(gb[3:]) - gb[:3]) * scale)})")
    print(f"  bay: nose {B['nose']:.2f}, windshield base {B['windshield']:.2f} -> firewall {B['firewall']:.2f}, "
          f"radiator {B['radiator']:.2f}; hood line {B['hood']:.2f}")
    cur = f["sound"]
    boxes = []
    if cur is not None:
        lo, hi = ghost_box(cur, f["scale"], ghost_bounds(f["mesh"]))
        wrong = fits(X, T, B, cur, f["scale"], ghost_bounds(f["mesh"]))
        par = f" (ParentFrame {f['sound_parent']})" if f["sound_parent"] else ""
        print(f"  now: <Sounds Origin> {fvec(cur)}{par}: " + ("; ".join(wrong) if wrong else "in the bay"))
        boxes.append(("now", lo, hi, (210, 40, 40)))
    else:
        print("  now: the truck has no <Sounds Origin>")
    at = np.array(a.set, float) if a.set else suggest(X, T, B, scale, gb, a.gap)
    at = at + np.array([a.forward, a.up - a.lower, 0.0])
    lo, hi = ghost_box(at, scale, gb)
    wrong = fits(X, T, B, at, scale, gb)
    print(f"  {'set' if a.set else 'suggested'}: <Sounds Origin=\"{fvec(at)}\"> -> ghost x {lo[0]:.2f}..{hi[0]:.2f}, "
          f"y {lo[1]:.2f}..{hi[1]:.2f}" + (": " + "; ".join(wrong) if wrong else ", in the bay"))
    print("  the height is a starting point: check it in game, then nudge with --raise / --lower")
    boxes.append(("new", lo, hi, (30, 150, 60)))
    if a.preview:
        preview(a.preview, P, X, T, B, boxes)
        print(f"  picture: {a.preview}")
    if a.apply:
        if game_running():
            raise SystemExit("Expeditions is running: close it first (the game holds its paks open)")
        where = s.write(set_sound(s.xml, at, steam=a.steam, scale=a.scale, mesh=a.ghost))
        print(f"  written: {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
