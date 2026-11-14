"""pack_map.py -- build an Expeditions level .pak without the editor.

Anatomy of a packed level (read off level_objectives.pak, 2026-09-16):
    ui/textures/*.png                       preview art (optional)
    pak.load_list / console_pak.load_list    flat index: u32-LE length + path
    [textures]/pct/<MAP>_<map>.pct(+_header) terrain textures, name-prefixed
    [scene]/data.stg, [scene]/data_for_ssl.bin
    level_desc.json, zone_settings.json, objective_settings.json,
    inventory_rand_settings.json, user_name_of_map.txt, content_settings.json
All members are STORED (compress_type 0), not deflated.

KEY CONSTRAINT: the load_list stores each path with a u32 length prefix. Renaming
the map inside it only stays valid if the new name is the SAME LENGTH as the old
one; otherwise every affected length field must be rewritten. Cloning a donor pak
with an equal-length name is therefore the safe route.
"""
import io, os, sys, json, zipfile, shutil, time

MEDIA = os.path.join(os.path.expanduser('~'), 'Documents', 'My Games', 'Expeditions', 'Media')
MODS = os.path.join(MEDIA, 'Mods')


def clone_pak(donor_pak, donor_name, new_name, level_dir, out_pak,
              json_files=('level_desc.json', 'zone_settings.json',
                          'objective_settings.json', 'inventory_rand_settings.json',
                          'content_settings.json')):
    """Clone a working level pak, renaming the map and swapping in fresh settings."""
    if len(donor_name) != len(new_name):
        raise ValueError('name length must match (%d vs %d) or the load_list '
                         'u32 length fields break' % (len(donor_name), len(new_name)))
    dn, nn = donor_name.encode(), new_name.encode()
    tag = '~#@%$`*{0}~#@%$`*'.format(new_name)

    src = zipfile.ZipFile(donor_pak)
    written, renamed, patched = [], [], []
    tmp = out_pak + '.tmp'
    with zipfile.ZipFile(tmp, 'w') as dst:
        for info in src.infolist():
            name = info.filename.replace(donor_name, new_name)
            if name != info.filename:
                renamed.append((info.filename, name))

            base = os.path.basename(info.filename)
            local = os.path.join(level_dir, base)
            if base in json_files and os.path.exists(local):
                data = open(local, 'rb').read()          # fresh settings from disk
                written.append(base)
            elif base == 'user_name_of_map.txt':
                data = tag.encode('utf8')
                written.append(base)
            else:
                data = src.read(info.filename)
                if dn in data:                           # load_list path strings
                    data = data.replace(dn, nn)
                    patched.append('%s (%d)' % (base, src.read(info.filename).count(dn)))

            ni = zipfile.ZipInfo(name, date_time=info.date_time)
            ni.compress_type = info.compress_type        # STORED, like the donor
            ni.external_attr = info.external_attr
            ni.internal_attr = info.internal_attr
            dst.writestr(ni, data)
    src.close()
    os.replace(tmp, out_pak)
    return written, renamed, patched


PAK_ALIGNMENT = 4096


def align_pak(path, alignment=PAK_ALIGNMENT):
    """Zero-pad a level pak up to a block boundary.

    EVERY level_*.pak the editor produces is an exact multiple of 4096 bytes
    (verified 16/16 across Diamondback and objectives, all platforms). A pak that
    ends flush at the EOCD is NOT listed by the game. Specialist and item mods are
    a different loader path and are NOT aligned, so only pad levels.
    """
    size = os.path.getsize(path)
    pad = (-size) % alignment
    if pad:
        with open(path, 'ab') as f:
            f.write(b'\0' * pad)
    return size, pad, os.path.getsize(path)


def set_create_system(path, value=3):
    """Match the editor's 'version made by' host byte (3 = Unix).

    Python's zipfile writes 0 (MS-DOS). Rewrites the byte in each central
    directory header; does not move any data, so offsets stay valid.
    """
    import struct
    data = bytearray(open(path, 'rb').read())
    changed = 0
    i = 0
    sig = b'PK\x01\x02'
    while True:
        i = data.find(sig, i)
        if i < 0:
            break
        # central header: sig(4) version_made_by(2) -> high byte is the host system
        if data[i + 5] != value:
            data[i + 5] = value
            changed += 1
        i += 4
    if changed:
        open(path, 'wb').write(bytes(data))
    return changed


def preflight(level_dir, prebuild_root=None, map_name=None):
    """Refuse to pack a map that breaks a rule from MAP_PACKING_SPEC.md.

    Returns a list of problems; empty means the map is structurally sound.
    """
    import struct
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from saber.saber_scene import tokenize

    problems = []

    # rule 3: every declared zone needs a locator of the same name in the scene
    scene_path = os.path.join(level_dir, 'data_for_ssl.bin')
    if not os.path.exists(scene_path):
        problems.append('no data_for_ssl.bin in %s' % level_dir)
        return problems
    scene = open(scene_path, 'rb').read()
    locators = {t[1] for t in tokenize(scene)}

    zs = json.load(open(os.path.join(level_dir, 'zone_settings.json'), encoding='utf8'))
    zones = zs['SslValue']['zoneDesc']
    for zn in zones:
        if zn['name'] not in locators:
            problems.append('zone %r has NO locator in the scene -- it will not exist'
                            % zn['name'])

    # rule 10: >=1 deploy zone and >=1 EXPEDITION contract
    has_deploy = any(p.get('$type') == 'ZonePropertyDeploy'
                     for zn in zones for p in (zn.get('props') or {}).values()
                     if isinstance(p, dict))
    if not has_deploy:
        problems.append('no ZonePropertyDeploy zone -- map will not load')

    ob = json.load(open(os.path.join(level_dir, 'objective_settings.json'), encoding='utf8'))
    if not any(c.get('contractType') == 1 for c in ob['SslValue'].get('contractsSettings', [])):
        problems.append('no contractType:1 (EXPEDITION) contract -- map will not load')

    # rules 8 and 9: storage zones
    for zn in zones:
        for p in (zn.get('props') or {}).values():
            if not isinstance(p, dict) or p.get('$type') != 'ZonePropertyInventoryStorage':
                continue
            if not p.get('capacity'):
                problems.append('zone %r: capacity is 0 -- inventory will not display'
                                % zn['name'])
            if not p.get('alwaysSpawn'):
                problems.append('zone %r: alwaysSpawn is false -- may never spawn'
                                % zn['name'])
            for s in p.get('slotsSettings') or []:
                for it in s.get('possibleItems') or []:
                    nm = it.get('itemName') or ''
                    if nm and nm not in ('Anchor', 'Anchor_not_removable'):
                        problems.append(
                            'zone %r: itemName %r is not one of the only two identifiers '
                            'the game defines; blank it and rely on type %s'
                            % (zn['name'], nm, it.get('type')))

    # rule 3b: _zones.xml must describe EVERY locator in the scene.
    # Fewer <Zone> entries than locators crashes the editor on open.
    if prebuild_root and map_name:
        import re
        zx = os.path.join(prebuild_root, map_name, '_zones.xml')
        if os.path.exists(zx):
            declared = set(re.findall(r'Id="([^"]+)"', open(zx, encoding='utf8').read()))
            # a locator is a scene string that some zone/link file refers to
            lz = os.path.join(prebuild_root, map_name, 'link_zones.ps')
            linked = set()
            if os.path.exists(lz):
                linked = set(re.findall(r'Id\s*=\s*"([^"]+)"',
                                        open(lz, encoding='utf8').read()))
            scene_zone_like = {n for n in locators if n in declared | linked}
            orphan = sorted(scene_zone_like - declared)
            if orphan:
                problems.append('scene locators with NO <Zone> entry in _zones.xml -- '
                                'this CRASHES the editor on open: %s' % orphan)
            if linked and linked != declared:
                problems.append('link_zones.ps and _zones.xml disagree: only in link=%s '
                                'only in xml=%s'
                                % (sorted(linked - declared), sorted(declared - linked)))

    # rule 1: the descriptor that makes the editor list the map
    if prebuild_root and map_name:
        desc = os.path.join(prebuild_root, map_name + '.xml')
        if not os.path.exists(desc):
            problems.append('missing %s -- the editor will not list this map' % desc)
        if not os.path.isdir(os.path.join(prebuild_root, map_name)):
            problems.append('missing prebuild\\%s source folder' % map_name)
    return problems


def verify(pak, new_name, donor_name):
    z = zipfile.ZipFile(pak)
    bad = z.testzip()
    leftover = [n for n in z.namelist() if donor_name in n]
    text_leftover = []
    for i in z.infolist():
        if donor_name.encode() in z.read(i.filename):
            text_leftover.append(i.filename)
    zs = json.loads(z.read('zone_settings.json').decode('utf-8-sig'))
    ob = json.loads(z.read('objective_settings.json').decode('utf-8-sig'))
    return {
        'testzip': bad,
        'entries': len(z.namelist()),
        'entry_names_still_donor': leftover,
        'members_still_containing_donor_name': text_leftover,
        'map_tag': zs['SslValue']['name'],
        'zones': [z2['name'] for z2 in zs['SslValue']['zoneDesc']],
        'contracts': [(c['name'], c['contractType'])
                      for c in ob['SslValue']['contractsSettings']],
        'user_name': z.read('user_name_of_map.txt').decode('utf8'),
        'size_mb': round(os.path.getsize(pak) / 1048576, 2),
    }
