"""Where the eight rock-light pods go on a truck, and the pod geometry itself.

Layout (proven on all 39 vanilla trucks of Expeditions, 2026-11-15):
  1. Four pods a side, placed from the truck's own wheel positions: one behind the front wheel, one
     ahead of the rear wheel, two under the frame. The ratios come from a hand-placed set on one truck.
  2. Height starts at the chassis frame (the BoneChassis_cdt frame record in the converted mesh, when
     the truck has one).
  3. Each pod is then MOUNTED on the truck's own geometry: rays from the pod's back, straight up and
     straight in toward the centreline, against the truck mesh. The nearest nearby spot (lower or further
     in) with steel within a short bracket wins, and the light moves with the pod.

Engine axes throughout: x forward, y up, z left. Everything is built for the LEFT side and mirrored.
"""
from __future__ import annotations

import re
import struct

import numpy as np

FRAME = "BoneChassis_cdt"
# (x ratio along the wheelbase from the rear axle, -, z ratio of the track) and the aim (left side)
LAYOUT = [
    ("front_well", (0.783, 0.790), (0.3, -0.68, 0.68)),
    ("rear_well", (0.216, 0.790), (-0.3, -0.68, 0.68)),
    ("under_front", (0.626, 0.687), (0.0, -0.96, 0.29)),
    ("under_rear", (0.389, 0.687), (0.0, -0.96, 0.29)),
]
DY = [0.033, 0.013, -0.087, -0.087]   # start heights from the chassis frame

# mounting search
SKIP_FRAMES = ("glass", "mirror", "cockpit", "windshield", "gauge", "guges", "lp_cab", "hp_cab", "dashboard",
               "suspension")
POD_R, POD_H, LENS_R = 0.026, 0.022, 0.021   # 2 in pod, ~7/8 in deep
LENS_BACK = 0.012                            # light point -> lens
POD_BACK = LENS_BACK + POD_H                 # light point -> pod back
REACH, MIN_GAP, FALLBACK_STEM = 0.15, 0.008, 0.05
STEP, SLIDE_MAX, DROP_MAX, RISE_MAX = 0.02, 0.50, -0.40, 0.10
BLACK, LENS = 0, 1                           # atlas swatches


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


# ---------------------------------------------------------------- reading the truck
def wheels(xml: str) -> list[tuple[float, float, float]]:
    return [tuple(float(v) for v in m.group(1).split(";"))
            for m in re.finditer(r'<Wheel\b[^>]*Pos="\(([^)]*)\)"', xml)]


def frame_y(mesh_blob: bytes, frame: str = FRAME) -> float | None:
    """height of a named frame's origin in a converted mesh (row-vector 4x4, translation in row 3)"""
    n = struct.unpack_from("<I", mesh_blob, 0)[0]
    geo = mesh_blob[4 + n:]
    name = frame.encode()
    for m in re.finditer(rb"\x00(" + re.escape(name) + rb")\x00", geo):
        o = m.start() + 1
        if struct.unpack_from("<I", geo, o - 4)[0] != len(name) + 1:
            continue
        M = np.array(struct.unpack_from("<16f", geo, o + len(name) + 1)).reshape(4, 4)
        if np.isfinite(M).all() and abs(M[3, 3] - 1) < 1e-3:
            return float(M[3, 1])
    return None


def body_tris(mesh_blob: bytes) -> np.ndarray:
    """(n, 3, 3) triangles of every structural frame: no cab, glass, interior, suspension or wheels"""
    from saber.stock_mesh import frame_blocks
    T = []
    for name, bl in frame_blocks(mesh_blob).items():
        if any(k in name.lower() for k in SKIP_FRAMES):
            continue
        # a part stored in its own frame's local space (shocks, springs, fans sit round the origin in some
        # mod meshes) is not where it shows on the truck: it dips below the ground, so leave it out
        if bl and min(float(bb.positions[:, 1].min()) for bb in bl if len(bb.positions)) < -0.05:
            continue
        for b in bl:
            f = b.faces[(b.faces[:, 0] != b.faces[:, 1]) & (b.faces[:, 1] != b.faces[:, 2])]
            T.append(b.positions[f])
    if not T:
        raise ValueError("no structural geometry found in the truck mesh")
    return np.concatenate(T).astype(float)


# ---------------------------------------------------------------- layout and mounting
def layout(xml: str, start_y: float) -> list[tuple[np.ndarray, np.ndarray]]:
    """[(light point, aim)] for the LEFT side from the wheels, at start_y + DY"""
    W = wheels(xml)
    if len(W) < 4:
        raise ValueError("fewer than 4 <Wheel Pos=...> in the truck class")
    fx, rx = max(w[0] for w in W), min(w[0] for w in W)
    wz = max(abs(w[2]) for w in W)
    L = fx - rx
    if L <= 0.5 or wz <= 0:
        raise ValueError(f"implausible wheel geometry: axles x {rx}..{fx}, track {wz}")
    return [(np.array([round(rx + r[0] * L, 3), round(start_y + dy, 3), round(wz * r[1], 3)]), unit(aim))   # mm, as tested
            for (_n, r, aim), dy in zip(LAYOUT, DY)]


def ray(tris: np.ndarray, o, d) -> float:
    """distance along unit d from o to the nearest triangle (Moller-Trumbore, two-sided), inf if none"""
    if not len(tris):
        return np.inf
    v0, e1, e2 = tris[:, 0], tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
    h = np.cross(d, e2)
    a = (e1 * h).sum(1)
    ok = np.abs(a) > 1e-12
    f = np.where(ok, 1 / np.where(ok, a, 1), 0)
    s = o - v0
    u = f * (s * h).sum(1)
    q = np.cross(s, e1)
    v = f * (q @ d)
    t = f * (e2 * q).sum(1)
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0.003)
    return float(t[hit].min()) if hit.any() else np.inf


def mount(tris: np.ndarray, p, aim, rise_max: float = RISE_MAX):
    """(light point, aim, bracket direction, bracket length, found) for a LEFT pod. The pod keeps its place
    along the truck; it may drop and slide inward to the nearest spot with steel within REACH straight above
    or straight inboard. Score = movement + bracket length."""
    p, aim = np.asarray(p, float), unit(aim)
    near = tris[np.abs(tris.mean(1)[:, 0] - (p - aim * POD_BACK)[0]) < 0.25]
    dirs = (np.array([0, 1.0, 0]), np.array([0, 0, -1.0]))
    best = None
    for dy in np.arange(DROP_MAX, rise_max + 1e-9, STEP):
        for dz in np.arange(0, SLIDE_MAX + 1e-9, STEP):
            q = p + np.array([0, dy, -dz])
            if q[2] < 0.08:
                break
            back = q - aim * POD_BACK
            for d in dirs:
                t = ray(near, back, d)
                if MIN_GAP <= t <= REACH:
                    score = np.hypot(dy, dz) + t
                    if best is None or score < best[0]:
                        best = (score, q, d, t)
    if best is None:
        return p, aim, np.array([0, 1.0, 0]), FALLBACK_STEM, False
    return best[1], aim, best[2], best[3], True


def plan(xml: str, mesh_blob: bytes) -> dict:
    """everything the kit needs: {"frame", "mounts": [(p, aim, sdir, slen, found) x4 left]}"""
    W = wheels(xml)
    cy = frame_y(mesh_blob)
    tris = body_tris(mesh_blob)
    if cy is not None:
        return {"frame": FRAME, "mounts": [mount(tris, p, a) for p, a in layout(xml, cy)]}
    # A truck without BoneChassis_cdt: no frame height to start from, so MEASURE the underside. From 0.3 m
    # below the wheel centres, a ray straight up at each pod's place finds the first surface: the frame
    # or floor. The pod starts just under it, then mounts as usual.
    wy = float(np.mean([w[1] for w in W]))
    start = []
    for p, a in layout(xml, wy):
        h = ray(tris, np.array([p[0], wy - 0.3, p[2]]), np.array([0, 1.0, 0]))
        if not np.isfinite(h):            # nothing above at the outer spot: look further in
            h = ray(tris, np.array([p[0], wy - 0.3, p[2] * 0.6]), np.array([0, 1.0, 0]))
        y = wy - 0.3 + h - POD_R - 0.01 if np.isfinite(h) and h < 1.5 else wy + 0.15
        start.append((np.array([p[0], round(y, 3), p[2]]), a))
    return {"frame": None, "mounts": [mount(tris, p, a) for p, a in start]}
    return {"frame": frame, "mounts": mounts}


def lights(mounts) -> list[tuple[np.ndarray, np.ndarray]]:
    """all eight (point, aim), left then mirrored right"""
    out = []
    for p, a, *_ in mounts:
        out += [(p, a), (p * [1, 1, -1], a * [1, 1, -1])]
    return out


# ---------------------------------------------------------------- pod geometry
class Mesh:
    def __init__(self):
        self.v, self.f, self.sw = [], [], []

    def add(self, V, F, sw):
        base = len(self.v)
        self.v += [np.asarray(p, float) for p in V]
        self.f += (np.asarray(F) + base).tolist()
        self.sw += [sw] * len(F)

    def add_out(self, V, F, centres, sw):
        """faces wound so each normal points away from its own reference point"""
        V = np.asarray(V, float)
        F = np.asarray(F)
        n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
        flip = (n * (V[F].mean(1) - centres)).sum(1) < 0
        F = F.copy()
        F[flip] = F[flip][:, ::-1]
        self.add(V, F, sw)

    def mirror(self):
        V = np.array(self.v)
        F = np.array(self.f)
        S = list(self.sw)
        V2 = V.copy()
        V2[:, 2] *= -1
        base = len(self.v)
        self.v += list(V2)
        self.f += (F[:, ::-1] + base).tolist()
        self.sw += S

    def save(self, path):
        np.savez(path, v=np.array(self.v), f=np.array(self.f, np.int32), sw=np.array(self.sw, np.int32))


def _frame(n):
    n = unit(n)
    u = unit(np.cross(n, [1.0, 0, 0] if abs(n[0]) < 0.9 else [0, 1.0, 0]))
    return u, np.cross(n, u)


def cyl(m, c0, c1, r, k=28, sw=BLACK):
    """closed cylinder from c0 to c1"""
    c0, c1 = np.asarray(c0, float), np.asarray(c1, float)
    a = unit(c1 - c0)
    u, w = _frame(a)
    ring = [r * (np.cos(t) * u + np.sin(t) * w) for t in np.linspace(0, 2 * np.pi, k, endpoint=False)]
    V = np.array([c0 + p for p in ring] + [c1 + p for p in ring] + [c0, c1])
    F, cap = [], []
    for j in range(k):
        j2 = (j + 1) % k
        F += [[j, j2, k + j2], [j, k + j2, k + j], [2 * k, j2, j], [2 * k + 1, k + j, k + j2]]
        cap += [False, False, True, True]
    F = np.array(F)
    mid = (c0 + c1) / 2
    C = np.array([mid if c else c0 + ((V[f].mean(0) - c0) @ a) * a for f, c in zip(F, cap)])
    m.add_out(V, F, C, sw)


def box(m, c, n, u, size, sw=BLACK):
    """a thin plate: centre c, thickness along n, size (along u, along n x u, thickness)"""
    c, n, u = np.asarray(c, float), unit(n), unit(u)
    w = np.cross(n, u)
    a, b, t = size[0] / 2, size[1] / 2, size[2] / 2
    V = [c + sx * a * u + sy * b * w + sz * t * n for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    F = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
         [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]]
    m.add_out(V, F, np.repeat(c[None], len(F), 0), sw)


def bolt(m, p, nrm, r=0.0045, h=0.003, k=8):
    p, nrm = np.asarray(p, float), unit(nrm)
    u = unit(np.cross(nrm, [0, 1, 0] if abs(nrm[1]) < 0.9 else [1, 0, 0]))
    w = np.cross(nrm, u)
    V = [p + r * (np.cos(t) * u + np.sin(t) * w) for t in np.linspace(0, 2 * np.pi, k, endpoint=False)]
    V.append(p + nrm * h)
    F = np.array([[i, (i + 1) % k, k] for i in range(k)])
    m.add_out(V, F, np.repeat((p - nrm * 0.01)[None], len(F), 0), BLACK)


def pod(m, p, d, sdir, slen):
    """housing + lens aimed along d (lens just behind light point p), a bracket along sdir to the steel,
    and a bolted tab flat on it"""
    p, d, sdir = np.asarray(p, float), unit(d), unit(sdir)
    front = p - d * LENS_BACK
    back = front - d * POD_H
    cyl(m, back, front, POD_R)
    cyl(m, front - d * 0.0012, front + d * 0.0010, LENS_R, sw=LENS)
    top = back + sdir * slen
    cyl(m, back + d * 0.004, top - sdir * 0.003, 0.006, k=12)
    u = np.array([1.0, 0, 0])
    box(m, top - sdir * 0.0015, -sdir, u, (0.048, 0.026, 0.003))
    for s in (-1, 1):
        bolt(m, top - sdir * 0.003 + u * 0.017 * s, -sdir)


def pod_mesh(mounts) -> Mesh:
    m = Mesh()
    for p, a, sdir, slen, _found in mounts:
        pod(m, p, a, sdir, slen)
    m.mirror()
    return m
