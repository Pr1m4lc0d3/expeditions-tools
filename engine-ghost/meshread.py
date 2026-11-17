"""Read a converted Saber mesh (the game's prebuild/meshes/* format) far enough to measure a truck:
every frame's world transform (the parent chain composed) and every vertex in truck space.

Frame record: u32, i16 parent, u16 index, u32, u32 name length, name (NUL-terminated), 4x4 float32,
row-vector (translation in row 3). Vertex blocks come from the shared saber.stock_mesh reader.
"""
from __future__ import annotations

import re
import struct

import numpy as np


def frames(blob: bytes) -> dict[str, dict]:
    """{name: {"index", "parent", "local"(4x4), "world"(4x4)}} for every frame record"""
    n = struct.unpack_from("<I", blob, 0)[0]
    geo = blob[4 + n:]
    recs = {}
    for m in re.finditer(rb"\x00([A-Za-z_][A-Za-z0-9_.\- ]{0,80})\x00", geo):
        o, name = m.start() + 1, m.group(1).decode()
        if o < 12 or struct.unpack_from("<I", geo, o - 4)[0] != len(name) + 1:
            continue
        M = np.array(struct.unpack_from("<16f", geo, o + len(name) + 1)).reshape(4, 4)
        if not (np.isfinite(M).all() and abs(M[3, 3] - 1) < 1e-3 and np.abs(M).max() < 1e4):
            continue
        parent, index = struct.unpack_from("<hH", geo, o - 12)
        if name not in recs:
            recs[name] = {"order": len(recs), "index": index, "parent": parent, "local": M}
    # a parent number counts frame records in file order (the index field disagrees on some mod meshes:
    # measured 11-17 on a ported Jeep, whose axles landed 12 m away when read by index)
    by_index = {r["order"]: r for r in recs.values()}

    def world(r, depth=0):
        if "world" in r:
            return r["world"]
        p = by_index.get(r["parent"]) if r["parent"] >= 0 and depth < 64 else None
        r["world"] = r["local"] @ world(p, depth + 1) if p is not None and p is not r else r["local"]
        return r["world"]
    for r in recs.values():
        world(r)
    return recs


def points(blob: bytes, skip=()) -> dict[str, np.ndarray]:
    """{frame: (n, 3) vertices in truck space}, frames whose name contains any of `skip` left out"""
    from saber.stock_mesh import frame_blocks
    F = frames(blob)
    box = bounds(blob)
    out = {}
    for name, blocks in frame_blocks(blob).items():
        if any(s in name.lower() for s in skip):
            continue
        P = [b.positions.astype(float) for b in blocks if len(b.positions)]
        if not P:
            continue
        P = np.concatenate(P)
        out[name] = _place(P, F.get(name), box)
    return out


def _place(P: np.ndarray, rec: dict | None, box: np.ndarray) -> np.ndarray:
    """a frame's vertices in truck space. Converted meshes disagree: some store a part relative to its
    frame's whole parent chain (a truck-builder body: +1 m under ChassisBone_cdt), some already in truck
    space (a ported Jeep: the chain would throw it 1.2 m out of the mesh). Pick the reading that keeps
    the most vertices inside the mesh's own header bounds -- the box the game itself uses."""
    cands = []
    if rec is not None:                    # the full chain first: on a tie it wins (a windshield sits inside
        for M in (rec["world"], rec["local"]):     # the bounds both raw and placed; only placed is right)
            cands.append(P @ M[:3, :3] + M[3, :3])
    cands.append(P)
    lo, hi = box[:3] - 0.02, box[3:] + 0.02
    inside = [np.mean(np.all((C >= lo) & (C <= hi), axis=1)) for C in cands]
    return cands[int(np.argmax(inside))]


def bounds(blob: bytes) -> np.ndarray:
    """the whole mesh's bounds as the game reads them (min xyz, max xyz), right after the magic"""
    n = struct.unpack_from("<I", blob, 0)[0]
    return np.array(struct.unpack_from("<6f", blob, 4 + n + 8))
