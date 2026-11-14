"""paint_river.py -- generate the speed, foam and mud masks for a map's rivers.

The editor's Water brush paints three things by hand. Two of them are ordinary
scalar masks and can be computed instead of painted, because what they represent
is not a matter of taste:

    speed (blue of _water.tga)  follows velocity, and velocity follows gradient
    foam  (green of _water.tga) follows the Froude number, plus obstacles
    mud   (_water_mud.tga)      follows the places a river drops its load

Flow direction is deliberately NOT generated. With ``NeedFlowMapLoading="false"``
the engine takes the direction from the node order, which is already correct, and
a flow map would only override it with something worse.

Calibration is taken from whatever reach the author has already painted, so the
generated part of a river matches the part that was done by eye rather than
imposing an arbitrary scale. Existing paint is never overwritten; it is treated as
the reference and blended into across a seam.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

from saber import river, water_masks as wm
from saber.river import MEDIA

FOAM_CAP = 217          # the loudest foam value seen in hand-painted work
FROUDE_ONSET = 0.72     # below this the surface is glassy
FROUDE_WHITE = 1.45     # at and above this the flow is fully broken
FOAM_FLOOR = 0.08       # anything fainter than this is a grey wash, not foam


def densify(reach, step=0.5):
    """Resample a reach to even spacing, carrying height, width and chainage."""
    x = np.array([n.x for n in reach.nodes])
    y = np.array([n.y for n in reach.nodes])
    z = np.array([n.z for n in reach.nodes])
    w = np.array([n.width for n in reach.nodes])
    seg = np.hypot(np.diff(x), np.diff(z))
    s = np.concatenate([[0.0], np.cumsum(seg)])
    t = np.arange(0.0, s[-1], step)
    return (t, np.interp(t, s, x), np.interp(t, s, y),
            np.interp(t, s, z), np.interp(t, s, w))


def smooth(a, window_m, step):
    """Moving average, used on the surface profile before differentiating it.

    Node-to-node gradient on 30 m spacing is noisy enough that a single node a
    few centimetres off reads as a rapid. Smoothing the surface first keeps the
    real steep reaches and drops the arithmetic spikes.
    """
    n = max(3, int(round(window_m / step)) | 1)
    k = np.ones(n) / n
    return np.convolve(np.pad(a, n // 2, mode="edge"), k, mode="valid")[:len(a)]


def geometry(t, x, y, z, w, step, grade_window=60.0):
    """Per-sample gradient, unit tangent, unit normal and signed curvature."""
    ys = smooth(y, grade_window, step)
    grade = -np.gradient(ys, step)                 # positive = falling downstream
    dx = np.gradient(x, step)
    dz = np.gradient(z, step)
    L = np.hypot(dx, dz)
    L[L == 0] = 1.0
    tx, tz = dx / L, dz / L
    nx, nz = -tz, tx                               # left-hand normal
    # signed curvature of the centreline; sign says which way the channel turns
    curv = (tx * np.gradient(tz, step) - tz * np.gradient(tx, step))
    curv = smooth(curv, 120.0, step)
    return grade, tx, tz, nx, nz, curv


def flowstate(grade, w, discharge, roughness, min_grade=5e-4):
    """Vectorised Manning solve -- same maths as river.hydraulics."""
    S = np.maximum(np.abs(grade), min_grade)
    W = np.maximum(w, 1.0)
    depth = (discharge * roughness / (W * np.sqrt(S))) ** 0.6
    vel = discharge / (W * depth)
    return S, depth, vel, vel / np.sqrt(9.81 * depth)


def boulder_positions(subgroups: Path, names=("Model_river boulders.xml",)):
    import re
    org = re.compile(r'Org="\(([-\d.eE]+); ([-\d.eE]+); ([-\d.eE]+)\)"')
    pts = []
    for nm in names:
        p = subgroups / nm
        if p.exists():
            pts += [(float(a), float(c)) for a, _b, c in org.findall(p.read_text(encoding="utf8"))]
    return np.array(pts) if pts else np.zeros((0, 2))


def foam_sources(t, x, z, half, curv, bend, vel, froude, boulders, step,
                 seed=20260917):
    """Discrete points where water is broken, with a lateral offset for each.

    The guide is explicit: foam belongs "in some 'wild' places of the river, for
    example, where water collides with rocks". It is produced at a POINT and then
    carried downstream - never along a whole cross-section at once.

    Computing foam per chainage and painting it across the full width produced
    mirror-symmetric bands bank to bank: measured variation 52.3 along the river
    against 3.2 across it. Nothing in a river does that.

    Three kinds of source, each returning (index, lateral offset, strength):
      * a boulder standing in the channel - the clearest case in the guide
      * the outer bank of a hard bend, where the thread of water hits it
      * a steep drop, where the surface breaks on its own

    Offsets are jittered because a natural river has no symmetry to preserve.
    """
    rng = np.random.default_rng(seed)
    out = []

    if len(boulders):
        from scipy.spatial import cKDTree
        tree = cKDTree(np.column_stack([x, z]))
        d, idx = tree.query(boulders)
        for j, i in enumerate(idx):
            if d[j] >= half[i]:
                continue                      # not in the water
            # signed offset of the boulder across the channel
            i2 = min(i + 1, len(t) - 1)
            tx, tz = x[i2] - x[i], z[i2] - z[i]
            L = np.hypot(tx, tz) or 1.0
            nx_, nz_ = -tz / L, tx / L
            off = (boulders[j][0] - x[i]) * nx_ + (boulders[j][1] - z[i]) * nz_
            # a rock only breaks water that is actually moving
            s = np.clip((vel[i] - 0.7) / 1.2, 0, 1)
            if s > 0.05:
                out.append((int(i), float(off), float(0.55 + 0.45 * s)))

    # the outer bank of a hard bend, sampled at intervals rather than continuously
    spacing = max(1, int(round(12.0 / step)))
    for i in range(0, len(t), spacing):
        if bend[i] < 0.45 or vel[i] < 1.2:
            continue
        side = -np.sign(curv[i]) or 1.0
        off = side * half[i] * rng.uniform(0.72, 0.94)
        s = np.clip((vel[i] - 1.2) / 1.0, 0, 1) * bend[i]
        if s > 0.12:
            out.append((i, float(off), float(0.35 + 0.5 * s)))

    # a genuine drop breaks the surface across more of the channel, but still in
    # patches, not a clean line
    for i in range(0, len(t), max(1, int(round(6.0 / step)))):
        if froude[i] < FROUDE_ONSET:
            continue
        s = np.clip((froude[i] - FROUDE_ONSET) / (FROUDE_WHITE - FROUDE_ONSET), 0, 1)
        for _ in range(1 + int(2 * s)):
            off = rng.uniform(-0.8, 0.8) * half[i]
            out.append((i, float(off), float(0.3 + 0.7 * s)))
    return out


def stamp_foam(foam_img, sources, t, x, z, half, vel, master_n, step,
               persist_s=14.0, width_m=0.85, seed=20260917):
    """Carry each source's foam downstream into a streak.

    A wake trails the thing that made it, so its length follows the water speed:
    fast water carries broken surface further before it heals. Width grows a
    little as the streak spreads, and the whole thing fades out.
    """
    rng = np.random.default_rng(seed + 1)
    for i0, off0, strength in sources:
        run_m = float(vel[i0]) * persist_s * rng.uniform(0.7, 1.35)
        n = max(2, int(round(run_m / step)))
        idx = np.arange(i0, min(i0 + n, len(t)))
        if len(idx) < 2:
            continue
        frac = np.linspace(0.0, 1.0, len(idx))
        fade = strength * (1.0 - frac) ** 1.6
        # the streak drifts across the channel slightly as it travels
        drift = rng.uniform(-0.25, 0.25) * half[i0] * frac
        # a wake stays narrow: it is a ribbon trailing an obstacle, not a patch
        wid = width_m * (0.7 + 1.1 * frac)
        for k, i in enumerate(idx):
            if fade[k] < 0.04:
                break
            i2 = min(i + 1, len(t) - 1)
            tx, tz = x[i2] - x[i], z[i2] - z[i]
            L = np.hypot(tx, tz) or 1.0
            nx_, nz_ = -tz / L, tx / L
            o = np.clip(off0 + drift[k], -half[i] * 0.97, half[i] * 0.97)
            span = np.arange(-wid[k], wid[k] + 0.35, 0.35)
            prof = fade[k] * np.clip(1.0 - (span / max(wid[k], 1e-6)) ** 2, 0, 1)
            px = x[i] + nx_ * (o + span)
            pz = z[i] + nz_ * (o + span)
            r, c = wm.world_to_px(px, pz, master_n)
            wm.stamp(foam_img, np.round(r).astype(int), np.round(c).astype(int), prof)


def paint_reach(speed_img, foam_img, mud_img, reach, master_n, mud_n,
                discharge, roughness, boulders, step=0.4,
                cross_step=0.45, bank_band=0.18, still=False):
    """Stamp one reach into the speed, foam and mud masters. Returns diagnostics."""
    t, x, y, z, w = densify(reach, step)
    grade, tx, tz, nx, nz, curv = geometry(t, x, y, z, w, step)
    S, depth, vel, froude = flowstate(grade, w, discharge, roughness)

    ppm = wm.px_per_m(master_n)
    mud_ppm = wm.px_per_m(mud_n)
    half = w / 2.0

    # the fast thread rides the outside of a bend; curvature sign gives the side
    bend = np.clip(np.abs(curv) * 400.0, 0.0, 1.0)
    thalweg = -np.sign(curv) * half * 0.30 * bend

    # foam from the Froude number, the physical whitewater criterion
    base_foam = np.clip((froude - FROUDE_ONSET) / (FROUDE_WHITE - FROUDE_ONSET), 0, 1)

    sources = foam_sources(t, x, z, half, curv, bend, vel, froude, boulders, step)

    rows_all, cols_all, sv, fv = [], [], [], []
    mrows, mcols, mv = [], [], []

    for i in range(len(t)):
        hw = half[i]
        offs = np.arange(-hw, hw + cross_step, cross_step)
        px = x[i] + nx[i] * offs
        pz = z[i] + nz[i] * offs
        r, c = wm.world_to_px(px, pz, master_n)

        if still:
            speed_here = np.zeros_like(offs)
        else:
            # velocity falls off toward the banks, measured from the thalweg
            u = (offs - thalweg[i]) / hw
            cross = np.clip(1.0 - 0.55 * u * u, 0.0, 1.0)
            speed_here = vel[i] * cross
        foam_here = np.zeros_like(offs)          # foam is stamped separately

        rows_all.append(r); cols_all.append(c)
        sv.append(speed_here); fv.append(foam_here)

        # mud: dropped where the water is slow and on the inside of bends
        if still:
            mud_here = np.full_like(offs, 0.85)
        else:
            slack = np.clip((1.15 - vel[i]) / 1.0, 0, 1)
            innerside = np.clip(offs * np.sign(curv[i]) / hw, 0, 1) * bend[i]
            margin = np.clip((np.abs(offs) / hw - 0.55) / 0.45, 0, 1)
            mud_here = np.clip(slack * (0.45 + 0.55 * margin) + innerside * 0.5, 0, 1)
        mr, mc = wm.world_to_px(px, pz, mud_n)
        mrows.append(mr); mcols.append(mc); mv.append(mud_here)

    def flat(a):
        return np.concatenate(a)

    r = np.round(flat(rows_all)).astype(int)
    c = np.round(flat(cols_all)).astype(int)
    wm.stamp(speed_img, r, c, flat(sv))
    if not still:
        stamp_foam(foam_img, sources, t, x, z, half, vel, master_n, step)
    mr = np.round(flat(mrows)).astype(int)
    mc = np.round(flat(mcols)).astype(int)
    wm.stamp(mud_img, mr, mc, flat(mv))

    return dict(t=t, vel=vel, depth=depth, froude=froude, grade=S, width=w,
                x=x, z=z, foam=base_foam)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level")
    ap.add_argument("--discharge", type=float, default=40.0, help="m^3/s")
    ap.add_argument("--roughness", type=float, default=0.040, help="Manning n")
    ap.add_argument("--blend", type=float, default=60.0,
                    help="metres over which to fade into existing hand paint")
    ap.add_argument("--speed-ref", type=float, default=None,
                    help="velocity in m/s that should read as 255 (default: the "
                         "fastest water on the map)")
    ap.add_argument("--apply", action="store_true", help="write the tiles")
    args = ap.parse_args()

    prebuild = MEDIA / "prebuild" / args.level
    data = prebuild / "data"
    subs = prebuild / "subgroups"

    water, wbase = wm.assemble(data, "_water.tga")
    mud, mbase = wm.assemble(data, "_water_mud.tga")
    master_n, mud_n = water.shape[0], mud.shape[0]
    print(f"water master {master_n}x{master_n} ({wm.px_per_m(master_n):.3f} px/m), "
          f"mud master {mud_n}x{mud_n} ({wm.px_per_m(mud_n):.3f} px/m)")

    had_speed = water[..., 2] > 0
    had_foam = water[..., 1] > 0
    print(f"existing hand paint: speed {had_speed.sum()} px, foam {had_foam.sum()} px")

    speed = np.zeros((master_n, master_n), np.float64)
    foam = np.zeros((master_n, master_n), np.float64)
    mudf = np.zeros((mud_n, mud_n), np.float64)

    boulders = boulder_positions(subs)
    print(f"{len(boulders)} boulders available as foam sources")

    groups = river.load_map_rivers(args.level)
    diags = []
    for g in groups:
        for rc in g.reaches:
            length = rc.length
            still = length < 200.0          # a pond, not a river
            d = paint_reach(speed, foam, mudf, rc, master_n, mud_n,
                            args.discharge, args.roughness, boulders, still=still)
            kind = "STILL" if still else "FLOWING"
            print(f"\n{g.name} [{kind}] {length:.0f} m, {len(rc.nodes)} nodes, "
                  f"{rc.clean}/{rc.muddy}")
            if not still:
                print(f"   gradient {d['grade'].min()*100:.2f}%..{d['grade'].max()*100:.2f}%")
                print(f"   depth    {d['depth'].min():.2f}..{d['depth'].max():.2f} m")
                print(f"   velocity {d['vel'].min():.2f}..{d['vel'].max():.2f} m/s "
                      f"(median {np.median(d['vel']):.2f})")
                print(f"   Froude   {d['froude'].min():.2f}..{d['froude'].max():.2f}  "
                      f"-- {100*(d['froude']>1).mean():.1f}% supercritical")
                diags.append(d)

    # ---- speed scale ----
    # The blue channel is a fraction of the engine's maximum water velocity, so
    # the honest mapping is v / v_ref. Anchoring instead on a hand-painted median
    # is tempting and wrong: the fastest water on this map is nearly twice the
    # median, so anchoring the median high pushes every rapid into the clip and
    # flattens the one signal the whole exercise exists to show.
    vmax = max(float(np.max(d["vel"])) for d in diags) if diags else 2.5
    v_ref = args.speed_ref or vmax / 0.98
    scale = 255.0 / v_ref
    print(f"\nspeed scale: {v_ref:.2f} m/s -> 255  ({scale:.1f} per m/s)")
    if had_speed.any():
        his = water[..., 2][had_speed].astype(float)
        mine = speed[had_speed]
        mine = mine[mine > 0]
        if len(mine) > 100:
            print(f"   your painted reach flows at {np.median(mine):.2f} m/s, which this "
                  f"scale puts at {np.median(mine)*scale:.0f}/255; you painted it "
                  f"{np.median(his):.0f}/255.")
            print(f"   matching yours exactly would need {np.median(his)/np.median(mine):.0f} "
                  f"per m/s, which clips everything above "
                  f"{255/(np.median(his)/np.median(mine)):.2f} m/s "
                  f"({100*np.mean(np.concatenate([d['vel'] for d in diags]) > 255/(np.median(his)/np.median(mine))):.0f}% "
                  f"of the river). Pass --speed-ref to override.")
    speed_u8 = np.clip(speed * scale, 0, 255).astype(np.uint8)
    foam_u8 = np.clip(foam * FOAM_CAP, 0, FOAM_CAP).astype(np.uint8)
    mud_u8 = np.clip(mudf * 255, 0, 255).astype(np.uint8)

    # ---- never overwrite hand paint; fade into it ----
    keep = had_speed | had_foam
    if keep.any():
        from scipy.ndimage import distance_transform_edt
        dist = distance_transform_edt(~keep) / wm.px_per_m(master_n)
        fade = np.clip(dist / max(args.blend, 1e-6), 0, 1)
        speed_u8 = np.where(keep, water[..., 2],
                            (speed_u8 * fade).astype(np.uint8))
        foam_u8 = np.where(keep, water[..., 1],
                           (foam_u8 * fade).astype(np.uint8))

    new_speed = int((speed_u8 > 0).sum())
    new_foam = int((foam_u8 > 0).sum())
    print(f"\nresult: speed {had_speed.sum()} -> {new_speed} px "
          f"(+{new_speed - int(had_speed.sum())})")
    print(f"        foam  {had_foam.sum()} -> {new_foam} px "
          f"(+{new_foam - int(had_foam.sum())})")
    print(f"        mud   0 -> {int((mud_u8 > 0).sum())} px")

    if not args.apply:
        print("\n(dry run -- pass --apply to write the tiles)")
        return 0

    water[..., 1] = foam_u8
    water[..., 2] = speed_u8
    n1 = wm.split(data, "_water.tga", water, wbase)
    if mud.shape[2] >= 3:
        for ch in range(min(3, mud.shape[2])):
            mud[..., ch] = mud_u8
    n2 = wm.split(data, "_water_mud.tga", mud, mbase)
    # 🚨 THE fullTexture MASTERS TOO, OR THERE IS NO WATER.
    # This wrote only the per-tile copies. Diamondback carries BOTH -- a
    # 2605x2605 fullTexture/_water.tga and a 253x253 _water_mud.tga beside the
    # 16 tiles -- and Shafer carried 30 valid River objects with 234 nodes,
    # 69% of them standing above the ground, and NOT A DROP of visible water.
    # The River object is geometry; these rasters are what the engine renders.
    import struct as _st
    for _nm in ("_water.tga", "_water_mud.tga"):
        _t = {}
        for _d in (prebuild / "data").iterdir():
            _p = _d / _nm
            if _d.is_dir() and _p.exists():
                _c, _r = (int(v) for v in _d.name.split("_"))
                _b = _p.read_bytes()
                _il = _b[0]
                _w, _h = _st.unpack_from("<HH", _b, 12)
                _bp = _b[16] // 8
                _o = 18 + _il
                _a = np.frombuffer(_b[_o:_o + _w * _h * _bp], np.uint8).reshape(_h, _w, _bp)
                _t[(_c, _r)] = _a if (_b[17] & 0x20) else _a[::-1]
        if not _t:
            continue
        _tp = min(min(v.shape[:2]) for v in _t.values())
        _ch = next(iter(_t.values())).shape[2]
        _n = 4 * _tp + 1
        _m = np.zeros((_n, _n, _ch), np.uint8)
        for (_c, _r), v in _t.items():
            _m[_r*_tp:_r*_tp+v.shape[0], _c*_tp:_c*_tp+v.shape[1]] = v
        _out = prebuild / "fullTexture" / _nm
        _out.write_bytes(bytes([0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0])
                         + _st.pack("<HH", _n, _n) + bytes([_ch * 8, 0x20]) + _m.tobytes())
        print(f"   master {_out.name}: {_n}x{_n} from {len(_t)} tiles")

    print(f"\nwrote {n1} _water.tga tiles and {n2} _water_mud.tga tiles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
