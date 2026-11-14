"""saberscene — decoder for Saber's `[scene]/data_for_ssl.bin`.

WHY THIS EXISTS
---------------
Saber ships NO source for their own levels: there is no `zone_settings.json` or
`objective_settings.json` in any shipped `.pak`, and all 58 `_zones.xml` /
`_sounds.xml` files under `[prebuild]/references/` are 10-11 byte empty stubs.
The only machine-readable record of how Saber actually builds a level is the
per-level `[scene]/data_for_ssl.bin` blob. This module reads it.

FORMAT (established 2026-09-16 by hex inspection of level_exp_us_01_01)
-----------------------------------------------------------------------
A string is:   u16-LE length  +  bytes  +  NUL
The length **includes the NUL terminator**, which is the off-by-one that makes
naive length-prefix probes report zero matches. Everything between strings is
fixed-width numeric payload, predominantly f32 (transforms, radii, extents).

    13 00  "level_exp_us_01_01\0"     0x13 = 19 = 18 chars + NUL
    11 00  "mapAirdropZone48\0"       0x11 = 17 = 16 chars + NUL

Strings are only ~1.2% of the file; the rest is geometry. Zone *properties*
(capacity, slotsSettings) live in the numeric payload and are NOT yet decoded.
"""
import os
import re
import struct
import zipfile
import collections

from . import gamepaths  # noqa: E402
GAME_PAKS = os.path.join(str(gamepaths.game_dir('expeditions') or '.'), 'preload', 'paks', 'client')
SCENE_MEMBER = '[scene]/data_for_ssl.bin'


def tokenize(data, min_len=2, max_len=256):
    """Yield (offset, text, gap_before) for every length-prefixed string."""
    out = []
    i, last_end, n = 0, 0, len(data)
    while i < n - 2:
        ln = data[i] | (data[i + 1] << 8)
        if min_len <= ln <= max_len and i + 2 + ln <= n:
            body = data[i + 2: i + 2 + ln]
            if len(body) > 1 and body[-1] == 0 and all(32 <= c < 127 for c in body[:-1]):
                out.append((i, body[:-1].decode('ascii'), i - last_end))
                i += 2 + ln
                last_end = i
                continue
        i += 1
    return out


def floats_after(data, offset, count=8):
    """Read f32s immediately following a token, for inspecting a record's payload."""
    vals = []
    for k in range(count):
        o = offset + k * 4
        if o + 4 <= len(data):
            v = struct.unpack_from('<f', data, o)[0]
            vals.append(v if abs(v) < 1e12 else None)
    return vals


def level_paks(root=GAME_PAKS):
    return [os.path.join(root, f) for f in sorted(os.listdir(root))
            if f.startswith('level_') and f.endswith('.pak')]


def read_scene(pak):
    with zipfile.ZipFile(pak) as z:
        if SCENE_MEMBER not in z.namelist():
            return None
        return z.read(SCENE_MEMBER)


def zone_names(data):
    """Saber's zone/locator identifiers are SCREAMING_SNAKE_CASE."""
    return [t[1] for t in tokenize(data) if re.match(r'^[A-Z][A-Z0-9_]{5,}$', t[1])]


def survey(root=GAME_PAKS):
    """Aggregate every vanilla level: {string: occurrences} plus per-level counts."""
    every = collections.Counter()
    per = {}
    for pak in level_paks(root):
        data = read_scene(pak)
        if data is None:
            continue
        toks = tokenize(data)
        per[os.path.basename(pak)] = len(toks)
        for _, txt, _ in toks:
            every[txt] += 1
    return every, per


# Verified findings, kept with the code so they are not lost to a dead session.
FINDINGS = {
    'saber_never_uses_itemName':
        'Across 25 vanilla levels and 8222 distinct strings, NONE of Anchor, '
        'Anchor_not_removable, Jack_screw, Scrap_For_Workshop, Arizona_Treasure_1 '
        'or Eagle_Egg appears. Saber identifies loot by TYPE only. This matches the '
        'docs marking "Inventory Id to bring" as "Currently not added".',
    'trade_is_treasure_only':
        '_TRADE twins pair with TREASURE zones (34/34) and nothing else. 106 plain '
        'LOOT_xx / CACHE_xx zones have no twin, so _TRADE is a sell mechanic, NOT '
        'what enables taking items from a box.',
    'zone_suffix_vocabulary':
        '_NAME 85, _BLOCK 76, _GIVER 75, _INT 74, _TRADE 65, _START 46, _EXTRA 31, '
        '_SCOUTING 16, _FINISH 14, _ZONE 12, _LOOT 11, _TASKGIVER 8, _DEPLOY 5, _FOB 4.',
    'not_yet_decoded':
        'Zone PROPERTIES (capacity, heavyInvCapacity, slotsSettings) live in the '
        'numeric payload between strings and are not decoded yet.',
}
