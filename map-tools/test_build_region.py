"""Gate for build_region.py.

The reference is the region that ran the full locked-map chain on 2026-11-05, kept in
_staging\\gateway_chain_proven. The tool, fed only the two installed maps, has to
reproduce it, so this test needs re-staging whenever the Gate maps change on purpose.

    python test_build_region.py
"""
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_region as B  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.environ.get('EXPEDITIONS_FIXTURES') or next(
    (d for d in (os.path.join(_HERE, 'fixtures'), os.path.join(os.path.dirname(_HERE), '_staging'))
     if os.path.isdir(d)), os.path.join(_HERE, 'fixtures'))
# reference data recorded from in-game tests; not shipped. Without it those tests skip.
STAGED = os.path.join(FIXTURES, 'gateway_chain_proven')
KNOWN = set()


def load(path):
    with open(path, encoding='utf-8-sig') as f:
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


def test_reproduces_the_region_that_crossed():
    desc = load(os.path.join(STAGED, 'regionDesc.json'))
    files = B.build(desc, B.MODS)
    assert sorted(files) == sorted(os.listdir(STAGED)), sorted(files)
    left = []
    for name, built in files.items():
        path = os.path.join(STAGED, name)
        if name.endswith('.txt'):
            assert built == open(path, encoding='utf-8').read().strip()
            continue
        left += ['%s %s' % (name, d) for d in diff(load(path), json.loads(json.dumps(built)))
                 if d.split(':')[0].rsplit('/', 1)[-1] not in KNOWN]
    assert not left, left


def test_negative_control_sees_a_wrong_key():
    desc = load(os.path.join(STAGED, 'regionDesc.json'))
    built = B.build(desc, B.MODS)['zone_settings.json']
    built['SslValue']['data']['gateway_test'] = built['SslValue']['data'].pop(B.KEY)
    assert diff(load(os.path.join(STAGED, 'zone_settings.json')), built)


def test_every_fault_stays_fixed():
    desc = load(os.path.join(STAGED, 'regionDesc.json'))
    files = B.build(desc, B.MODS)
    ssl = files['zone_settings.json']['SslValue']
    maps = ssl['data']['mod_region']['mapDescs']
    assert list(ssl['data']) == ['mod_region'] and ssl['regions'][0]['name'] == 'mod_region'
    assert 'modId' not in maps[0] and maps[1]['modId'] == 'mod_gateway_test'
    for m in maps:
        assert m['uiFuelConsumptionModifier'] == 1.0 and m['noFlyZone'] == -1.0
        assert m['caveSettings']['isCave'] is False and m['uiImages'] == []
        gate = m['zoneDesc'][1]['props']['ZonePropertyGateway']
        assert gate['levelZoneLink'].startswith('mod_level_')
        assert gate['isShown'] is True and gate['isAffectedByZoneSettings'] is True
        assert '$type' not in gate['cameraDir']
    for c in files['objective_settings.json']['SslValue']['contractsSettings']:
        assert c['region'] == 'mod_region'


def test_refresh_keeps_hand_set_values():
    desc = load(os.path.join(STAGED, 'regionDesc.json'))
    with tempfile.TemporaryDirectory() as d:
        files = B.build(desc, B.MODS)
        m = files['zone_settings.json']['SslValue']['data']['mod_region']['mapDescs'][1]
        m['caveSettings']['isCave'] = True
        m['uiImages'] = ['popupX', 'popupGrayX']
        B.write(d, files)
        again, kept = B.load_existing(d)
        out = B.build(again, B.MODS, kept)
    m = out['zone_settings.json']['SslValue']['data']['mod_region']['mapDescs'][1]
    assert m['caveSettings']['isCave'] is True and m['uiImages'] == ['popupX', 'popupGrayX']


def test_files_are_lf_and_refused_when_a_map_is_missing():
    desc = load(os.path.join(STAGED, 'regionDesc.json'))
    with tempfile.TemporaryDirectory() as d:
        B.write(d, B.build(desc, B.MODS))
        assert b'\r' not in open(os.path.join(d, 'zone_settings.json'), 'rb').read()
    desc['LevelList'][1]['LevelName'] = 'level_not_there'
    try:
        B.build(desc, B.MODS)
    except ValueError:
        return
    raise AssertionError('should refuse')


def main():
    bad = skipped = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith('test_')]
    for name, fn in tests:
        try:
            fn()
            print('%-48s ok' % name)
        except FileNotFoundError as e:   # an input this machine does not have
            skipped += 1
            print('%-48s skipped (missing %s)' % (name, e.filename))
        except Exception as e:      # noqa: BLE001
            bad += 1
            print('%-48s FAILED %s' % (name, str(e)[:900]))
    print('%d of %d passed, %d skipped' % (len(tests) - bad - skipped, len(tests), skipped))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
