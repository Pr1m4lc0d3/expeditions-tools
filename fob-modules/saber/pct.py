"""The PICT (.pct) texture container, and the .pct_header the pak carries beside it.

WHY THIS EXISTS. A pak declares a texture by its `.pct_header` tag and carries
TWO members: the full `.pct` and a small `.pct_header`. The editor emits both
for a level's own terrain textures and emits NEITHER for a custom model's
textures, so a map shipped with a custom model carries geometry and no skin.
Building the header is the missing step, and it cannot be a plain truncation.

THE FORMAT, decoded from the game's own files on 2026-10-08. A .pct is a linked
list of tagged fields, NOT a fixed header plus payload:

    <u16 tag> <u32 next_offset> <payload bytes, up to next_offset>

    tag     meaning                                   payload
    0x00f0  magic                                     "PICT" (stored "TCIP")
    0x0102  dimensions                                u32 width, u32 height, 1, 1
    0x00f2  pixel format                              u32
    0x00f9  mip count                                 u32
    0x0107  mip table                                 u32 pairs
    0x00ff  PIXEL DATA -- next_offset is its end      the image
    0x0001  TERMINATOR -- next_offset is the file end

A `.pct_header` is the same chain with the data chunk REPLACED by a terminator:
everything up to the 0x00ff field, then 0x0001 pointing at the new end. So the
header length varies with the mip table -- 72 B for a single-mip terrain
texture, 120 B for a 7-mip one, 144 B for the culvert's 10 mips. A naive
truncation to 72 bytes reproduces none of them.

⛔ The first 62 bytes of header and .pct are identical, which makes a plain
truncation look right at a glance. It is wrong in the last 6 bytes of every
file. `header_from_pct` is verified against all 7 header members the editor
itself wrote into `level_shafer_trail.pak`, byte for byte, with a negative
control proving truncation fails. Run `python test_pct_header.py`.
"""
from __future__ import annotations

import struct
from typing import Iterator

MAGIC = b"TCIP"               # "PICT" as stored, little-endian word order

TAG_MAGIC = 0x00F0
TAG_DIMS = 0x0102
TAG_FORMAT = 0x00F2
TAG_MIPS = 0x00F9
TAG_MIPTABLE = 0x0107
TAG_DATA = 0x00FF
TAG_END = 0x0001

FIELD = struct.Struct("<HI")   # tag, next_offset


class NotPct(ValueError):
    """The bytes are not a PICT container."""


def walk(blob: bytes, limit: int | None = None) -> Iterator[tuple[int, int, int]]:
    """Yield (offset, tag, next_offset) for each field in the chain.

    Stops after the data chunk or the terminator: both are final, and
    `next_offset` on those points at an end rather than at another field.
    """
    stop = len(blob) if limit is None else min(limit, len(blob))
    off = 0
    while off + FIELD.size <= stop:
        tag, nxt = FIELD.unpack_from(blob, off)
        yield off, tag, nxt
        if tag in (TAG_END, TAG_DATA) or nxt <= off:
            return
        off = nxt


def fields(blob: bytes) -> dict[int, bytes]:
    """Map tag -> payload bytes, for the metadata fields."""
    out = {}
    for off, tag, nxt in walk(blob):
        if tag in (TAG_END, TAG_DATA):
            break
        out[tag] = blob[off + FIELD.size:nxt]
    return out


def dimensions(blob: bytes) -> tuple[int, int]:
    """(width, height) from the 0x0102 field."""
    f = fields(blob)
    if TAG_DIMS not in f or len(f[TAG_DIMS]) < 8:
        raise NotPct("no dimensions field")
    return struct.unpack_from("<II", f[TAG_DIMS], 0)


def mip_count(blob: bytes) -> int:
    f = fields(blob)
    if TAG_MIPS not in f:
        raise NotPct("no mip-count field")
    return struct.unpack_from("<I", f[TAG_MIPS], 0)[0]


def is_pct(blob: bytes) -> bool:
    return len(blob) > FIELD.size + 4 and blob[6:10] == MAGIC


def data_offset(blob: bytes) -> int:
    """Byte offset of the 0x00ff pixel-data field."""
    for off, tag, _ in walk(blob):
        if tag == TAG_DATA:
            return off
    raise NotPct("no 0x00ff pixel-data field -- is this already a .pct_header?")


def header_from_pct(blob: bytes) -> bytes:
    """Build the `.pct_header` member that belongs beside this `.pct`.

    The metadata chain verbatim, with the pixel-data field replaced by a
    terminator whose next_offset is the header's own length.
    """
    if not is_pct(blob):
        raise NotPct("missing PICT magic at offset 6")
    off = data_offset(blob)
    return blob[:off] + FIELD.pack(TAG_END, off + FIELD.size)


def describe(blob: bytes) -> str:
    """One-line summary, for tool output."""
    try:
        w, h = dimensions(blob)
        n = mip_count(blob)
        chain = " ".join("%04x@%d" % (t, o) for o, t, _ in walk(blob))
        return "%dx%d, %d mip(s), chain %s" % (w, h, n, chain)
    except (NotPct, struct.error) as exc:
        return "unreadable: %s" % exc
