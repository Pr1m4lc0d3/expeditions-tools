"""Read and write the DDS rasters the Expeditions editor uses for terrain.

Two facts here were learned the hard way and are the reason this module exists:

* ``_height_source.png`` is **not a PNG**. Its magic bytes are ``DDS ``. Parsing
  it as PNG yields garbage dimensions and silently wrong data.
* Tile heightmaps are ``D3DFMT_R16F`` (fourCC 111): 16-bit *half floats*,
  normalized 0..1 against the level's ``MaxHeight``. They are not the 16-bit
  unsigned integers used by the baked ``height_map.dds``.

The write path deliberately preserves the original 128-byte header and asserts
that the output length equals the input length. A tile whose byte count changes
is a corrupt tile, and the editor will not tell you which one.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

HEADER_BYTES = 128
FOURCC_R16F = 111          # D3DFMT_R16F
FOURCC_L16 = 0             # 16-bit luminance uses masks, not a fourCC


def read_header(path: str | Path) -> dict:
    """Parse a DDS header. Raises if the file is not a DDS, whatever its extension."""
    raw = Path(path).read_bytes()[:HEADER_BYTES]
    if raw[:4] != b"DDS ":
        raise ValueError(f"{path} is not a DDS (magic {raw[:4]!r}); note that some "
                         f"Expeditions files carry a .png extension but are DDS")
    size, flags, height, width, pitch, depth, mips = struct.unpack_from("<7I", raw, 4)
    pf_size, pf_flags, fourcc, bits, rm, gm, bm, am = struct.unpack_from("<2I4s5I", raw, 76)[:8]
    return {
        "width": width, "height": height, "mips": mips,
        "fourcc": struct.unpack("<I", fourcc)[0],
        "bits": bits, "masks": (rm, gm, bm, am),
        "pixel_bytes": HEADER_BYTES,
    }


def read_r16f(path: str | Path) -> np.ndarray:
    """Read an R16F DDS as float32 in its stored (normalized) units."""
    p = Path(path)
    raw = p.read_bytes()
    hdr = read_header(p)
    if hdr["fourcc"] != FOURCC_R16F:
        raise ValueError(f"{p} is fourCC {hdr['fourcc']}, expected {FOURCC_R16F} (R16F)")
    w, h = hdr["width"], hdr["height"]
    expected = w * h * 2
    body = raw[HEADER_BYTES:HEADER_BYTES + expected]
    if len(body) != expected:
        raise ValueError(f"{p}: expected {expected} pixel bytes, found {len(body)}")
    return np.frombuffer(body, dtype="<f2").astype(np.float32).reshape(h, w)


def write_r16f(path: str | Path, data: np.ndarray) -> int:
    """Overwrite an existing R16F DDS in place, keeping its header verbatim.

    Refuses to write if the resulting file length would differ from the original.
    The editor gives no diagnostic for a malformed tile, so the check has to
    happen here or not at all.
    """
    p = Path(path)
    raw = p.read_bytes()
    hdr = read_header(p)
    w, h = hdr["width"], hdr["height"]
    if data.shape != (h, w):
        raise ValueError(f"{p}: data is {data.shape}, tile is {(h, w)}")
    out = raw[:HEADER_BYTES] + data.astype("<f2").tobytes()
    if len(out) != len(raw):
        raise ValueError(f"{p}: refusing to write, length would change {len(raw)} -> {len(out)}")
    p.write_bytes(out)
    return len(out)


def create_r16f(path: str | Path, data: np.ndarray, template: str | Path) -> int:
    """Write an R16F DDS at a NEW size, cloning `template`'s header and patching dims.

    ``write_r16f`` deliberately refuses to change dimensions, because a tile whose
    byte count changes silently is a corrupt tile. This is the explicit escape
    hatch for the one case that legitimately needs it: replacing a level's
    ``_height_source`` with a different-resolution terrain.

    DDS header layout used here: height at offset 12, width at 16, pitch at 20.
    """
    tmpl = Path(template).read_bytes()[:HEADER_BYTES]
    if tmpl[:4] != b"DDS ":
        raise ValueError(f"{template} is not a DDS")
    h, w = int(data.shape[0]), int(data.shape[1])
    hdr = bytearray(tmpl)
    struct.pack_into("<I", hdr, 12, h)
    struct.pack_into("<I", hdr, 16, w)
    struct.pack_into("<I", hdr, 20, w * 2)          # pitch, 2 bytes per texel
    out = bytes(hdr) + data.astype("<f2").tobytes()
    Path(path).write_bytes(out)
    check = read_header(path)
    if (check["width"], check["height"]) != (w, h):
        raise RuntimeError(f"header did not take: wrote {w}x{h}, reads back {check}")
    return len(out)


def read_l16(path: str | Path) -> np.ndarray:
    """Read a 16-bit luminance DDS (the *baked* height_map.dds) as 0..1 floats."""
    p = Path(path)
    hdr = read_header(p)
    if hdr["bits"] != 16:
        raise ValueError(f"{p}: expected 16 bits per pixel, found {hdr['bits']}")
    w, h = hdr["width"], hdr["height"]
    body = Path(p).read_bytes()[HEADER_BYTES:HEADER_BYTES + w * h * 2]
    return (np.frombuffer(body, dtype="<u2").astype(np.float32) / 65535.0).reshape(h, w)
