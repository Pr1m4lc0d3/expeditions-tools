"""check_new_map.py -- acceptance gate for a map built by new_map_from.py.

Written because the first run of `new_map_from.py` produced a map that looked
finished and was wrong in two ways nothing reported:

  - the descriptor had two '>' in the opening <Terrain> tag, because the
    description was inserted after the tag had already closed
  - 35 of Hell's Revenge's road overlays came across, because they live under
    `data/Overlay` and the skip list only looked at the project root

Both were invisible from the tool's own success output. So the tool no longer
gets to report its own success.

Usage:
    python check_new_map.py shafer_trail --source hells_revenge --relief 454.9
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from saber import dds, project, tga  # noqa: E402

try:
    from defusedxml import ElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET


def register(lvl: Path, tiles, grid: int, tile_px: int, max_height: float, chk) -> None:
    """Do the overlays land on the road benches that are physically in the DEM?

    THE CHECK THAT WAS MISSING. Every other check here verifies one layer in
    isolation, so a map whose terrain was mirrored east-west relative to its
    roads passed 19/19 and was still wrong -- caught by the owner looking at
    the rendered map. A per-layer gate structurally cannot see a disagreement
    BETWEEN layers; only a cross-layer one can.

    A road is a cut-and-fill bench, so it is in the heightmap whether or not
    anything marked it. Score the overlay nodes against that, and score each
    mirror too: if a flip beats identity, the frames disagree and the flip
    names which axis.
    """
    import numpy as np
    from scipy.ndimage import maximum_filter, minimum_filter, binary_dilation

    n = grid * tile_px + 1
    span = tile_px * grid * 0.5          # 0.5 m/px is fixed by the block size
    mpp = span / (n - 1)
    master = np.zeros((n, n), np.float32)
    for name, a in tiles:
        col, row = (int(c) for c in name.split("_"))
        master[row*tile_px:row*tile_px+a.shape[0], col*tile_px:col*tile_px+a.shape[1]] = a
    dem = master * max_height

    gy, gx = np.gradient(dem, mpp)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    w = int(41 / mpp)
    bench = (slope < 10) & (maximum_filter(dem, w) - minimum_filter(dem, w) > 20)
    tol = binary_dilation(bench, np.ones((13, 13), bool))
    chance = float(tol.mean())

    pts = []
    for f in sorted((lvl / "data" / "Overlay").glob("Overlay_*.xml")):
        for nd in ET.fromstring(f.read_text(encoding="utf-8")).iter("Node"):
            x, z = nd.get("Position").strip("()").split(";")
            pts.append((float(x), float(z)))
    pts = np.array(pts)
    half = span / 2

    def hit(fx, fz):
        x = -pts[:, 0] if fx else pts[:, 0]
        z = -pts[:, 1] if fz else pts[:, 1]
        c = np.clip(((x + half) / mpp).astype(int), 0, n-1)
        r = np.clip(((z + half) / mpp).astype(int), 0, n-1)
        return float(tol[r, c].mean())

    scores = {(fx, fz): hit(fx, fz) for fx in (False, True) for fz in (False, True)}
    ident = scores[(False, False)]
    best = max(scores, key=scores.get)
    for (fx, fz), s in sorted(scores.items(), key=lambda kv: -kv[1]):
        tag = (("flip X " if fx else "") + ("flip Z" if fz else "")) or "identity"
        print(f"        {tag:<12} {100*s:5.1f}%")
    print(f"        chance level {100*chance:.1f}%")
    chk("overlays beat chance on the benches", ident > chance * 1.8,
        f"{100*ident:.1f}% vs {100*chance:.1f}% chance")
    chk("no mirror beats identity (frames agree)", best == (False, False),
        "" if best == (False, False) else
        f"{'flip X ' if best[0] else ''}{'flip Z' if best[1] else ''}"
        f"scores {100*scores[best]:.1f}% -- TERRAIN AND ROADS ARE IN DIFFERENT FRAMES")


class Checks:
    def __init__(self) -> None:
        self.failed = 0
        self.run = 0

    def __call__(self, label: str, cond: bool, detail: str = "") -> bool:
        self.run += 1
        if not cond:
            self.failed += 1
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
        return bool(cond)


def expected_tile_shape(name: str, tile_px: int, grid: int) -> tuple[int, int]:
    """The last row and column carry the seam pixel: grid*tile+1 stitched.

    So on a 4x4 of 1008, tile 3_3 is 1009x1009 and 0_3 is 1009x1008. Asserting a
    flat tile_px here fails on correct data -- it did, first time out.
    """
    col, row = (int(c) for c in name.split("_"))
    return (tile_px + (row == grid - 1), tile_px + (col == grid - 1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="level name without the level_ prefix")
    ap.add_argument("--source", help="the map it was cloned from; checks it is unharmed")
    ap.add_argument("--relief", type=float, help="expected relief in metres, from the DEM fetch")
    ap.add_argument("--blocks", type=int, default=84)
    a = ap.parse_args()

    P = project.PREBUILD
    lvl = P / f"level_{a.name}"
    xmlp = P / f"level_{a.name}.xml"
    if not lvl.is_dir() or not xmlp.is_file():
        print(f"map pair incomplete: folder={lvl.is_dir()} descriptor={xmlp.is_file()}")
        return 2
    chk = Checks()

    print("DESCRIPTOR")
    try:
        root = ET.fromstring(xmlp.read_text(encoding="utf-8"))
        chk("well-formed XML", True)
    except Exception as e:
        chk("well-formed XML", False, str(e))
        return 1
    chk(f"{a.blocks} blocks", root.get("NumBlocksX") == str(a.blocks)
        and root.get("NumBlocksZ") == str(a.blocks),
        f'{root.get("NumBlocksX")}x{root.get("NumBlocksZ")}')
    mh = float(root.get("MaxHeight") or 0)
    chk("MaxHeight set", mh > 0, str(mh))
    chk("TerrainId set", bool(root.get("TerrainId")), repr(root.get("TerrainId")))

    print("\nTERRAIN")
    tiles = sorted(p for p in (lvl / "data").iterdir()
                   if p.is_dir() and re.fullmatch(r"\d+_\d+", p.name))
    h = [(t.name, dds.read_r16f(t / "_height.dds")) for t in tiles
         if (t / "_height.dds").exists()]
    grid = int(math.isqrt(len(h))) or 1
    chk(f"{grid*grid} height tiles", len(h) == grid * grid, f"{len(h)} found")
    if h:
        tile_px = min(min(s.shape) for _, s in h)
        bad = [(n, s.shape) for n, s in h if s.shape != expected_tile_shape(n, tile_px, grid)]
        chk("tile sizes incl. seam row/col", not bad,
            str(bad) if bad else f"{grid*tile_px+1}x{grid*tile_px+1} stitched")
        lo = min(float(s.min()) for _, s in h)
        hi = max(float(s.max()) for _, s in h)
        relief = (hi - lo) * mh
        if a.relief:
            chk(f"relief matches the DEM ({a.relief} m)", abs(relief - a.relief) < 1.0,
                f"{relief:.1f} m")
        else:
            print(f"        relief {relief:.1f} m")
        chk("no flat tile (a missed write)",
            all(float(s.max()) - float(s.min()) > 1e-4 for _, s in h))
        chk("R16F step is usable", relief / 2048 < 0.30, f"{relief/2048*100:.1f} cm at the top")

    print("\nROADS")
    ovs = sorted((lvl / "data" / "Overlay").glob("Overlay_*.xml"))
    if not ovs:
        print("        no overlays (fine if this map has no roads yet)")
    else:
        flat = off = 0
        total = 0.0
        for f in ovs:
            r = ET.fromstring(f.read_text(encoding="utf-8"))
            flat += r.get("Flatten") == "true"
            off += r.get("ApplyOffset") == "false"
            pts = []
            for n in r.iter("Node"):
                x, y = n.get("Position").strip("()").split(";")
                pts.append((float(x), float(y)))
            total += sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        chk("Flatten=true on all (the default is backwards)", flat == len(ovs), f"{flat}/{len(ovs)}")
        chk("ApplyOffset=false on all (the default adds ruts)", off == len(ovs), f"{off}/{len(ovs)}")
        print(f"        {len(ovs)} overlays, {total:.0f} m of road")

    if ovs and h:
        print("\nREGISTER: DO THE ROADS SIT ON THEIR BENCHES?")
        register(lvl, h, grid, tile_px, mh, chk)

    # A map without these does not load: the engine requires at least one
    # Expedition (contractType 1) and at least one deploy zone.
    print("\nLOADABILITY")
    zs = lvl / "zone_settings.json"
    ob = lvl / "objective_settings.json"
    deploy = expeditions = defaults = 0
    zone_names, refs = set(), set()
    if zs.is_file():
        import json as _json
        d = _json.loads(zs.read_text(encoding="utf-8"))
        for z in d.get("SslValue", {}).get("zoneDesc", []):
            zone_names.add(z.get("name"))
            dp = (z.get("props") or {}).get("ZonePropertyDeploy")
            deploy += dp is not None
            defaults += bool(dp and dp.get("defaultDeployPoint"))
    if ob.is_file():
        import json as _json, re as _re
        txt = ob.read_text(encoding="utf-8")
        d = _json.loads(txt)
        cs = d.get("SslValue", {}).get("contractsSettings", [])
        expeditions = sum(1 for c in cs if c.get("contractType") == 1)
        for m in _re.findall(r'"globalZoneId"\s*:\s*"([^"]*)"', txt):
            refs.add(m.split("||")[-1].strip())
        for c in cs:
            if c.get("recommendedDeployZone"):
                refs.add(c["recommendedDeployZone"])
    # zone_settings carries references too, and MORE of them: an outpost's FOB
    # module pairs, main base and supply shop all point at zones from here.
    # Scanning only objective_settings reported "0 refs" on a map that had just
    # emitted eighteen.
    if zs.is_file():
        import re as _re
        for m in _re.findall(r'"globalZoneId"\s*:\s*"([^"]*)"',
                             zs.read_text(encoding="utf-8")):
            refs.add(m.split("||")[-1].strip())
    chk(">=1 deploy zone", deploy >= 1, f"{deploy} of {len(zone_names)} zones")
    # Exactly one default, as every shipped map does it: Diamondback has four
    # deploy zones and one default. Adding a second zone made both default,
    # which nothing else would have caught.
    if deploy:
        chk("exactly one defaultDeployPoint", defaults == 1,
            f"{defaults} zones marked default")
    chk(">=1 Expedition (contractType 1)", expeditions >= 1, f"{expeditions}")
    dangling = {r for r in refs if r and r not in zone_names}
    # THE four-day bug: a zone reference that resolves to nothing crashes
    # DeployZonesInfo.GetDefaultDeployZone with no popup and no log.
    chk("no dangling zone reference", not dangling,
        f"{sorted(dangling)} not in {sorted(zone_names)}" if dangling else
        f"{len(refs)} refs, all resolve")
    spaced = {z for z in zone_names if z and any(c.isspace() for c in z)}
    chk("no whitespace in zone ids", not spaced, f"{sorted(spaced)}" if spaced else "")

    print("\nNOTHING INHERITED FROM THE SOURCE MAP")
    # Both of these were written as "the file must not exist", which is wrong
    # once the map is legitimately worked on. dress_map.py authors a
    # Distribution subgroup FOR this terrain, and the editor writes an empty
    # <Zones/> the moment the map is opened. Neither is inheritance, and a gate
    # that cries wolf at them gets ignored at the moment it matters.
    #
    # What is actually forbidden is content that came from ANOTHER map, so test
    # that: byte-identity against the source, and non-empty mission data.
    srcdir = P / f"level_{a.source}" if a.source else None

    def carried(rel: str) -> bool:
        if not srcdir:
            return False
        x, y = lvl / rel, srcdir / rel
        return (x.is_file() and y.is_file()
                and x.stat().st_size == y.stat().st_size
                and x.read_bytes() == y.read_bytes())

    subs = sorted(p.name for p in (lvl / "subgroups").glob("*.xml"))
    carried_subs = [s for s in subs if carried(f"subgroups/{s}")]
    chk("no subgroup carried from the source", not carried_subs,
        f"carried: {', '.join(carried_subs)}" if carried_subs
        else (f"{len(subs)} authored here: {', '.join(subs)}" if subs else "none"))
    chk("no Model_* placements", not list((lvl / "subgroups").glob("Model_*.xml")))

    # Third time this class of check has cried wolf at real work, so state the
    # rule once: these files SHOULD exist on a finished map -- a map with no
    # zone_settings.json has no deploy zone and will not load. What is
    # forbidden is the SOURCE map's copy. Test provenance, not presence.
    for n in ("zone_settings.json", "objective_settings.json", "features.json"):
        if not (lvl / n).exists():
            chk(f"{n} absent or authored here", True, "absent")
        else:
            chk(f"{n} absent or authored here", not carried(n),
                "carried from the source!" if carried(n) else "authored here")
    z = lvl / "_zones.xml"
    # A finished map HAS zones here. The forbidden thing is the source map's
    # copy. (The previous form allowed any content whose tag was not literally
    # "<Zone " with a trailing space -- it passed a file full of real zones by
    # accident, because the generator writes "<Zone\n".)
    nz = len(list(ET.fromstring(z.read_text(encoding="utf-8")).iter("Zone"))) if z.is_file() else 0
    chk("_zones.xml authored here", not carried("_zones.xml"),
        f"{nz} zone(s)" if z.is_file() else "absent")
    # Zone GEOMETRY is split across _zones.xml and every subgroups/Zone_*.xml --
    # Diamondback keeps 3 in the former and 32 in the latter. Comparing
    # zone_settings against _zones.xml alone declared a correct 18-zone map
    # broken the moment outposts were added.
    placed = {e.get("Id")
              for f in [z] + sorted((lvl / "subgroups").glob("Zone_*.xml"))
              if f.is_file() and f.suffix == ".xml"
              for e in ET.fromstring(f.read_text(encoding="utf-8")).iter("Zone")}
    if zone_names or placed:
        only_geom = sorted(placed - zone_names)
        only_sett = sorted(zone_names - placed)
        chk("every zone has both geometry and settings",
            not only_geom and not only_sett,
            (f"geometry-only {only_geom} " if only_geom else "")
            + (f"settings-only {only_sett}" if only_sett else "")
            or f"{len(placed)} zones agree")
    leaked = [p for p in lvl.rglob("*")
              if p.is_file() and (".pre-" in p.name or p.name.endswith((".bak", ".disabled")))]
    chk("no backup chain", not leaked, f"{len(leaked)} files" if leaked else "")

    if a.source:
        # Carried PAINTING is as wrong as carried models and much harder to see:
        # Hell's Revenge's mtrl_base_blends came across byte-identical and drove
        # us_concrete_old over ~40% of Canyonlands. Byte-identity is the right
        # test rather than uniformity, because it stays valid once this map is
        # painted for real -- then the rasters differ by design.
        print("\nNO PAINTING CARRIED FROM THE SOURCE")
        src = P / f"level_{a.source}"
        same = []
        for t in sorted(p for p in (lvl / "data").iterdir() if p.is_dir()):
            for f in t.glob("*.tga"):
                o = src / "data" / t.name / f.name
                if not o.exists() or f.stat().st_size != o.stat().st_size:
                    continue
                if f.read_bytes() != o.read_bytes():
                    continue
                # A uniform raster matching is fine -- both are simply blank, and
                # most of these are (snow, water, mud are all zero). Decode to
                # decide. Sniffing raw bytes at a guessed pixel offset flagged
                # every empty raster as carried painting.
                try:
                    arr = tga.read(f)
                except Exception:
                    continue
                flat = arr.reshape(-1, arr.shape[-1])
                if not (flat == flat[0]).all():
                    same.append(f"{t.name}/{f.name}")
        chk("no painted raster is byte-identical to the source", not same,
            f"{len(same)} carried: {', '.join(sorted({s.split('/')[1] for s in same}))}"
            if same else "")

        print("\nSOURCE MAP UNTOUCHED")
        src = P / f"level_{a.source}"
        chk("source still has its overlays",
            bool(list((src / "data" / "Overlay").glob("*.xml"))),
            f'{len(list((src / "data" / "Overlay").glob("*.xml")))}')
        chk("source still has its subgroups", bool(list((src / "subgroups").glob("*.xml"))))

    print(f"\n{chk.run - chk.failed}/{chk.run} checks passed"
          f"{'' if not chk.failed else f'  -- {chk.failed} FAILED'}")
    return 1 if chk.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
