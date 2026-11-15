"""Read geometry out of a stock (shipped) Saber mesh.

Each sub-mesh is stored as: 6-float bbox, u32 1, u32 0, u32 0, u32 triangle count,
u32 0, u32 vertex count, u32 element count, then a D3D9 vertex declaration
(8-byte elements: stream u16, offset u16, type u8, method u8, usage u8,
usage index u8) ending in stream 0xFF, then the vertices, then u16 indices.
Layout measured 2026-11-10 on trucks_tuning_don_71_tuning and the CJ-7 tuning
meshes: positions are float3 in model space (the bbox brackets them).

Works on converted mod meshes too (the editor's prebuild/meshes output). Used by
rock-lights/add_rock_lights.py to find a truck's frame and floor.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

# D3DDECLTYPE byte sizes
_TYPE_SIZE = {0: 4, 1: 8, 2: 12, 3: 16, 4: 4, 5: 4, 6: 8, 7: 4, 8: 4, 9: 4,
              10: 8, 11: 4, 12: 8, 13: 4, 14: 4, 15: 4, 16: 8}


@dataclass
class Block:
    offset: int          # vertex data start, within the geometry section
    bbox: np.ndarray     # (6,) lo xyz, hi xyz
    positions: np.ndarray  # (n, 3)
    faces: np.ndarray    # (m, 3) indices into positions
    uv: np.ndarray | None
    # (material slot, first triangle, triangle count); slot indexes the frame's
    # material list. Table sits just before the bbox: u32 n, n x 6 u32
    # (slot, tri start, tri count, vert start, vert count, 0), u32 1, u32 ?.
    submeshes: list = None
    materials: list = None   # filled by frame_blocks


def _geometry(blob: bytes) -> bytes:
    n = struct.unpack_from("<I", blob, 0)[0]
    return blob[4 + n:]


def _submeshes(g: bytes, bbox_at: int, ic: int, vc: int) -> list[tuple[int, int, int]]:
    """The per-material split, read from the table before the bbox. Its exact
    framing varies (2- and 4-byte trailers, extra flag words seen), so look for
    the chain itself: entries (slot, tri start, tri count, vert start, vert
    count) whose starts run on from the previous counts and add up to ic / vc."""
    seg = g[max(0, bbox_at - 400):bbox_at]
    for al in range(4):
        w = np.frombuffer(seg[al:al + (len(seg) - al) // 4 * 4], "<u4")
        for i in range(1, len(w) - 3):
            if w[i] or w[i + 2] or not (0 < w[i + 1] <= ic) or not (0 < w[i + 3] <= vc):
                continue
            chain, ts, vs, j = [(int(w[i - 1]), 0, int(w[i + 1]))], int(w[i + 1]), int(w[i + 3]), i + 4
            while ts < ic and j < len(w) - 3:
                k = next((k for k in range(j, min(j + 4, len(w) - 3))
                          if w[k] == ts and w[k + 2] == vs and w[k + 1] and w[k + 3]), None)
                if k is None:
                    break
                chain.append((int(w[k - 1]), ts, int(w[k + 1])))
                ts, vs, j = ts + int(w[k + 1]), vs + int(w[k + 3]), k + 4
            if ts == ic and vs == vc:
                return chain
    return [(0, 0, ic)]


def blocks(blob: bytes) -> list[Block]:
    g = _geometry(blob)
    out: list[Block] = []
    p = 24
    while True:
        p = g.find(b"\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00", p)
        if p < 0 or p + 28 > len(g):
            break
        hit, p = p, p + 4
        if hit < 24:
            continue
        bb = np.array(struct.unpack_from("<6f", g, hit - 24))
        ic, z1, vc, ne = struct.unpack_from("<IIII", g, hit + 12)
        if (z1 or not (0 < vc < 1_000_000) or not (0 < ic < 1_000_000) or not (1 <= ne <= 16)
                or not np.isfinite(bb).all() or (np.abs(bb) > 100).any()
                or (bb[3:] < bb[:3]).any()):
            continue
        q = hit + 28
        elems, ok = [], False
        for _ in range(ne + 1):
            st, off, typ, _m, use, ui = struct.unpack_from("<HHBBBB", g, q)
            q += 8
            if st == 0xFF:
                ok = True
                break
            elems.append((off, typ, use, ui))
        if not ok or not elems or elems[0][2] != 0 or elems[0][1] != 2:
            continue
        stride = max(o + _TYPE_SIZE.get(t, 4) for o, t, _u, _i in elems)
        vend = q + stride * vc
        iend = vend + 6 * ic   # ic counts triangles (measured: 3*ic indices)
        if iend > len(g):
            continue
        raw = np.frombuffer(g[q:vend], np.uint8).reshape(vc, stride)
        pos = raw[:, :12].copy().view("<f4").reshape(vc, 3)
        if not ((pos >= bb[:3] - 1e-2) & (pos <= bb[3:] + 1e-2)).all():
            continue
        idx = np.frombuffer(g[vend:iend], "<u2").astype(np.int64)
        if idx.max(initial=0) >= vc:
            continue
        uv = None
        for o, t, u, i in elems:
            if u == 5 and i == 0 and t == 1:
                uv = raw[:, o:o + 8].copy().view("<f4").reshape(vc, 2)
        subs = _submeshes(g, hit - 24, ic, vc)
        out.append(Block(q, bb, pos, idx.reshape(-1, 3), uv, subs))
        p = iend
    return out


def frame_blocks(blob: bytes) -> dict[str, list[Block]]:
    """{visual frame name: its blocks}. A frame's geometry follows its
    'mesh_<frame>' record and runs to the next one."""
    import re
    g = _geometry(blob)
    marks = []
    for m in re.finditer(rb"\x00mesh_([A-Za-z0-9_.\-]{1,80})\x00", g):
        o = m.start() + 1
        if struct.unpack_from("<I", g, o - 4)[0] == len(m.group(0)) - 1:
            marks.append((o, m.group(1).decode()))
    bl = blocks(blob)
    out: dict[str, list[Block]] = {}
    for k, (o, name) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(g)
        mine = [b for b in bl if o < b.offset < end]
        if mine:
            head = g[o:mine[0].offset]
            mats = [m.group(1).decode() for m in re.finditer(rb"([A-Za-z0-9_]+_mat[0-9]*)\x00", head)]
            for b in mine:
                b.materials = mats
        out[name] = mine
    return out


def material_maps(blob: bytes) -> dict[str, dict[str, str]]:
    """{material name: {AlbedoMap: ..., NormalMap: ...}} from the mesh header XML."""
    import re
    n = struct.unpack_from("<I", blob, 0)[0]
    xml = blob[4:4 + n].decode("utf-8", "replace")
    out = {}
    for m in re.finditer(r"<Material([^>]*)/?>", xml):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        if "Name" in attrs:
            out[attrs["Name"]] = attrs
    return out


def write_obj(path, parts: list[Block]) -> None:
    with open(path, "w") as f:
        base = 1
        for k, b in enumerate(parts):
            f.write(f"o block_{k}\n")
            for x, y, z in b.positions:
                f.write(f"v {x:.5f} {y:.5f} {z:.5f}\n")
            for a, c, d in b.faces:
                f.write(f"f {a + base} {c + base} {d + base}\n")
            base += len(b.positions)
