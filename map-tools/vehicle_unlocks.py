"""vehicle_unlocks.py -- lock a mod truck and its parts behind a map's objectives.

Proven end to end on level_unlock_test, 2026-11-13 (Toolkit/notes/unlock_test.md): a parked
mod truck was hauled home to unlock it, then a Task, a Contract and an Expedition (stage and
finish) unlocked every one of its parts. This tool does the truck-mod side of that, reading
the same objective plan build_objectives.py writes, so the two can never disagree.

THE RULES IT ENCODES (each one cost a test run to find):

  truck      GameData UnlockByObjective = the objective's SAVED name, mod_<region>_<name>
             (build_objectives.saved_name). Reward: ObjectiveRewardUnlock "<truck>_<modid>".
  add-on     GameData AddonUnlockByObjective="true". Reward: ObjectiveRewardItem
             "<addon>_<modid>" (the plain name worked too, on the two bumpers).
  engine,    the variant's GameData AddonUnlockByObjective="true". Reward id: the variant's
  gearbox,   Name as written, with NO mod id: the save stores mod engines and gearboxes
  suspension plainly (jtt_g_jeep_renegade_offroad).
  parked     a truck hauled by a truckDelivery stage must carry Id "mod_<level>_<truckId>"
  truck      in data/<tile>/Truck.xml: the game prefixes the task's truckId with the map id
             (PrependWorldIdForTruckDelivery) and not the parked truck's. A plain Id never
             completes. Its Type is the truck's in-game id, "<truck>_<modid>"; the editor
             cannot list a mod truck, so place a blank truck there and let this set the rest.
  ownership  a part the player already owns shows unlocked, whatever the lock says.

Saber's own packer refuses a truck mod that has locks (docs: Linking Mods), and a player who
has the truck mod but not the map can never unlock it. Ship the map and the truck together.

    python vehicle_unlocks.py <plan.json> --mod 1234567            report only
    python vehicle_unlocks.py <plan.json> --mod 1234567 --apply    lock the installed pak, fix Ids
    python vehicle_unlocks.py <plan.json> --mod 1234567 --restore  put the pre-lock pak back

--apply edits the INSTALLED pak in place (it is idempotent) after one backup of the pak as it
was before this tool first touched it, in %LOCALAPPDATA%\\ExpeditionsToolkit\\vehicle_locks\\<modid>\\.
Close the game.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_objectives as B  # noqa: E402

USER = os.path.join(os.path.expanduser('~'), 'Documents', 'My Games', 'Expeditions')
MODIO = os.path.join(USER, 'base', 'Mods', '.modio', 'mods')
PREBUILD = os.path.join(USER, 'Media', 'prebuild')
STAGING = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'ExpeditionsToolkit', 'vehicle_locks')

VARIANT_DIRS = {'engines': 'engine', 'gearboxes': 'gearbox', 'suspensions': 'suspension',
                'winches': 'winch'}


# ---------------------------------------------------------------- reading a truck mod

def strip_templates(text):
    return re.sub(r'<_templates[\s\S]*?</_templates>|<_templates[^>]*/>', '', text)


def parts_of(files, mod_id):
    """{reward id: part} for every truck, add-on and variant a truck mod ships.

    files: {pak path: xml text}. A part: {kind, path, name, variant (or None)}."""
    out = {}
    for path, text in files.items():
        if not (path.startswith('classes/') and path.endswith('.xml')):
            continue
        folder, base = path.split('/')[1], os.path.basename(path)[:-4]
        body = strip_templates(text)
        if folder == 'trucks' and re.search(r'<Truck\b', body):
            out[f'{base}_{mod_id}'] = {'kind': 'truck', 'path': path, 'name': base, 'variant': None}
        elif folder == 'trucks' and re.search(r'<TruckAddon\b', body):
            out[f'{base}_{mod_id}'] = {'kind': 'addon', 'path': path, 'name': base, 'variant': None}
        elif folder in VARIANT_DIRS:
            for name in re.findall(r'\bName="([^"]+)"[^>]*>\s*(?=[\s\S]*?<GameData)', body):
                out[name] = {'kind': VARIANT_DIRS[folder], 'path': path, 'name': name,
                             'variant': name}
    return out


def resolve(reward_id, parts, mod_id):
    """The part a reward id names, and the id it should be written as (or None, None)."""
    if reward_id in parts:
        return parts[reward_id], reward_id
    full = f'{reward_id}_{mod_id}'
    if full in parts and parts[full]['kind'] in ('truck', 'addon'):
        return parts[full], full                       # plain add-on/truck name
    if reward_id.endswith(f'_{mod_id}'):
        bare = reward_id[:-len(mod_id) - 1]
        if bare in parts and parts[bare]['kind'] not in ('truck', 'addon'):
            return parts[bare], bare                   # engine written with a mod id
    return None, None


# ---------------------------------------------------------------- reading the plan

def rewards_in(plan):
    """[(objective name, 'unlock'|'item', reward id)] from objectives and their stages."""
    out = []
    for o in plan['objectives']:
        for spec in [o] + o.get('stages', []):
            for key in ('unlock', 'item'):
                for rid in spec.get(key, []) or []:
                    out.append((o['name'], key, rid))
    return out


# ---------------------------------------------------------------- the edits

def gamedata_span(text, variant=None):
    """(start, end) of the opening <GameData ...> tag: the file's first, or the variant's."""
    at = 0
    if variant:
        m = re.search(r'\bName="%s"' % re.escape(variant), text)
        if not m:
            raise ValueError(f'no variant {variant}')
        at = m.end()
    m = re.compile(r'<GameData\b[^>]*>').search(text, at)
    if not m:
        raise ValueError('no GameData')
    return m.start(), m.end()


def set_attr(tag, attr, value):
    if re.search(r'\b%s="[^"]*"' % attr, tag):
        return re.sub(r'\b%s="[^"]*"' % attr, f'{attr}="{value}"', tag)
    return tag.replace('<GameData', f'<GameData {attr}="{value}"', 1)


def lock_part(text, part, objective_saved_name=None):
    """The class text with this part locked. Trucks name their objective; parts flag it."""
    s, e = gamedata_span(text, part['variant'])
    tag = text[s:e]
    if part['kind'] == 'truck':
        tag = set_attr(tag, 'UnlockByObjective', objective_saved_name)
    else:
        tag = set_attr(tag, 'AddonUnlockByObjective', 'true')
    tag = set_attr(tag, 'UnlockByExploration', 'false')
    return text[:s] + tag + text[e:]


def is_locked(text, part):
    s, e = gamedata_span(text, part['variant'])
    tag = text[s:e]
    if part['kind'] == 'truck':
        return bool(re.search(r'\bUnlockByObjective="[^"]+"', tag))
    return 'AddonUnlockByObjective="true"' in tag


def check(plan, files, mod_id):
    """(per-reward rows, fails, warns, {path: new text})"""
    parts = parts_of(files, mod_id)
    region = plan['region']
    rows, fails, warns, edits = [], [], [], {}
    rewarded = set()
    for obj, key, rid in rewards_in(plan):
        part, want = resolve(rid, parts, mod_id)
        if part is None:
            if rid.endswith(f'_{mod_id}') or rid in {p['name'] for p in parts.values()}:
                fails.append(f'{obj}: {rid} is not a part of mod {mod_id}')
            continue                                   # another mod's or vanilla's: not ours
        if part['kind'] == 'truck' and key != 'unlock':
            fails.append(f'{obj}: the truck {rid} is a reward of "unlock", not "item"')
        if part['kind'] != 'truck' and key != 'item':
            fails.append(f'{obj}: the part {rid} is a reward of "item", not "unlock"')
        if want != rid and part['kind'] in ('engine', 'gearbox', 'suspension', 'winch'):
            fails.append(f'{obj}: write {part["kind"]} {rid} as "{want}" (no mod id)')
        elif want != rid:
            warns.append(f'{obj}: {rid} works, but the proven form is "{want}"')
        rewarded.add(id(part))
        path = part['path']
        text = edits.get(path, files[path])
        saved = B.saved_name(region, obj) if part['kind'] == 'truck' else None
        if part['kind'] == 'truck':
            cur = re.search(r'\bUnlockByObjective="([^"]*)"', text[slice(*gamedata_span(text))])
            if cur and cur.group(1) not in ('', saved):
                fails.append(f'{obj}: truck already names objective "{cur.group(1)}", not "{saved}"')
        new = lock_part(text, part, saved)
        state = 'locked' if is_locked(text, part) else 'will lock'
        if new != text:
            edits[path] = new
        rows.append((obj, part['kind'], want, state))
    for rid, part in parts.items():
        if id(part) in rewarded or part['kind'] == 'truck':
            continue
        text = files[part['path']]
        if is_locked(text, part):
            fails.append(f'{part["kind"]} {rid} is locked but no objective rewards it: '
                         'it would be locked forever')
    return rows, fails, warns, edits


# ---------------------------------------------------------------- parked trucks

def truck_files(level):
    d = os.path.join(PREBUILD, level, 'data')
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, t, 'Truck.xml') for t in os.listdir(d)
            if os.path.isfile(os.path.join(d, t, 'Truck.xml'))]


def parked_truck_fixes(plan, mod_trucks):
    """[(path, old text, new text, note)] so every truck stage's parked truck carries the
    map-prefixed Id; and notes for trucks that cannot be found."""
    out, notes = [], []
    for o in plan['objectives']:
        for s in o.get('stages', []):
            for v in s.get('truck', []):
                level = B.split_zone(v['zone'], o['map'])[0]
                want = f'mod_{level}_{v["truck"]}'
                found = False
                for p in truck_files(level):
                    t = open(p, encoding='utf-8', newline='').read()
                    for blk in re.findall(r'<Truck\b[\s\S]*?</Truck>', t):
                        ident = re.search(r'\bId="([^"]*)"', blk)
                        ident = ident.group(1) if ident else ''
                        if ident == want:
                            found = True
                        elif ident == v['truck']:
                            new = t.replace(blk, blk.replace(f'Id="{ident}"', f'Id="{want}"', 1))
                            out.append((p, t, new, f'{o["name"]}: Id {ident} -> {want}'))
                            found = True
                        if ident in (want, v['truck']):
                            typ = re.search(r'\bType="([^"]*)"', blk)
                            if not typ:
                                notes.append(f'{o["name"]}: the parked truck has no Type yet '
                                             f'(the mod\'s trucks: {sorted(mod_trucks)})')
                if not found:
                    notes.append(f'{o["name"]}: no parked truck with Id "{want}" on {level}. Place a '
                                 f'blank truck in the editor, save, close it, run --apply; it sets the '
                                 f'Id, and Type if you pass --truck-type')
    return out, notes


def set_blank_truck(level, truck_id, truck_type):
    """Give the one blank (no Type, no Id) truck on the map this Type and Id."""
    hits = []
    for p in truck_files(level):
        t = open(p, encoding='utf-8', newline='').read()
        for blk in re.findall(r'<Truck\b[\s\S]*?</Truck>', t):
            if 'Type=' not in blk and not re.search(r'\bId=', blk.split('>')[0].replace('CustomizationPresetId', '')):
                hits.append((p, t, blk))
    if len(hits) != 1:
        return None, f'expected one blank truck on {level}, found {len(hits)}'
    p, t, blk = hits[0]
    new = blk.replace('\tDoLand="true"\n', f'\tDoLand="true"\n\tType="{truck_type}"\n\tId="{truck_id}"\n', 1)
    if new == blk:
        return None, 'blank truck has no DoLand line to anchor on'
    return (p, t, t.replace(blk, new)), None


# ---------------------------------------------------------------- the pak

def mod_pak(mod_id):
    d = os.path.join(MODIO, str(mod_id))
    paks = [f for f in os.listdir(d) if f.endswith('.pak') and f != 'pc.pak']
    if len(paks) != 1:
        raise SystemExit(f'{d}: expected one main pak, found {paks}')
    return os.path.join(d, paks[0])


def read_pak(path):
    with zipfile.ZipFile(path) as z:
        return {i.filename: z.read(i) for i in z.infolist()}


def write_pak(path, raw, edits):
    tmp = path + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in raw.items():
            z.writestr(name, edits[name].encode('utf-8') if name in edits else data)
    with zipfile.ZipFile(tmp) as z:
        if z.testzip() is not None:
            os.remove(tmp)
            raise SystemExit('written pak failed its own check; nothing replaced')
    os.replace(tmp, path)
    meta = os.path.join(os.path.dirname(path), 'modio.json')
    if os.path.isfile(meta):                       # it records the pak's byte size
        doc = json.load(open(meta, encoding='utf-8'))
        doc.setdefault('modfile', {})['filesize'] = os.path.getsize(path)
        json.dump(doc, open(meta, 'w', encoding='utf-8'), indent=1)


def game_running():
    import subprocess
    out = subprocess.run(['tasklist'], capture_output=True, text=True).stdout.lower()
    return 'expeditions.exe' in out or 'expeditionseditor.exe' in out


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('plan')
    ap.add_argument('--mod', required=True)
    ap.add_argument('--truck-type', help='Type for a blank parked truck, e.g. mytruck_1234567')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--restore', action='store_true')
    a = ap.parse_args(argv)
    pak = mod_pak(a.mod)
    backup = os.path.join(STAGING, str(a.mod), os.path.basename(pak))
    if (a.apply or a.restore) and game_running():
        return print('REFUSING: close the game and the editor first') or 2
    if a.restore:
        if not os.path.isfile(backup):
            return print('no backup at', backup) or 1
        shutil.copyfile(backup, pak)
        write_pak(pak, read_pak(pak), {})          # refresh modio.json's size
        return print('restored', pak, 'from', backup) or 0

    plan = json.load(open(a.plan, encoding='utf-8'))
    raw = read_pak(pak)
    files = {k: v.decode('utf-8') for k, v in raw.items() if k.startswith('classes/') and k.endswith('.xml')}
    rows, fails, warns, edits = check(plan, files, a.mod)
    trucks = {k for k, p in parts_of(files, a.mod).items() if p['kind'] == 'truck'}
    fixes, notes = parked_truck_fixes(plan, trucks)
    blank = None
    if a.truck_type and notes:
        for o in plan['objectives']:
            for s in o.get('stages', []):
                for v in s.get('truck', []):
                    level = B.split_zone(v['zone'], o['map'])[0]
                    blank, err = set_blank_truck(level, f'mod_{level}_{v["truck"]}', a.truck_type)
                    if err:
                        fails.append(err)
                    else:
                        notes = []

    for obj, kind, rid, state in rows:
        print(f'  {state:10} {kind:10} {rid:50} <- {obj}')
    for f in fixes:
        print('  parked truck:', f[3])
    if blank:
        print('  parked truck: blank truck in', blank[0], 'gets Type', a.truck_type)
    for w in warns + notes:
        print('WARNING:', w)
    for f in fails:
        print('FAIL:', f)
    if fails:
        return print('nothing written') or 1
    if not a.apply:
        return print(f'{len(edits)} class file(s) to change (report only; --apply to write)') or 0
    if edits:
        if not os.path.isfile(backup):
            os.makedirs(os.path.dirname(backup), exist_ok=True)
            shutil.copyfile(pak, backup)
            print('pre-lock pak backed up to', backup)
        write_pak(pak, raw, edits)
        print(f'locked {len(edits)} class file(s) in', pak)
    for p, old, new, note in fixes + ([blank + ('blank',)] if blank else []):
        if open(p, encoding='utf-8', newline='').read() != old:
            return print('changed under us, skipped:', p) or 1
        open(p, 'w', encoding='utf-8', newline='').write(new)
        print('wrote', p)
    if fixes or blank:
        print('repack the map in the editor, then run after_pack')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
