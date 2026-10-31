"""Fail a model whose baked normal map points backward on its own surface.

    python check_normal_map.py <low-poly .glb> <normal .tga>

A tangent-space normal can never face away from its surface (blue below 128).
Chuck-It's first build shipped 30% of its island texels that way, from a normal
bake whose rays started inside the neighbouring geometry, and in game every
face read as a patchwork of dark triangles (2026-10-27). Every model the owner
has approved measures under 1%. Only texels inside the UV islands count: the
empty space between islands is black, which decodes as "backward" too.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import numpy as np

LIMIT_PCT = 3.0


def glb_uv_triangles(glb: Path) -> np.ndarray:
    """(n, 3, 2) UV triangles of every primitive in a .glb."""
    data = glb.read_bytes()
    jlen = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20:20 + jlen])
    bin_off = 20 + jlen + 8
    types = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
    sizes = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}

    def acc(i):
        a = doc["accessors"][i]
        bv = doc["bufferViews"][a["bufferView"]]
        dt, k = types[a["componentType"]], sizes[a["type"]]
        start = bin_off + bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = bv.get("byteStride", 0) or np.dtype(dt).itemsize * k
        raw = np.frombuffer(data, np.uint8, a["count"] * stride, start).reshape(a["count"], stride)
        return raw[:, :np.dtype(dt).itemsize * k].copy().view(dt).reshape(a["count"], k)

    out = []
    for m in doc["meshes"]:
        for p in m["primitives"]:
            if "TEXCOORD_0" not in p["attributes"]:
                continue
            uv = acc(p["attributes"]["TEXCOORD_0"])
            idx = acc(p["indices"]).ravel() if "indices" in p else np.arange(len(uv))
            out.append(uv[idx.reshape(-1, 3)])
    return np.concatenate(out)


def backward_pct(glb: Path, tga: Path) -> float:
    from PIL import Image, ImageDraw
    from scipy import ndimage
    n = np.asarray(Image.open(tga).convert("RGB"), np.float32) / 255
    R = n.shape[0]
    im = Image.new("L", (n.shape[1], R), 0)
    d = ImageDraw.Draw(im)
    for t in glb_uv_triangles(glb):  # glTF UV origin is top-left, like the image
        d.polygon([(u * n.shape[1], v * R) for u, v in t], fill=255)
    m = ndimage.binary_erosion(np.asarray(im) > 0, iterations=1)
    z = n[..., 2][m] * 2 - 1
    return 100.0 * float((z < 0).mean())


def gate(glb: Path, tga: Path) -> tuple[bool, str]:
    pct = backward_pct(glb, tga)
    return pct <= LIMIT_PCT, f"{pct:.1f}% of island texels point backward (limit {LIMIT_PCT}%)"


if __name__ == "__main__":
    ok, msg = gate(Path(sys.argv[1]), Path(sys.argv[2]))
    print(("PASS  " if ok else "FAIL  ") + msg)
    sys.exit(0 if ok else 1)
