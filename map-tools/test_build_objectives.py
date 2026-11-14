"""Gate for build_objectives.py.

The reference is the setup that locked, unlocked and deployed in game on
2026-11-05, kept in _staging\\gate_unlock_proven. The plan objective_plans\\gate_test.json
has to reproduce it, field for field.

    python test_build_objectives.py
"""
import copy
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_objectives as B  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.environ.get('EXPEDITIONS_FIXTURES') or next(
    (d for d in (os.path.join(_HERE, 'fixtures'), os.path.join(os.path.dirname(_HERE), '_staging'))
     if os.path.isdir(d)), os.path.join(_HERE, 'fixtures'))
# reference data recorded from in-game tests; not shipped. Without it those tests skip.
PROVEN = os.path.join(FIXTURES, 'gate_unlock_proven')
CAMP = os.path.join(FIXTURES, 'build_camp_reference')
PLAN = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'objective_plans', 'gate_test.json')


def plan():
    with open(PLAN, encoding='utf-8') as f:
        return json.load(f)


def diff(a, b, path=''):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append('%s/%s only on one side' % (path, k))
            else:
                out += diff(a[k], b[k], '%s/%s' % (path, k))
        return out
    if isinstance(a, list) and isinstance(b, list):
        out = [] if len(a) == len(b) else ['%s length %d vs %d' % (path, len(a), len(b))]
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, '%s/%d' % (path, i))
        return out
    return [] if a == b else ['%s: %r vs %r' % (path, a, b)]


def test_reproduces_the_setup_that_worked():
    files, fails, warns = B.build(plan(), PROVEN)
    assert not fails, fails
    assert not warns, warns
    left = []
    for path, doc in files.items():
        left += ['%s %s' % (os.path.basename(path), d) for d in diff(B.read_json(path), doc)]
    assert not left, left[:12]


def test_a_lock_gets_the_full_saved_name():
    c = B.contract(plan()['objectives'][2], 'gateway_test')
    assert c['blockerObjectives'] == ['mod_gateway_test_Find the Gate']


def test_negative_control_sees_a_plain_lock():
    files, _, _ = B.build(plan(), PROVEN)
    path = [p for p in files if p.endswith('level_gate_two\\' + B.OBJECTIVES)][0]
    doc = copy.deepcopy(files[path])
    doc['SslValue']['contractsSettings'][0]['blockerObjectives'] = ['Find the Gate']
    assert diff(B.read_json(path), doc)


def failing(change):
    p = plan()
    change(p)
    return B.build(p, PROVEN)[1]


def test_lock_on_a_missing_objective_fails():
    def change(p):
        p['objectives'][2]['after'] = ['Nothing']
    assert any('not in the plan' in f for f in failing(change))


def test_a_loop_of_locks_fails():
    def change(p):
        p['objectives'][1]['after'] = ['Gate Two Free Roam']
    assert any('never be finished' in f for f in failing(change))


def test_locking_everything_on_the_first_map_fails():
    def change(p):
        p['objectives'][0]['after'] = ['Gate Two Free Roam']
        p['objectives'][1]['after'] = ['Gate One Free Roam']
    assert any('never be finished' in f for f in failing(change))


def test_missing_zone_fails():
    def change(p):
        p['objectives'][1]['stages'][0]['visit'][0]['zone'] = 'nowhere'
    assert any('no zone called' in f for f in failing(change))


def test_map_without_free_roam_fails():
    p = plan()
    p['objectives'].append({'name': 'Odd', 'map': 'level_gate_two',
                            'stages': [{'visit': [{'zone': 'gate_two'}]}]})
    del p['objectives'][2]
    assert any('no Free Roam' in f for f in B.build(p, PROVEN)[1])


def test_two_objectives_with_one_name_fail():
    def change(p):
        p['objectives'][1]['name'] = 'Gate One Free Roam'
        p['objectives'][2]['after'] = ['Gate One Free Roam']
    assert any('two objectives' in f for f in failing(change))


def test_all_hidden_camps_trap_the_player():
    def change(p):
        p['hidden_camps'] = ['level_gate_one', 'level_gate_two']
    assert any('never be finished' in f for f in failing(change))


def test_cross_map_stage_is_allowed():
    p = plan()
    p['objectives'][1]['stages'].append({'visit': [{'zone': 'level_gate_two:gate_two'}]})
    _, fails, _ = B.build(p, PROVEN)
    assert not fails, fails


def test_objectives_outside_the_plan_are_kept():
    p = plan()
    p['objectives'] = [o for o in p['objectives'] if o['name'] != 'Find the Gate']
    p['objectives'][1]['after'] = []
    with tempfile.TemporaryDirectory() as d:
        for lv in ('level_gate_one', 'level_gate_two'):
            shutil.copytree(os.path.join(PROVEN, lv), os.path.join(d, lv))
        files, fails, warns = B.build(p, d)
        assert not fails, fails
        doc = files[os.path.join(d, 'level_gate_one', B.OBJECTIVES)]
    assert 'Find the Gate' in [c['name'] for c in doc['SslValue']['contractsSettings']]
    assert any('keeps Find the Gate' in w for w in warns)


def test_open_base_matches_a_working_build_camp():
    ref = B.read_json(os.path.join(CAMP, B.OBJECTIVES))['SslValue']['contractsSettings']
    want = [c for c in ref if c['name'] == 'Build camp'][0]['rewards']
    obj = {'name': 'Build camp', 'map': 'level_objectives', 'money': 5000, 'stages': [],
           'open_base': {'zone': 'FOB', 'modules': [['mod a', 'mod 1'], ['mod b', 'mod 2'],
                                                    ['mod c', 'mod 3']]}}
    got = B.contract(obj, 'x')['rewards']
    assert not diff(want, got), diff(want, got)[:6]


def base_plan():
    p = plan()
    p['objectives'][1]['open_base'] = {'zone': 'level_gate_two:gate_two', 'modules': [['gate_two', 'deploy']]}
    return p


def test_open_base_writes_the_interaction_prop():
    p = base_plan()
    p['objectives'][1]['open_base']['zone'] = 'level_gate_one:gate_one'
    p['objectives'][1]['open_base']['modules'] = [['gate_one', 'deploy']]
    files, fails, _ = B.build(p, PROVEN)
    assert not fails, fails
    doc = files[os.path.join(PROVEN, 'level_gate_one', B.ZONES)]
    z = [x for x in doc['SslValue']['zoneDesc'] if x['name'] == 'gate_one'][0]
    prop = z['props']['ZonePropertyBaseBuilding']
    assert prop['modelPlacementZone']['globalZoneId'].endswith('|| deploy')
    assert prop['zoneDiscoverability']['state'] == 0


def test_open_base_on_a_missing_zone_fails():
    p = base_plan()
    p['objectives'][1]['open_base']['zone'] = 'level_gate_two:no_site'
    assert any('no zone called no_site' in f for f in B.build(p, PROVEN)[1])


def test_open_base_on_a_deploy_zone_fails():
    p = base_plan()
    p['objectives'][1]['open_base']['zone'] = 'level_gate_two:deploy'
    assert any('already a deploy zone' in f for f in B.build(p, PROVEN)[1])


def test_retire_removes_an_old_objective():
    p = plan()
    p['objectives'] = [o for o in p['objectives'] if o['name'] != 'Find the Gate']
    p['objectives'][1]['after'] = []
    p['retire'] = ['Find the Gate']
    files, fails, warns = B.build(p, PROVEN)
    assert not fails, fails
    doc = files[os.path.join(PROVEN, 'level_gate_one', B.OBJECTIVES)]
    assert 'Find the Gate' not in [c['name'] for c in doc['SslValue']['contractsSettings']]


def camp_objective(name):
    doc = B.read_json(os.path.join(CAMP, B.OBJECTIVES))['SslValue']
    return [c for c in doc['contractsSettings'] + doc['tasksSettings'] if c['name'] == name][0]


def test_task_reproduces_a_working_task():
    obj = {'name': 'ZZ Salvage Run', 'map': 'level_objectives', 'kind': 'task', 'giver': 'mod a',
           'desc': 'x', 'money': 6000,
           'fields': {'description': 'A short two-stage task.',
                      'uiDesc': 'Pull the anchor off pad 2 and drop it at the deploy point.'},
           'stages': [
               {'take': [{'zone': 'mod 2', 'item': 'Anchor_not_removable', 'type': 4,
                          'text': 'Take the anchor from pad 2.', 'message': 'Loaded.'}]},
               {'deliver': [{'zone': 'deploy', 'item': 'Anchor_not_removable', 'type': 4,
                             'text': 'Drop it at the deploy point.'}],
                'deliver_message': 'Task complete.'}]}
    d = diff(camp_objective('ZZ Salvage Run'), B.contract(obj, 'x'))
    assert not d, d[:8]


def test_contract_reproduces_a_working_truck_delivery():
    obj = {'name': 'deliver truck', 'map': 'level_objectives', 'kind': 'contract', 'money': 5000,
           'fields': {'description': 'money!', 'uiDesc': 'Deliver the truck to base camp',
                      'employer': 'TVCompany', 'prepayment': 5000},
           'stages': [{'truck': [{'truck': 'lo4f', 'zone': 'FOB', 'after': 'reward', 'timeout': 0,
                                  'text': 'Deliver Lo4f to base', 'message': 'Thank you!'}]}]}
    d = diff(camp_objective('deliver truck'), B.contract(obj, 'x'))
    assert not d, d[:8]


def small(objectives, **extra):
    p = plan()
    p['objectives'] += objectives
    p.update(extra)
    return B.build(p, PROVEN)


def test_task_without_giver_fails():
    o = {'name': 'T', 'map': 'level_gate_one', 'kind': 'task', 'stages': [{'visit': [{'zone': 'gate_one'}]}]}
    assert any('no giver' in f for f in small([o])[1])


def test_task_writes_the_giver_prop_and_the_task_list():
    o = {'name': 'T', 'map': 'level_gate_one', 'kind': 'task', 'giver': 'gate_one',
         'stages': [{'visit': [{'zone': 'gate_one'}]}]}
    files, fails, _ = small([o])
    assert not fails, fails
    doc = files[os.path.join(PROVEN, 'level_gate_one', B.ZONES)]
    z = [x for x in doc['SslValue']['zoneDesc'] if x['name'] == 'gate_one'][0]
    assert 'ZonePropertyTaskGiver' in z['props']
    obj = files[os.path.join(PROVEN, 'level_gate_one', B.OBJECTIVES)]['SslValue']
    assert [t['name'] for t in obj['tasksSettings']] == ['T']
    assert 'T' not in [c['name'] for c in obj['contractsSettings']]


def test_take_without_an_item_fails():
    o = {'name': 'T', 'map': 'level_gate_one', 'stages': [{'take': [{'zone': 'gate_one'}]}]}
    assert any('item and its type' in f for f in small([o])[1])


def test_delivery_writes_a_blank_id():
    o = {'name': 'T', 'map': 'level_gate_one', 'stages': [{'deliver': [{'zone': 'gate_one', 'type': 14}]}]}
    files, fails, warns = small([o])
    assert not fails and not [w for w in warns if 'delivery' in w], (fails, warns)
    inv = B.contract(o, 'x')['stages'][0]['objectConstruction']['zoneToFill'][0]['inventory']
    assert inv['invId'] == '' and inv['type'] == 14


def test_named_delivery_warns_about_the_patch_and_writes_the_id():
    o = {'name': 'T', 'map': 'level_gate_one', 'stages': [{'deliver': [{'zone': 'gate_one', 'type': 14, 'item': 'x'}]}]}
    assert any('named delivery patch' in w for w in small([o])[2])
    assert B.contract(o, 'x')['stages'][0]['objectConstruction']['zoneToFill'][0]['inventory']['invId'] == 'x'


def test_named_delivery_on_a_type_with_no_default_fails():
    o = {'name': 'T', 'map': 'level_gate_one', 'stages': [{'deliver': [{'zone': 'gate_one', 'type': 42, 'item': 'x'}]}]}
    assert any('no default item' in f for f in small([o])[1])


def test_empty_delivery_fails():
    o = {'name': 'T', 'map': 'level_gate_one', 'stages': [{'deliver': [{'zone': 'gate_one'}]}]}
    assert any('nothing to deliver' in f for f in small([o])[1])


def test_lock_on_a_task_uses_the_region_id():
    # Seen in game 2026-11-08: tasks played in region gateway_test were saved as
    # mod_gateway_test_<task>; locks naming mod_level_gate_two_<task> never held.
    t = {'name': 'T', 'map': 'level_gate_two', 'kind': 'task', 'giver': 'gate_two',
         'stages': [{'visit': [{'zone': 'gate_two'}]}]}
    e = {'name': 'E', 'map': 'level_gate_one', 'after': ['T'], 'stages': [{'visit': [{'zone': 'gate_one'}]}]}
    files, fails, _ = small([t, e])
    assert not fails, fails
    doc = files[os.path.join(PROVEN, 'level_gate_one', B.OBJECTIVES)]['SslValue']['contractsSettings']
    assert [c for c in doc if c['name'] == 'E'][0]['blockerObjectives'] == ['mod_%s_T' % plan()['region']]


def test_fuel_delivery_needs_no_item():
    o = {'name': 'T', 'map': 'level_gate_one', 'stages': [{'deliver': [{'zone': 'gate_one', 'fuel': 100}]}]}
    files, fails, _ = small([o])
    assert not fails, fails
    c = B.contract(o, 'x')['stages'][0]['objectConstruction']['zoneToFill'][0]
    assert c['fuelNum'] == 100 and c['inventory'] is None


def test_truck_stage_warns_and_defaults_to_destroy():
    o = {'name': 'T', 'map': 'level_gate_one', 'stages': [{'truck': [{'truck': 'lo4f', 'zone': 'gate_one'}]}]}
    _, fails, warns = small([o])
    assert not fails, fails
    assert any('check truck lo4f' in w for w in warns)
    assert B.contract(o, 'x')['stages'][0]['truckDelivery'][0]['afterStageFinished'] == 2


def test_stage_money_pays_on_the_stage():
    o = {'name': 'T', 'map': 'level_gate_one', 'stages': [{'visit': [{'zone': 'gate_one'}], 'money': 100}]}
    c = B.contract(o, 'x')
    assert c['stages'][0]['rewards']['ObjectiveRewardMoney']['amount'] == 100 and c['rewards'] == {}


def test_unlock_and_item_rewards_match_vanilla():
    # vanilla shapes, initial.cache_block: ObjectiveRewardUnlock {itemToUnlock = "yar_87"},
    # ObjectiveRewardItem {id = "don_71_roofrack_01"}; a second one of a kind is <class>_1
    o = {'name': 'T', 'map': 'level_gate_one', 'unlock': ['yar_87', 'TRUCK_SELL_UNLOCK'],
         'item': ['don_71_roofrack_01'], 'stages': [{'visit': [{'zone': 'gate_one'}]}]}
    r = B.contract(o, 'x')['rewards']
    assert r['ObjectiveRewardUnlock'] == {'$type': 'ObjectiveRewardUnlock', 'itemToUnlock': 'yar_87'}, r
    assert r['ObjectiveRewardUnlock_1']['itemToUnlock'] == 'TRUCK_SELL_UNLOCK', r
    assert r['ObjectiveRewardItem'] == {'$type': 'ObjectiveRewardItem', 'id': 'don_71_roofrack_01'}, r


def test_item_reward_on_a_stage_pays_on_the_stage():
    o = {'name': 'T', 'map': 'level_gate_one',
         'stages': [{'visit': [{'zone': 'gate_one'}], 'item': ['don_71_roofrack_01']}]}
    c = B.contract(o, 'x')
    assert c['stages'][0]['rewards']['ObjectiveRewardItem']['id'] == 'don_71_roofrack_01'
    assert c['rewards'] == {}


def test_unlock_given_as_a_string_fails():
    o = {'name': 'T', 'map': 'level_gate_one', 'unlock': 'yar_87',
         'stages': [{'visit': [{'zone': 'gate_one'}]}]}
    _, fails, _ = small([o])
    assert any('list of ids' in f for f in fails), fails


def test_two_jobs_in_one_stage():
    o = {'name': 'T', 'map': 'level_gate_one', 'stages': [{
        'visit': [{'zone': 'gate_one'}], 'take': [{'zone': 'gate_one', 'item': 'x', 'type': 4}]}]}
    st = B.contract(o, 'x')['stages'][0]
    assert st['visitAllZones'] and st['takeInventory']


def test_lock_on_a_task_is_accepted():
    t = {'name': 'T', 'map': 'level_gate_one', 'kind': 'task', 'giver': 'gate_one',
         'stages': [{'visit': [{'zone': 'gate_one'}]}]}
    e = {'name': 'E', 'map': 'level_gate_one', 'after': ['T'], 'stages': [{'visit': [{'zone': 'gate_one'}]}]}
    assert not any('untested' in w for w in small([t, e])[2])


def test_hide_writes_the_stage_model_type_the_editor_keeps():
    # The stage field is a ModelBuildingSettings pointer. The zone-side base type
    # ZoneModelBuildingSettings was nulled by the editor's save (2026-11-07).
    o = {'name': 'T', 'map': 'level_gate_one', 'stages': [{'deliver': [
        {'zone': 'gate_one', 'fuel': 100, 'hide': 'det block'}]}]}
    m = B.contract(o, 'x')['stages'][0]['objectConstruction']['zoneToFill'][0]['model']
    assert m == {'$type': 'ModelBuildingSettings', 'dropOnRestart': False,
                 'isNeedRespawnOnReplayAbility': True,
                 'levelName': '~#@%$`*level_gate_one~#@%$`*', 'modelTag': 'det block', 'modelType': 0,
                 'stagesProgress': {'stage_hidden': 1, 'stage_visible': 0}, 'unloadBlockedZoneId': None}


def test_a_delivery_without_hide_leaves_the_model_alone():
    o = {'name': 'T', 'map': 'level_gate_one', 'stages': [{'deliver': [{'zone': 'gate_one', 'fuel': 1}]}]}
    assert B.contract(o, 'x')['stages'][0]['objectConstruction']['zoneToFill'][0]['model'] is None


def loot_task(**stage):
    s = {'deliver': [{'zone': 'gate_one', 'repairs': 2500}], 'open_loot': {'zone': 'gate_one', 'item': 'Box', 'type': 14}}
    s.update(stage)
    return {'name': 'T', 'map': 'level_gate_one', 'stages': [s]}


def test_open_loot_puts_the_item_in_the_zone_when_the_stage_closes():
    r = B.contract(loot_task(), 'x')['stages'][0]['rewards']['ObjectiveRewardOpenZoneProperties']
    z = r['zoneSettings']['ZonePropertiesSettingsObjectiveReward']
    assert z['zoneId'] == 'gate_one' and z['mapId'] == 'level_gate_one'
    st = z['props']['ZonePropertyInventoryStorage']
    assert st['slotsSettings'][0]['possibleItems'] == [{'$type': 'ZoneItem', 'chance': 1, 'itemName': 'Box', 'type': 14}]
    assert st['alwaysSpawn'] is False


def test_open_loot_with_open_base_on_one_stage_fails():
    o = loot_task(open_base={'zone': 'gate_one'})
    assert any('open_loot and open_base' in f for f in small([o])[1])


def test_open_loot_on_a_zone_that_already_has_loot_fails():
    with tempfile.TemporaryDirectory() as d:
        for lv in ('level_gate_one', 'level_gate_two'):
            shutil.copytree(os.path.join(PROVEN, lv), os.path.join(d, lv))
        path = os.path.join(d, 'level_gate_one', B.ZONES)
        doc = B.read_json(path)
        z = [x for x in doc['SslValue']['zoneDesc'] if x['name'] == 'gate_one'][0]
        z.setdefault('props', {})['ZonePropertyInventoryStorage'] = {}
        B.write_json(path, doc)
        p = plan()
        p['objectives'].append(loot_task())
        assert any('already holds loot' in f for f in B.build(p, d)[1])


def test_open_loot_on_a_missing_zone_fails():
    o = loot_task(open_loot={'zone': 'nowhere', 'item': 'Box', 'type': 14})
    assert any('no zone called nowhere' in f for f in small([o])[1])


def test_written_files_read_back_the_same():
    with tempfile.TemporaryDirectory() as d:
        for lv in ('level_gate_one', 'level_gate_two'):
            shutil.copytree(os.path.join(PROVEN, lv), os.path.join(d, lv))
        files, _, _ = B.build(plan(), d)
        for path, doc in files.items():
            B.write_json(path, doc)
            assert not diff(B.read_json(path), doc)
            assert open(path, 'rb').read()[:3] == b'{\r\n'


def main():
    bad = skipped = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith('test_')]
    for name, fn in tests:
        try:
            fn()
            print('%-50s ok' % name)
        except FileNotFoundError as e:   # an input this machine does not have
            skipped += 1
            print('%-50s skipped (missing %s)' % (name, e.filename))
        except Exception as e:      # noqa: BLE001
            bad += 1
            print('%-50s FAILED %s' % (name, str(e)[:700]))
    print('%d of %d passed, %d skipped' % (len(tests) - bad - skipped, len(tests), skipped))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
