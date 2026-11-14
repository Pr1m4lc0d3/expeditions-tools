"""Gate for vehicle_unlocks.py.

The reference is the setup that unlocked a mod truck and all its parts in game on
2026-11-13 (Toolkit/notes/unlock_test.md), frozen in the fixtures folder (unlock_test_proven): the locked
truck mod pak and its objective plan. The test map itself was deleted after. Run against those, the tool must find every lock already
in place and change nothing. The rest are small made-up mods.

    python test_vehicle_unlocks.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vehicle_unlocks as V  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = '9100099'
TRUCK = '<_templates Include="trucks"/>\n<Truck>\n\t<GameData\n\t\tPrice="100"\n\t\tUnlockByExploration="false"\n\t\tUnlockByRank="1"\n\t>\n\t</GameData>\n</Truck>\n'
ADDON = '<TruckAddon>\n\t<GameData Price="5" UnlockByExploration="false" UnlockByRank="1" >\n\t</GameData>\n</TruckAddon>\n'
ENGINE = ('<_templates Include="trucks"/>\n<EngineVariants>\n\t<Engine Name="toy_engine_0" Torque="1">\n'
          '\t\t<GameData Price="1" UnlockByExploration="false" AddonUnlockByObjective="false" UnlockByRank="1">\n'
          '\t\t</GameData>\n\t</Engine>\n\t<Engine Name="toy_engine_1" Torque="2">\n'
          '\t\t<GameData Price="2" UnlockByExploration="true" AddonUnlockByObjective="false" UnlockByRank="1">\n'
          '\t\t</GameData>\n\t</Engine>\n</EngineVariants>\n')


def toy():
    return {'classes/trucks/toy.xml': TRUCK,
            'classes/trucks/toy_tuning/toy_rack.xml': ADDON,
            'classes/trucks/toy_tuning/toy_light.xml': ADDON,
            'classes/engines/e_toy.xml': ENGINE}


def plan(*objectives):
    return {'region': 'level_toy', 'objectives': [
        {'name': 'Toy Free Roam', 'map': 'level_toy', 'free_roam': True}] + list(objectives)}


def task(name, **rewards):
    o = {'name': name, 'map': 'level_toy', 'kind': 'task', 'giver': 'g',
         'stages': [{'visit': [{'zone': 'z'}]}]}
    o.update(rewards)
    return o


_HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.environ.get('EXPEDITIONS_FIXTURES') or next(
    (d for d in (os.path.join(_HERE, 'fixtures'), os.path.join(os.path.dirname(_HERE), '_staging'))
     if os.path.isdir(d)), os.path.join(_HERE, 'fixtures'))
# reference data recorded from in-game tests; not shipped. Without it those tests skip.
PROVEN = os.path.join(FIXTURES, 'unlock_test_proven')   # the pak + plan that worked in game


def test_the_proven_setup_needs_no_change():
    p = json.load(open(os.path.join(PROVEN, 'unlock_test.json'), encoding='utf-8'))
    raw = V.read_pak(os.path.join(PROVEN, 'alpine_rescue.pak'))
    files = {k: v.decode('utf-8') for k, v in raw.items() if k.startswith('classes/') and k.endswith('.xml')}
    rows, fails, warns, edits = V.check(p, files, '9100002')
    assert not fails, fails
    assert not edits, sorted(edits)
    assert len(rows) == 11 and all(r[3] == 'locked' for r in rows), rows


def test_parts_are_found_with_their_reward_ids():
    parts = V.parts_of(toy(), MOD)
    assert parts[f'toy_{MOD}']['kind'] == 'truck'
    assert parts[f'toy_rack_{MOD}']['kind'] == 'addon'
    assert parts['toy_engine_1']['kind'] == 'engine'       # engines carry no mod id


def test_a_chain_locks_the_truck_and_its_parts():
    p = plan(task('Find it', unlock=[f'toy_{MOD}']),
             task('Parts', item=[f'toy_rack_{MOD}', 'toy_engine_1']))
    rows, fails, warns, edits = V.check(p, toy(), MOD)
    assert not fails, fails
    assert 'UnlockByObjective="mod_level_toy_Find it"' in edits['classes/trucks/toy.xml']
    assert 'AddonUnlockByObjective="true"' in edits['classes/trucks/toy_tuning/toy_rack.xml']
    eng = edits['classes/engines/e_toy.xml']
    one = eng[eng.index('toy_engine_1'):]
    assert 'AddonUnlockByObjective="true"' in one and 'UnlockByExploration="false"' in one
    zero = eng[:eng.index('toy_engine_1')]
    assert 'AddonUnlockByObjective="false"' in zero               # the stock engine stays open
    assert 'toy_light.xml' not in ' '.join(edits)                 # nothing rewards it: untouched


def test_an_engine_written_with_a_mod_id_fails():
    p = plan(task('Parts', item=[f'toy_engine_1_{MOD}']))
    fails = V.check(p, toy(), MOD)[1]
    assert any('as "toy_engine_1"' in f for f in fails), fails


def test_a_truck_given_as_an_item_fails():
    p = plan(task('Find it', item=[f'toy_{MOD}']))
    assert any('"unlock", not "item"' in f for f in V.check(p, toy(), MOD)[1])


def test_a_locked_part_nothing_rewards_fails():
    files = toy()
    files['classes/trucks/toy_tuning/toy_light.xml'] = ADDON.replace('<GameData', '<GameData AddonUnlockByObjective="true"')
    fails = V.check(plan(task('Parts', item=[f'toy_rack_{MOD}'])), files, MOD)[1]
    assert any('locked forever' in f and 'toy_light' in f for f in fails), fails


def test_a_plain_addon_name_resolves_with_a_warning():
    rows, fails, warns, edits = V.check(plan(task('Parts', item=['toy_rack'])), toy(), MOD)
    assert not fails and any(f'toy_rack_{MOD}' in w for w in warns), (fails, warns)
    assert 'classes/trucks/toy_tuning/toy_rack.xml' in edits


def test_a_truck_already_tied_to_another_objective_fails():
    files = toy()
    files['classes/trucks/toy.xml'] = TRUCK.replace('UnlockByRank', 'UnlockByObjective="mod_level_toy_Other" UnlockByRank')
    fails = V.check(plan(task('Find it', unlock=[f'toy_{MOD}'])), files, MOD)[1]
    assert any('already names objective' in f for f in fails), fails


def test_parked_truck_gets_the_map_prefix():
    with tempfile.TemporaryDirectory() as d:
        old = V.PREBUILD
        V.PREBUILD = d
        try:
            tile = os.path.join(d, 'level_toy', 'data', '1_1')
            os.makedirs(tile)
            p = os.path.join(tile, 'Truck.xml')
            open(p, 'w', newline='').write('<Truck\n\tDoLand="true"\n\tType="toy_9100099"\n\tId="barn"\n>\n</Truck>\n')
            pl = plan({'name': 'Find it', 'map': 'level_toy', 'kind': 'task', 'giver': 'g',
                       'stages': [{'truck': [{'truck': 'barn', 'zone': 'home'}]}]})
            fixes, notes = V.parked_truck_fixes(pl, {'toy_9100099'})
            assert len(fixes) == 1 and 'Id="mod_level_toy_barn"' in fixes[0][2], (fixes, notes)
        finally:
            V.PREBUILD = old


def test_a_blank_parked_truck_gets_type_and_id():
    with tempfile.TemporaryDirectory() as d:
        old = V.PREBUILD
        V.PREBUILD = d
        try:
            tile = os.path.join(d, 'level_toy', 'data', '2_1')
            os.makedirs(tile)
            open(os.path.join(tile, 'Truck.xml'), 'w', newline='').write(
                '<Truck\n\tDoLand="true"\n\tCustomizationPresetId="-1"\n>\n</Truck>\n')
            (p, t, new), err = V.set_blank_truck('level_toy', 'mod_level_toy_barn', 'toy_9100099')
            assert not err and 'Type="toy_9100099"' in new and 'Id="mod_level_toy_barn"' in new, new
        finally:
            V.PREBUILD = old


def main():
    bad = skipped = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith('test_')]
    for name, fn in tests:
        try:
            fn()
            print('%-52s ok' % name)
        except FileNotFoundError as e:   # an input this machine does not have
            skipped += 1
            print('%-52s skipped (missing %s)' % (name, e.filename))
        except Exception as e:      # noqa: BLE001
            bad += 1
            print('%-52s FAILED %s' % (name, str(e)[:500]))
    print('%d of %d passed, %d skipped' % (len(tests) - bad - skipped, len(tests), skipped))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
