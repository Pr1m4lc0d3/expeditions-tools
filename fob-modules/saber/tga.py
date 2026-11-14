"""Read and write the editor's TGA maps in place.

Every map the editor keeps beside the heightfield is an uncompressed 32-bit TGA
with a top-left origin (header descriptor 0x28) storing BGRA, not RGBA. Getting
the channel order wrong is invisible in a file listing and obvious the moment
the terrain renders, so ``read``/``write`` deal in RGBA and do the swap here.

``write`` refuses to change a file's dimensions. These maps are addressed by the
engine at fixed resolutions that differ per purpose - 1008 for blends, 651 for
water, 147 for quick mud, 21 for the per-block material layout - and the tiles
on the map's edge carry one extra row or column. Writing a "corrected" size
produces a file the editor loads and silently misreads.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np


def read_header(path: str | Path) -> dict:
    b = Path(path).open("rb").read(18)
    idlen, cmaptype, imgtype = b[0], b[1], b[2]
    w, h = struct.unpack("<HH", b[12:16])
    bpp, desc = b[16], b[17]
    if imgtype != 2:
        raise ValueError(f"{path}: expected uncompressed true-colour TGA (type 2), got {imgtype}")
    return {"idlen": idlen, "width": w, "height": h, "bpp": bpp, "desc": desc,
            "offset": 18 + idlen, "top_down": bool(desc & 0x20)}


def read(path: str | Path) -> np.ndarray:
    """Return HxWx4 RGBA uint8 (or HxWx3 for a 24-bit file), north-up."""
    p = Path(path)
    hdr = read_header(p)
    ch = hdr["bpp"] // 8
    raw = p.open("rb").read()
    body = np.frombuffer(raw[hdr["offset"]:hdr["offset"] + hdr["width"] * hdr["height"] * ch],
                         dtype=np.uint8).reshape(hdr["height"], hdr["width"], ch)
    out = body[:, :, [2, 1, 0] + ([3] if ch == 4 else [])]     # BGRA -> RGBA
    return out if hdr["top_down"] else out[::-1]


def create(path: str | Path, rgba: np.ndarray) -> Path:
    """Write a new 32-bit top-down TGA, the layout the editor itself writes.

    Needed for distribution maps: a map that has never had a distribution has no
    ``dstr_*.tga`` at all, so there is no existing file whose dimensions can be
    preserved.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    h, w = rgba.shape[:2]
    if rgba.shape[2] == 3:
        rgba = np.dstack([rgba, np.full((h, w), 255, np.uint8)])
    hdr = bytes([0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0]) + \
        struct.pack("<HHBB", w, h, 32, 0x28)        # 0x28 = top-left origin, 8 alpha bits
    body = rgba[:, :, [2, 1, 0, 3]].astype(np.uint8)
    p.open("wb").write(hdr + body.tobytes())
    return p


def write(path: str | Path, rgba: np.ndarray) -> Path:
    """Overwrite a TGA in place, keeping its exact dimensions and bit depth."""
    p = Path(path)
    hdr = read_header(p)
    ch = hdr["bpp"] // 8
    if rgba.shape[0] != hdr["height"] or rgba.shape[1] != hdr["width"]:
        raise ValueError(f"{p.name}: is {hdr['width']}x{hdr['height']}, refusing to write "
                         f"{rgba.shape[1]}x{rgba.shape[0]} - the engine expects the stored size")
    a = rgba
    if a.shape[2] == 3 and ch == 4:
        a = np.dstack([a, np.full(a.shape[:2], 255, np.uint8)])
    elif a.shape[2] == 4 and ch == 3:
        a = a[:, :, :3]
    if not hdr["top_down"]:
        a = a[::-1]
    body = a[:, :, [2, 1, 0] + ([3] if ch == 4 else [])]        # RGBA -> BGRA
    raw = bytearray(p.open("rb").read())
    start = hdr["offset"]
    end = start + hdr["width"] * hdr["height"] * ch
    raw[start:end] = body.astype(np.uint8).tobytes()
    p.open("wb").write(bytes(raw))
    return p
