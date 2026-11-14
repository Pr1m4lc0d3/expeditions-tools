"""build_region.py -- build or refresh a custom region of linked maps.

WHY IT EXISTS. Saber's hidden region editor writes files the game cannot load:
each one below was a startup hang, an empty list or a crash, found one at a time
on 2026-11-03/04 (RE\\PATCH_LOG.md). A region must have:

  data key `mod_region`        any other name hangs before the main menu
  modId on map 2 onward        blank modId empties the Custom list
  fuel, bird, no-fly values    region maps get 0.0, not defaults: divide by zero
  caveSettings, uiImages       null freezes the game entering a map
  every zone key written out   the region loader restores no defaults
  `mod_level_x || zone` links  the tagged form drops to the main menu

This writes all of that from the maps' own paks, so the region always matches
what was packed. Never re-save a region through Saber's tool afterward: it strips
these again. Writes to Media\\prebuild\\<id>, which is where the game looks.

Gateways cross without a region (tested 11-04). The region is what groups the
maps as one entry; regionDesc.json is its source of truth.

    python build_region.py new <id> --name "Shown name" [--desc TEXT] [--country US]
                           level_a level_b ... [--out DIR] [--apply]
    python build_region.py refresh <id> [--out DIR] [--apply]
    python build_region.py refresh --all [--apply]         every installed region

Report only without --apply. A refresh keeps each map's hand-set values (cave
settings, popup images, fuel, birds, no-fly) and only reloads zones and contracts.
"""
import argparse
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fix_gateway_links as links  # noqa: E402

MEDIA = os.path.join(os.path.expanduser('~'), 'Documents', 'My Games', 'Expeditions', 'Media')
MODS = os.path.join(MEDIA, 'Mods')
PREBUILD = os.path.join(MEDIA, 'prebuild')
KEY = 'mod_region'
TAG = links.TAG
# Standalone maps show these defaults. In a region, absent fields load as 0.
MAP_DEFAULTS = {
    'birdsMultiplier': 0.5,
    'caveSettings': {'$type': 'CaveSettings', 'isCave': False, 'surfaceMapID': '',
                     'truckUncloakRadius': 0, 'truckUncloakTranspRadius': 0},
    'noFlyZone': -1.0,
    'uiFuelConsumptionModifier': 1.0,
    'uiImages': [],
}


def read_json(zf, member):
    return json.loads(zf.read(member).decode('utf-8-sig'))


def real_link(link):
    level, zone = links.split_link(link)
    return 'mod_%s%s%s' % (level, links.CUTTER, zone)


def region_zones(zone_desc):
    """Zones as the region loader wants them: real links, plain vectors."""
    zones = json.loads(json.dumps(zone_desc))
    for zone in zones:
        gate = (zone.get('props') or {}).get('ZonePropertyGateway')
        if gate:
            gate['levelZoneLink'] = real_link(gate.get('levelZoneLink') or '')
            for field in ('cameraDir', 'cameraPos'):
                (gate.get(field) or {}).pop('$type', None)
    return zones


def read_map(mods, level):
    stem = links.installed_name(mods, level)
    if stem is None:
        raise ValueError('%s.pak is not installed in %s' % (level, mods))
    with zipfile.ZipFile(os.path.join(mods, stem + '.pak')) as z:
        return {'level': stem,
                'zones': read_json(z, 'zone_settings.json')['SslValue'],
                'objectives': read_json(z, 'objective_settings.json'),
                'inventory': read_json(z, 'inventory_rand_settings.json'),
                'content': read_json(z, 'content_settings.json')}


def map_desc(region_id, index, m, kept):
    """One map's entry. CreateConfig fills map 1's modId and inventory itself."""
    desc = {k: json.loads(json.dumps(kept.get(k, v))) for k, v in MAP_DEFAULTS.items()}
    desc.update({
        'indexInRegion': index,
        'isNeedToBeCloacked': bool(m['zones'].get('isNeedToBeCloacked')) or index > 0,
        'name': '%s%s%s' % (TAG, m['level'], TAG),
        'zoneDesc': region_zones(m['zones']['zoneDesc']),
    })
    if index:
        inv = m['inventory']['SslValue']
        desc['modId'] = 'mod_' + region_id
        desc['inventorySettings'] = {'$type': 'InventoryRandomizerSettings',
                                     'additionalLoot': inv.get('additionalLoot', []),
                                     'maxInventoryZones': inv.get('maxInventoryZones', 0)}
    return desc


def local_zones(maps):
    """The gateways alone, per map, as Saber's tool keeps them."""
    out = []
    for m in maps:
        gates = [{'name': z['name'],
                  'props': {'ZonePropertyGateway': z['props']['ZonePropertyGateway']}}
                 for z in region_zones(m['zones']['zoneDesc'])
                 if 'ZonePropertyGateway' in (z.get('props') or {})]
        if gates:
            out.append({'name': '%s%s%s' % (TAG, m['level'], TAG), 'zoneDesc': gates})
    return {'ExternalData': [], 'SslValue': out}


def objectives(maps):
    """Contracts AND tasks. In region play the game takes objectives from this file
    only: with tasks left out, no task could be started on Gate Two (2026-11-08).
    The save lists a task under both mod_<region>_X and mod_level_<map>_X; that is
    not proof of two live copies."""
    contracts, tasks = [], []
    for m in maps:
        ssl = m['objectives']['SslValue']
        for c in ssl.get('contractsSettings', []):
            c['region'] = KEY
            contracts.append(c)
        tasks += ssl.get('tasksSettings', [])
    return {'ExternalData': [], 'SslType': 'ObjectiveSettingsContainer',
            'SslValue': {'contestSettings': [], 'contractsSettings': contracts,
                         'tasksSettings': tasks}}


def build(region_desc, mods, kept=None):
    """{file name: content} for a region. kept: {level: previous map entry}."""
    region_id = region_desc['Name']
    levels = [e['LevelName'] for e in sorted(region_desc['LevelList'],
                                             key=lambda e: e['LevelOrder'])]
    maps = [read_map(mods, level) for level in levels]
    kept = kept or {}
    descs = [map_desc(region_id, i, m, kept.get(m['level'], {})) for i, m in enumerate(maps)]
    return {
        'regionDesc.json': region_desc,
        'zone_settings.json': {
            'SslType': 'ZoneDescStorageContainerBase',
            'SslValue': {
                'data': {KEY: {'mapDescs': descs}},
                'regions': [{'country': region_desc['Country'], 'name': KEY, 'uiInfo': {
                    'globalMapRegionBackground': region_desc['SslValue']['globalMapRegionBackground'],
                    'globalMapRegionIcon': region_desc['SslValue']['globalMapRegionIcon'],
                    'uiDesc': region_desc['UiDesc'],
                    'uiName': region_desc['UiName']}}]}},
        'local_zone_settings.json': local_zones(maps),
        'objective_settings.json': objectives(maps),
        'local_objective_settings.json': {
            'ExternalData': [], 'SslType': 'ObjectiveSettings',
            'SslValue': {'contractsSettings': [], 'name': 'objectiveSettings'}},
        'inventory_rand_settings.json': maps[0]['inventory'],
        'content_settings.json': maps[0]['content'],
        'user_name_of_map.txt': '%sRegion%s' % (TAG, TAG),
    }


def problems(files, mods):
    """What would stop this region working. Returns (failures, warnings)."""
    fails, warns = [], []
    descs = files['zone_settings.json']['SslValue']['data'][KEY]['mapDescs']
    here = {d['name'].replace(TAG, '').lower() for d in descs}
    for d in descs:
        level = d['name'].replace(TAG, '')
        pak = os.path.join(mods, level + '.pak')
        with zipfile.ZipFile(pak) as z:
            blob = z.read(links.MEMBER)
        if links.rewrite_links(blob, mods)[1]:
            fails.append('%s still has packed links: run after_pack first' % level)
        f, w = links.check_map(pak, blob)
        fails += ['%s: %s' % (level, x) for x in f]
        warns += ['%s: %s' % (level, x) for x in w]
        for zone in d['zoneDesc']:
            gate = (zone.get('props') or {}).get('ZonePropertyGateway')
            if gate:
                target = links.split_link(gate['levelZoneLink'])[0]
                if target.lower() not in here:
                    warns.append('%s: gate %s leads outside this region, to %s'
                                 % (level, zone['name'], target))
    if len(descs) < 2:
        warns.append('this region has only one map')
    return fails, warns


def new_desc(args):
    return {'Country': args.country,
            'LevelList': [{'LevelName': lv, 'LevelOrder': i + 1}
                          for i, lv in enumerate(args.levels)],
            'Name': args.id, 'SslType': 'ModRegionUIDesc',
            'SslValue': {'globalMapRegionBackground': '', 'globalMapRegionIcon': ''},
            'UiDesc': args.desc, 'UiName': args.name,
            'isEnableDevMenu': False, 'isIgnoreCountryPurchaseBlock': False}


def load_existing(folder):
    with open(os.path.join(folder, 'regionDesc.json'), encoding='utf-8-sig') as f:
        desc = json.load(f)
    kept = {}
    path = os.path.join(folder, 'zone_settings.json')
    if os.path.exists(path):
        with open(path, encoding='utf-8-sig') as f:
            old = json.load(f)
        for d in old['SslValue']['data'].get(KEY, {}).get('mapDescs', []):
            kept[d['name'].replace(TAG, '')] = d
    return desc, kept


def write(folder, files):
    os.makedirs(folder, exist_ok=True)
    for name, content in files.items():
        text = content if isinstance(content, str) else \
            json.dumps(content, indent=4, sort_keys=True, ensure_ascii=False) + '\n'
        with open(os.path.join(folder, name), 'w', encoding='utf-8', newline='') as f:
            f.write(text)


def finish(desc, kept, folder, mods, apply):
    print('region %s: %d map(s)' % (desc['Name'], len(desc['LevelList'])))
    try:
        files = build(desc, mods, kept)
    except (ValueError, KeyError, OSError) as e:
        print('FAILED:', e)
        return 1
    fails, warns = problems(files, mods)
    for w in warns:
        print('WARNING:', w)
    for f in fails:
        print('FAILED:', f)
    if fails:
        print('nothing written')
        return 1
    if not apply:
        print('would write %d file(s) to %s' % (len(files), folder))
        return 0
    if links.game_running():
        print('REFUSING: the game is running. Close it and run this again')
        return 1
    write(folder, files)
    print('wrote %d file(s) to %s' % (len(files), folder))
    return 0


def installed_regions():
    if not os.path.isdir(PREBUILD):
        return []
    return [d for d in sorted(os.listdir(PREBUILD))
            if os.path.exists(os.path.join(PREBUILD, d, 'regionDesc.json'))]


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    n = sub.add_parser('new')
    n.add_argument('id')
    n.add_argument('levels', nargs='+')
    n.add_argument('--name', required=True)
    n.add_argument('--desc', default='')
    n.add_argument('--country', default='US')
    r = sub.add_parser('refresh')
    r.add_argument('id', nargs='?')
    r.add_argument('--all', action='store_true')
    for s in (n, r):
        s.add_argument('--out')
        s.add_argument('--mods', default=MODS)
        s.add_argument('--apply', action='store_true')
    args = p.parse_args(argv[1:])

    if args.cmd == 'new':
        folder = args.out or os.path.join(PREBUILD, args.id)
        if os.path.exists(os.path.join(folder, 'regionDesc.json')):
            print('REFUSING: %s already exists. Use refresh' % folder)
            return 1
        return finish(new_desc(args), {}, folder, args.mods, args.apply)

    ids = installed_regions() if args.all else [args.id]
    if not ids or ids == [None]:
        print('no regions to refresh')
        return 0
    code = 0
    for region in ids:
        folder = args.out or os.path.join(PREBUILD, region)
        desc, kept = load_existing(folder)
        code |= finish(desc, kept, folder, args.mods, args.apply)
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv))
