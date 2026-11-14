"""patch_member.py -- rewrite a file INSIDE a .pak without rezipping it.

Rezipping a pak with Python has so far produced maps the game refuses to list,
while a CRC-correct in-place byte edit is accepted. So: edit in place, keep every
offset identical, and pad the member back to its original byte length.

JSON tolerates trailing whitespace after the closing brace, so any new content
shorter than the original can be padded with spaces. Content must never grow.
"""
import os
import zlib
import struct
import shutil
import zipfile
import time


def read_member(pak, member):
    z = zipfile.ZipFile(pak)
    info = z.getinfo(member)
    with open(pak, 'rb') as f:
        f.seek(info.header_offset)
        nlen, elen = struct.unpack_from('<HH', f.read(30), 26)
        data_off = info.header_offset + 30 + nlen + elen
        f.seek(data_off)
        blob = f.read(info.file_size)
    z.close()
    return blob, data_off, info


def patch_member(pak, member, new_bytes, pad=b' ', backup=True):
    """Replace a member's bytes in place. Returns (old_len, new_len, padding)."""
    blob, data_off, info = read_member(pak, member)
    if len(new_bytes) > len(blob):
        raise ValueError('new content is %d bytes larger than the original (%d > %d); '
                         'in-place patching cannot grow a member'
                         % (len(new_bytes) - len(blob), len(new_bytes), len(blob)))
    padding = len(blob) - len(new_bytes)
    final = new_bytes + pad * padding

    if backup:
        shutil.copy2(pak, pak + '.pre-patch-' + time.strftime('%Y%m%d-%H%M%S') + '-bak')

    data = bytearray(open(pak, 'rb').read())
    data[data_off:data_off + len(blob)] = final
    crc = zlib.crc32(final) & 0xFFFFFFFF

    # local file header CRC
    struct.pack_into('<I', data, info.header_offset + 14, crc)
    # The central directory header, MATCHED BY LOCAL HEADER OFFSET.
    #
    # Matching by NAME silently fails here and corrupts the pak. The editor
    # writes central directory names with MIXED separators -- the real bytes
    # are `[scene]\data_for_ssl.bin` and `classes/models\corrugated_culvert.xml`
    # -- while zipfile normalises every one to forward slashes when it reads
    # them. So `member` never equalled the stored name, ZERO central entries
    # were updated, the local header got the new CRC and the central directory
    # kept the old one, and the archive came back Bad CRC-32. A 164 MB pak was
    # corrupted exactly this way before the cause was found.
    #
    # The local header offset is a number and cannot be normalised.
    fixed = 0
    eocd = data.rfind(b'PK\x05\x06')
    count, _cdsize, cdoff = struct.unpack_from('<HII', data, eocd + 10)
    q = cdoff
    for _ in range(count):
        if bytes(data[q:q + 4]) != b'PK\x01\x02':
            break
        nl, el, cl = struct.unpack_from('<HHH', data, q + 28)
        if struct.unpack_from('<I', data, q + 42)[0] == info.header_offset:
            struct.pack_into('<I', data, q + 16, crc)
            fixed += 1
        q += 46 + nl + el + cl
    if not fixed:
        raise RuntimeError('no central directory entry points at local header %d '
                           'for %s; refusing to write a half-patched pak'
                           % (info.header_offset, member))
    open(pak, 'wb').write(bytes(data))

    z = zipfile.ZipFile(pak)
    ok = z.testzip() is None and z.read(member) == final
    z.close()
    if not ok:
        raise RuntimeError('verification failed after patching %s' % member)
    return len(blob), len(new_bytes), padding
