"""fix_gateway_links.py -- correct map-to-map gateway links after a pack, and check the map.

WHY IT EXISTS. An installed map is called `mod_level_<name>`, but the editor's
pack writes a gateway link as `level_<name> || <zone>`, a level that does not
exist. Pressing Let's go then drops the player to the main menu. MEASURED and
tested in game 2026-11-04 (RE\\PATCH_LOG.md): with the real name in the pak's
zone_settings.json, a truck crosses both ways, with no region.

Every pack writes the bad link again, so after_pack.ps1 runs this each time.

It also stops on what else breaks a crossing, none of which the game reports:
  no ground layers    the game crashes the moment a truck arrives (11-04)
  no gate on partner  Saber's guide: crashes during the transfer
  zero cameraDir      Saber's guide: black screen

`levelZoneLink` is rewritten, and so is any objective reference to ANOTHER map: the
pack writes those as plain `level_x` too, so a stage on the far map can never be
reached (save showed `level_gate_two || base_site`, 2026-11-05). References to the
map's own zones keep their tag, which resolves.

The member keeps its exact length, because a pak is patched in place and cannot
grow: the 4 bytes `mod_` adds come out of that line's own indent.

    python fix_gateway_links.py <level.pak>            report only
    python fix_gateway_links.py <level.pak> --apply    write the correction

Idempotent. A map with no gateways is left alone. Exit 1 if a check failed.
"""
import json
import os
import re
import struct
import subprocess
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from patch_member import patch_member, read_member  # noqa: E402

MEMBER = 'zone_settings.json'
SCENE = '[scene]/data.stg'
TAG = '~#@%$`*'
CUTTER = ' || '
OBJECTIVES = 'objective_settings.json'
LINK_LINE = re.compile(rb'(?P<indent>[ \t]*)"levelZoneLink": "(?P<link>[^"\r\n]*)"')
REF_LINE = re.compile(rb'(?P<indent>[ \t]*)"(?P<key>globalZoneId|mapId|map)": "(?P<ref>level_[^"\r\n]*)"')


def split_link(link):
    """`<level> || <zone>` -> (level without tag or mod_ prefix, zone)."""
    level, _, zone = link.partition(CUTTER)
    level = level.replace(TAG, '').strip()
    if level.startswith('mod_'):
        level = level[4:]
    return level, zone.strip()


def installed_name(folder, level):
    """The pak stem for this level, spelled as it is on disk, or None."""
    want = (level + '.pak').lower()
    for name in os.listdir(folder):
        if name.lower() == want:
            return name[:-4]
    return None


def rewrite_links(blob, folder):
    """Return (new blob, [(old link, new link)], [links with no installed map])."""
    changed, missing = [], []

    def fix(m):
        old = m.group('link').decode('utf-8')
        level, zone = split_link(old)
        stem = installed_name(folder, level) if level else None
        if stem is None:
            if old:
                missing.append(old)
            return m.group()
        new = 'mod_%s%s%s' % (stem, CUTTER, zone)
        if new == old:
            return m.group()
        indent = m.group('indent')
        room = len(indent) - (len(new.encode('utf-8')) - len(m.group('link')))
        if room < 0:
            raise ValueError('no indent left to absorb the longer link: %s' % new)
        changed.append((old, new))
        return b' ' * room + b'"levelZoneLink": "' + new.encode('utf-8') + b'"'

    return LINK_LINE.sub(fix, blob), changed, missing


def rewrite_refs(blob, folder):
    """Objective references to another installed map, given their real name."""
    changed = []

    def fix(m):
        ref = m.group('ref').decode('utf-8')
        level, _, zone = ref.partition(CUTTER)
        stem = installed_name(folder, level.strip())
        if stem is None:
            return m.group()
        new = 'mod_' + stem + (CUTTER + zone if zone else '')
        room = len(m.group('indent')) - (len(new.encode('utf-8')) - len(m.group('ref')))
        if room < 0:
            raise ValueError('no indent left to absorb the longer reference: %s' % new)
        changed.append((ref, new))
        return b' ' * room + b'"' + m.group('key') + b'": "' + new.encode('utf-8') + b'"'

    return REF_LINE.sub(fix, blob), changed


def zones_of(blob):
    """{zone name: props} from a zone_settings.json."""
    doc = json.loads(blob.decode('utf-8-sig'))
    return {z.get('name'): z.get('props') or {} for z in doc['SslValue']['zoneDesc']}


def gateways_of(zones):
    return {name: props['ZonePropertyGateway'] for name, props in zones.items()
            if 'ZonePropertyGateway' in props}


def ground_layers(stg):
    """Terrain layer names from a data.stg: u32 count, then per layer f32 tiling,
    f32 contrast, i32 mask, u16 length, name. The first mask is -1, then 0, 1, 2."""
    for m in re.finditer(rb'\xff\xff\xff\xff(?=[\x03-\x40]\x00[a-z])', stg):
        q = m.start() - 8
        if q < 4:
            continue
        count = struct.unpack_from('<I', stg, q - 4)[0]
        if not 1 <= count <= 16:
            continue
        names = []
        try:
            for i in range(count):
                _, _, mask, length = struct.unpack_from('<ffiH', stg, q)
                raw = stg[q + 14:q + 14 + length]
                if mask != i - 1 or raw[-1:] != b'\0' or not re.fullmatch(rb'[ -~]+', raw[:-1]):
                    raise ValueError
                names.append(raw[:-1].decode('ascii'))
                q += 14 + length
        except (ValueError, struct.error):
            continue
        return names
    return []


def check_map(pak, blob):
    """Everything that breaks a crossing. Returns (failures, warnings)."""
    fails, warns = [], []
    folder = os.path.dirname(os.path.abspath(pak))
    me = os.path.basename(pak)[:-4]
    with zipfile.ZipFile(pak) as z:
        layers = ground_layers(z.read(SCENE))
    if not layers:
        fails.append('no ground layers in the compiled map: a truck arriving here crashes the '
                     'game. Give it four built-in layers')
    elif '\\' in layers[0]:
        fails.append('base ground layer %s is a custom tile: custom tiles can be dropped when '
                     'the map compiles. Use a built-in one' % layers[0])
    for name, gate in gateways_of(zones_of(blob)).items():
        link = gate.get('levelZoneLink') or ''
        if not link:
            fails.append('gate %s has no link' % name)
            continue
        cam = gate.get('cameraDir') or {}
        if not any(cam.get(k) for k in 'xyz'):
            fails.append('gate %s: cameraDir is all zeros, which gives a black screen' % name)
        for field in ('extraZoneToPlaceTrucks', 'extraZoneToPlaceWinch'):
            if gate.get(field):
                warns.append('gate %s: %s is set; extra zones are untested on custom maps'
                             % (name, field))
        level, zone = split_link(link)
        stem = installed_name(folder, level)
        if stem is None:
            continue    # reported once, by rewrite_links
        with zipfile.ZipFile(os.path.join(folder, stem + '.pak')) as z:
            there = zones_of(z.read(MEMBER))
        if zone not in there:
            fails.append('gate %s: %s has no zone called %s' % (name, stem, zone))
            continue
        partner = there[zone].get('ZonePropertyGateway')
        if partner is None:
            fails.append('gate %s: zone %s on %s has no ZonePropertyGateway, which crashes '
                         'the transfer' % (name, zone, stem))
            continue
        back = split_link(partner.get('levelZoneLink') or '')
        if (back[0].lower(), back[1]) != (me.lower(), name):
            warns.append('gate %s: %s on %s does not lead back here' % (name, zone, stem))
    return fails, warns


def game_running():
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Expeditions.exe'],
                         capture_output=True, text=True).stdout
    return 'expeditions.exe' in out.lower()


def write_member(pak, blob, member=MEMBER):
    """Patch a copy and swap it in, so a failure cannot leave half a pak."""
    tmp = pak + '.linkfix-tmp'
    try:
        with open(pak, 'rb') as src, open(tmp, 'wb') as dst:
            while True:
                chunk = src.read(1 << 24)
                if not chunk:
                    break
                dst.write(chunk)
        patch_member(tmp, member, blob, backup=False)
        os.replace(tmp, pak)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def run(pak, apply=False):
    """Returns the exit code."""
    with zipfile.ZipFile(pak) as z:
        info = next((i for i in z.infolist() if i.filename == MEMBER), None)
    if info is None:
        print('no zone settings; nothing to do')
        return 0
    if info.compress_type != zipfile.ZIP_STORED:
        print('REFUSING: %s is compressed, so it cannot be patched in place' % MEMBER)
        return 1
    blob = read_member(pak, MEMBER)[0]
    gates = gateways_of(zones_of(blob))
    if not gates:
        print('no gateways; nothing to do')
        return 0

    folder = os.path.dirname(os.path.abspath(pak))
    new, changed, missing = rewrite_links(blob, folder)
    for old, link in changed:
        print('will rename link: %s -> %s' % (old, link))
    for link in missing:
        print('WARNING: no installed map for link, left alone: %s' % link)
    fails, warns = check_map(pak, new)
    for w in warns:
        print('WARNING:', w)
    for f in fails:
        print('FAILED:', f)
    if not changed:
        print('%d gateway link(s) already correct' % len(gates))
    elif apply:
        if game_running():
            print('REFUSING: the game is running. Close it and run this again')
            return 1
        write_member(pak, new)
        print('wrote %d link(s)' % len(changed))
    fix_objective_refs(pak, folder, apply)
    fix_switch_copy(pak, folder, apply)
    return 1 if fails else 0


def fix_objective_refs(pak, folder, apply):
    with zipfile.ZipFile(pak) as z:
        if OBJECTIVES not in z.namelist():
            return
    new, changed = rewrite_refs(read_member(pak, OBJECTIVES)[0], folder)
    if not changed:
        return
    for old, ref in changed:
        print('will rename reference: %s -> %s' % (old, ref))
    if apply and not game_running():
        write_member(pak, new, OBJECTIVES)
        print('wrote %d reference(s)' % len(changed))


def fix_switch_copy(pak, folder, apply):
    """The Switch build keeps its own zone_settings.json, with the same bad link.
    The other platform paks hold none. Crossing on Switch is untested."""
    twin = pak[:-4] + '_stg_nx64.pak'
    if not os.path.exists(twin):
        return
    blob = read_member(twin, MEMBER)[0]
    new, changed, _ = rewrite_links(blob, folder)
    if not changed:
        return
    print('will rename %d link(s) in %s' % (len(changed), os.path.basename(twin)))
    if apply and not game_running():
        write_member(twin, new)
        print('wrote %s' % os.path.basename(twin))


def main(argv):
    paks = [a for a in argv[1:] if not a.startswith('--')]
    if len(paks) != 1:
        print(__doc__)
        return 2
    return run(paks[0], apply='--apply' in argv)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
