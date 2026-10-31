#!/usr/bin/env python3
"""Replace one pak member with a different-sized payload by splicing bytes.

Handles STORED (method 0, every level pak) and DEFLATE (method 8, every mod
pak), re-encoding into whichever method the member already uses. Grow or shrink.

WHY THIS EXISTS

`pak_patch.py` edits a member in place and works, but cannot grow one.
`repack_pak.py` can grow, because it rebuilds the archive with `zipfile` - and
a rebuilt pak is one the game will not list. Structurally the rebuilt file looks
identical (all members STORED, 4096-aligned, create_system=3, same order, same
local/central extra fields, same flags, zero comment, zero padding), so whatever
the loader objects to is something `zipfile` normalises silently on re-encode.

So: do not re-encode. Copy the original file byte for byte, cut out the one
member's data, drop the new bytes in, and fix up only what the change forces -
the member's sizes and CRC, every later local-header offset in the central
directory, and the EOCD. Everything else is the original file's own bytes.

    python pak_splice.py <pak> <member> <payload-file>
    python pak_splice.py <pak> --verify
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib

LFH, CEN, EOCD = 0x04034b50, 0x02014b50, 0x06054b50
PAD = 4096


def read_eocd(buf: bytes):
    i = buf.rfind(struct.pack("<I", EOCD))
    if i < 0:
        raise SystemExit("no EOCD - not a zip")
    n, cd_size, cd_off = struct.unpack("<HII", buf[i + 10:i + 20])
    cmt = struct.unpack("<H", buf[i + 20:i + 22])[0]
    return i, n, cd_size, cd_off, cmt


def splice(pak: str, member: str, payload: bytes, force_running: bool = False) -> None:
    # 🚨 THE GUARD BELONGS HERE, NOT ONLY IN main(). Added 2026-10-14 in main()
    # and the same day `register_mod_models.py` started calling splice() directly
    # -- straight past it. A guard on the CLI and not on the function protects
    # the one caller that already knew better.
    if not force_running:
        _refuse_if_game_running()
    data = bytearray(open(pak, "rb").read())
    orig_len = len(data)
    eocd_i, count, cd_size, cd_off, cmt_len = read_eocd(bytes(data))

    # locate the member in the central directory
    entries, p = [], cd_off
    for _ in range(count):
        if struct.unpack("<I", data[p:p + 4])[0] != CEN:
            raise SystemExit("central directory walk desynced")
        mth = struct.unpack("<H", data[p + 10:p + 12])[0]
        csz, usz = struct.unpack("<II", data[p + 20:p + 28])
        nlen, elen, clen = struct.unpack("<HHH", data[p + 28:p + 34])
        lho = struct.unpack("<I", data[p + 42:p + 46])[0]
        name = bytes(data[p + 46:p + 46 + nlen]).decode("utf-8", "replace")
        entries.append(dict(cen=p, name=name, method=mth, csz=csz, usz=usz,
                            lho=lho, nlen=nlen, elen=elen, clen=clen))
        p += 46 + nlen + elen + clen

    # Members can be stored with either separator. zipfile normalises a stored
    # "[scene]\data_for_ssl.bin" to "[scene]/data_for_ssl.bin" on read, so a
    # caller that got the name from zipfile will not match the raw bytes here.
    def _norm(x):
        return x.replace("\\", "/")

    tgt = next((e for e in entries if _norm(e["name"]) == _norm(member)), None)
    if tgt is None:
        raise SystemExit(f"{member!r} not in the pak. stored names: "
                         f"{[e['name'] for e in entries][:8]}")
    # STORED (level paks) and DEFLATE (every mod pak) are both accepted. The
    # method is NOT ours to choose: all 22 mod paks installed and working in
    # game are method 8, all level paks are method 0, so the one already on the
    # member is the one the game expects. Re-encode into that same method.
    if tgt["method"] not in (0, 8):
        raise SystemExit(f"{member!r} uses zip method {tgt['method']} - only STORED and DEFLATE")

    # where the member's DATA lives, via its local header
    lh = tgt["lho"]
    if struct.unpack("<I", data[lh:lh + 4])[0] != LFH:
        raise SystemExit("bad local header signature")
    lnlen, lelen = struct.unpack("<HH", data[lh + 26:lh + 30])
    dstart = lh + 30 + lnlen + lelen
    dend = dstart + tgt["csz"]
    if tgt["method"] == 0:
        blob = payload
    else:
        # raw deflate, no zlib header or trailer - that is what a zip stores
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        blob = co.compress(payload) + co.flush()
    delta = len(blob) - tgt["csz"]
    # the CRC and the uncompressed size are always of the PAYLOAD; only the
    # compressed size and the spliced bytes come from the encoded blob.
    crc = zlib.crc32(payload) & 0xffffffff
    print(f"  {member}: {tgt['csz']:,} -> {len(blob):,} B ({delta:+,}) "
          f"method={tgt['method']} uncompressed={len(payload):,}")

    # 1. swap the bytes
    data[dstart:dend] = blob

    # 2. the member's own local header (before the data, so it has NOT moved)
    #    and its central entry (after the data, so it HAS moved by delta).
    #    Writing the central entry at its pre-splice offset corrupts whatever
    #    now occupies that address and leaves the real entry stale - that is a
    #    Bad CRC-32 on read, which is exactly how this bug announced itself.
    struct.pack_into("<I", data, lh + 14, crc)
    struct.pack_into("<II", data, lh + 18, len(blob), len(payload))
    struct.pack_into("<I", data, tgt["cen"] + delta + 16, crc)
    struct.pack_into("<II", data, tgt["cen"] + delta + 20, len(blob), len(payload))

    # 3. every local-header offset after the edit shifts by delta.
    #    The whole central directory sits after the member data, so every
    #    central entry address shifts by delta too.
    moved = 0
    for e in entries:
        cen = e["cen"] + delta
        if e["lho"] > lh:
            struct.pack_into("<I", data, cen + 42, e["lho"] + delta)
            moved += 1

    # 4. the EOCD's central-directory offset
    new_eocd = eocd_i + delta
    struct.pack_into("<I", data, new_eocd + 16, cd_off + delta)
    print(f"  {moved} later member offset(s) shifted, EOCD cd_off {cd_off:,} -> {cd_off + delta:,}")

    # 5. re-pad to a 4096 boundary; the tail is zero padding, never data
    end = new_eocd + 22 + cmt_len
    body = bytes(data[:end])
    pad = (-len(body)) % PAD
    out = body + b"\x00" * pad
    print(f"  {orig_len:,} -> {len(out):,} B  4096-aligned={len(out) % PAD == 0}")

    bak = pak + ".pre-splice"
    shutil.copy2(pak, bak)
    tmp = pak + ".splice.tmp"
    open(tmp, "wb").write(out)

    # prove it: every other member byte-identical, target reads back exactly
    src, dst = zipfile.ZipFile(bak), zipfile.ZipFile(tmp)
    bad = dst.testzip()
    drift = [n for n in src.namelist()
             if n != member and src.read(n) != dst.read(n)]
    got = dst.read(member)
    order = src.namelist() == dst.namelist()
    src.close(); dst.close()
    ok = bad is None and not drift and got == payload and order
    print(f"  testzip={bad or 'OK'}  order_preserved={order}  "
          f"other members changed={drift or 'NONE'}  payload_readback={got == payload}")
    if not ok:
        os.remove(tmp)
        raise SystemExit("VERIFY FAILED - pak untouched")
    os.replace(tmp, pak)
    os.remove(bak)
    print("  spliced")


def _refuse_if_game_running() -> None:
    """A pak the game already has open must not be swapped under it.

    2026-10-14 07:55: this tool replaced BaseUpgradeDesc_PebbleGas.pak while
    Expeditions PID 24520 was live. The session played on with the OLD bytes from
    its open handle, so the change was never tested, and the owner's sell result
    got banked against the wrong version of the pak. That is the whole reason for
    this guard.

    The game must be closed before a pak is written. Not "should be": the write
    silently does nothing useful while it is open, because the running process
    keeps serving the bytes it already mapped.

    WHAT THIS GUARD IS *NOT* FOR. The first version of this comment also blamed a
    crash-on-close at 08:15:30 on the live write. That attribution was withdrawn
    the same day: the identical crash recurred at 09:25:43 with no pak write
    anywhere in the session, and both dumps carry 0 ssl frames and nothing but
    debug-var objects, i.e. the fault lands after the level has already unloaded.
    Do not reintroduce that claim and do not sell this guard as crash prevention.
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process -Name Expeditions*,*MudRunner* -ErrorAction SilentlyContinue |"
             " Select-Object -First 1 -ExpandProperty Id)"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
    except Exception:
        return  # never let the guard itself break the tool
    if out:
        sys.stderr.write(
            f"REFUSED: Expeditions is running (PID {out}).\n"
            "A running game keeps serving the pak bytes it already mapped, so the\n"
            "write would not be under test -- the session plays on with the old\n"
            "content and any result gets banked against the wrong version.\n"
            "(An earlier version of this message also blamed a crash-on-close on\n"
            "the write. That was withdrawn the same day: the identical crash\n"
            "recurred with no pak write in the session.)\n"
            "Close the game, and any crash-report window still holding the process,\n"
            "then run this again. Pass --force-running only if you mean it.\n"
        )
        raise SystemExit(3)


def main() -> int:
    argv = [a for a in sys.argv if a != "--force-running"]
    forced = "--force-running" in sys.argv
    if len(argv) == 3 and argv[2] == "--verify":
        z = zipfile.ZipFile(argv[1])
        print(z.testzip() or "OK")
        return 0
    if len(argv) != 4:
        print(__doc__)
        return 2
    if not forced:
        _refuse_if_game_running()
    splice(argv[1], argv[2], open(argv[3], "rb").read())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
