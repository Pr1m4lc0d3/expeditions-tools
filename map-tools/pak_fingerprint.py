"""pak_fingerprint.py -- capture a complete, diffable description of a .pak.

Purpose: take a fingerprint of a hand-built pak, let the editor repack the same
map, fingerprint again, and diff. Every byte-level difference between "mine" and
"the editor's" then becomes visible instead of guessed at.

Captures: file size and block alignment, trailing padding, EOCD fields, and for
every member -- name, sizes, CRC, content SHA1, compression, all header fields,
timestamps, and the raw local-file-header bytes.
"""
import os
import json
import struct
import hashlib
import zipfile


def _eocd(path):
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        window = min(size, 1 << 20)          # padding can be large; look back 1 MB
        f.seek(size - window)
        tail = f.read()
    k = tail.rfind(b'PK\x05\x06')
    if k < 0:
        return {'found': False}
    e = struct.unpack_from('<IHHHHIIH', tail, k)
    off = size - len(tail) + k
    return {
        'found': True,
        'file_offset': off,
        'disk': e[1],
        'cd_entries_total': e[3],
        'cd_size': e[5],
        'cd_offset': e[6],
        'comment_len': e[7],
        'bytes_after_eocd': size - (off + 22),
    }


def _local_header(f, offset):
    f.seek(offset)
    raw = f.read(30)
    if len(raw) < 30 or raw[:4] != b'PK\x03\x04':
        return {'raw': raw.hex(' '), 'valid_sig': False}
    (sig, ver, flag, comp, mtime, mdate, crc,
     csize, usize, nlen, elen) = struct.unpack('<IHHHHHIIIHH', raw)
    extra = b''
    if elen:
        f.seek(offset + 30 + nlen)
        extra = f.read(elen)
    return {
        'valid_sig': True, 'extract_version': ver, 'flag_bits': flag,
        'compress_type': comp, 'crc': crc, 'csize': csize, 'usize': usize,
        'name_len': nlen, 'extra_len': elen, 'extra': extra.hex(' '),
        'raw30': raw.hex(' '),
    }


def fingerprint(path):
    size = os.path.getsize(path)
    out = {
        'path': path,
        'name': os.path.basename(path),
        'size': size,
        'align_4096': size % 4096,
        'is_4096_aligned': size % 4096 == 0,
        'sha1_whole_file': None,
        'eocd': _eocd(path),
        'entries': [],
    }
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    out['sha1_whole_file'] = h.hexdigest()

    # trailing padding: how many zero bytes at the very end
    with open(path, 'rb') as f:
        f.seek(max(0, size - 65536))
        tail = f.read()
    pad = 0
    for b in reversed(tail):
        if b != 0:
            break
        pad += 1
    out['trailing_zero_bytes'] = pad

    z = zipfile.ZipFile(path)
    out['testzip'] = z.testzip()
    out['archive_comment'] = z.comment.hex(' ')
    with open(path, 'rb') as f:
        for i in z.infolist():
            data = z.read(i.filename)
            out['entries'].append({
                'filename': i.filename,
                'file_size': i.file_size,
                'compress_size': i.compress_size,
                'compress_type': i.compress_type,
                'CRC': i.CRC,
                'sha1': hashlib.sha1(data).hexdigest(),
                'date_time': list(i.date_time),
                'create_system': i.create_system,
                'create_version': i.create_version,
                'extract_version': i.extract_version,
                'flag_bits': i.flag_bits,
                'internal_attr': i.internal_attr,
                'external_attr': i.external_attr,
                'header_offset': i.header_offset,
                'extra_cd': i.extra.hex(' '),
                'comment': i.comment.hex(' '),
                'local_header': _local_header(f, i.header_offset),
            })
    return out


def save(path, out_json):
    fp = fingerprint(path)
    with open(out_json, 'w', encoding='utf8') as f:
        json.dump(fp, f, indent=2)
    return fp


def diff(a_json, b_json):
    """Return a list of human-readable differences between two fingerprints."""
    a = json.load(open(a_json, encoding='utf8'))
    b = json.load(open(b_json, encoding='utf8'))
    d = []
    for k in ('size', 'is_4096_aligned', 'align_4096', 'trailing_zero_bytes',
              'testzip', 'archive_comment'):
        if a.get(k) != b.get(k):
            d.append('FILE  %-22s %s -> %s' % (k, a.get(k), b.get(k)))
    for k in ('found', 'cd_entries_total', 'cd_size', 'cd_offset',
              'comment_len', 'bytes_after_eocd'):
        if a['eocd'].get(k) != b['eocd'].get(k):
            d.append('EOCD  %-22s %s -> %s' % (k, a['eocd'].get(k), b['eocd'].get(k)))

    ea = {e['filename']: e for e in a['entries']}
    eb = {e['filename']: e for e in b['entries']}
    for n in sorted(set(ea) - set(eb)):
        d.append('ENTRY only in A: %s (%d bytes)' % (n, ea[n]['file_size']))
    for n in sorted(set(eb) - set(ea)):
        d.append('ENTRY only in B: %s (%d bytes)' % (n, eb[n]['file_size']))
    for n in sorted(set(ea) & set(eb)):
        for k in ('file_size', 'compress_type', 'sha1', 'create_system',
                  'create_version', 'extract_version', 'flag_bits',
                  'internal_attr', 'external_attr', 'date_time', 'extra_cd',
                  'comment'):
            if ea[n].get(k) != eb[n].get(k):
                d.append('ENTRY %-34s %-16s %s -> %s'
                         % (n[:34], k, ea[n].get(k), eb[n].get(k)))
        la, lb = ea[n]['local_header'], eb[n]['local_header']
        for k in ('extract_version', 'flag_bits', 'compress_type', 'extra_len', 'extra'):
            if la.get(k) != lb.get(k):
                d.append('LFH   %-34s %-16s %s -> %s'
                         % (n[:34], k, la.get(k), lb.get(k)))
    return d
