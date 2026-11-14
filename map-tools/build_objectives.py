"""build_objectives.py -- write a chain of objectives across linked maps from one plan file.

WHY IT EXISTS. Saber's editor hides the field that locks an objective, and a lock
written by hand fails silently. Everything here was proven in game on 2026-11-05
(RE\\PATCH_LOG.md):

  blockerObjectives    hidden in the editor but kept by a pack; locks a menu row
  full saved name      a lock must hold `mod_<region>_<contract>`. A plain name
                       shows the lock and never clears
  hidden camp          a deploy zone set NOT_EXPLORED; found by driving to it, and
                       deployable once the map unlocks
  gateways             reach a locked map while it is locked

A plan is one JSON file. See objective_plans\\gate_test.json:

  {"region": "gateway_test",
   "hidden_camps": ["level_gate_two"],
   "objectives": [
     {"name": "Find the Gate", "map": "level_gate_one", "desc": "Drive to the gate.",
      "stages": [{"visit": [{"zone": "gate_one", "text": "Reach the gate",
                             "message": "You found the gate.", "radius": 30}]}]},
     {"name": "Gate Two Free Roam", "map": "level_gate_two", "free_roam": true,
      "desc": "Gate Two", "after": ["Find the Gate"]}]}

`after` takes plain names: the tool writes the full saved name. `fields` sets any
other contract field. A zone on another map is written `level_x:zone`. `money` pays
out when the objective finishes. `retire` removes old objectives by name.

`open_base` builds a base as the reward, the way a Build camp contract does: the
site zone becomes a deploy point with a supply shop, and each module is a pair of
zones, the one you interact with and the one the model is built on:

  "open_base": {"zone": "base_site", "modules": [["base_mod_a", "base_mod_1"]]}

`kind` is `expedition` (the default), `contract`, or `task`. A task needs `giver`, the
zone that hands it out. A stage may hold several of these, done in any order:

  "visit":   [{"zone", "text", "message", "radius"}]            reach zones
  "take":    [{"zone", "item", "type", "count", "text", "message"}]   collect items
  "deliver": [{"zone", "item", "type", "count", "fuel", "repairs", "power",
               "text", "message"}]                              bring things to a zone
  "truck":   [{"truck", "name", "zone", "after", "text", "message"}]  bring a placed truck

`item` and `type` are an inventory id and its type number. A delivery of a custom
item names it and gives a type that has a default item, such as 14. It needs the
named delivery patch in initial.pak (RE\scripts\patch_named_delivery.py), proven
2026-11-06. Without the patch only a type's default item delivers, with `item` left
out. A delivery on a type with no default item, such as 42, crashes on the key press. A
stage's own `money` or `open_base` pays when that stage closes, not at the end.

`"unlock": ["yar_87"]` unlocks a truck (ObjectiveRewardUnlock), the way vanilla rescue
tasks do; `"item": ["don_71_roofrack_01"]` unlocks an upgrade (ObjectiveRewardItem), the
way vanilla contracts pay parts. Both go on an objective or a stage. Vanilla pairs them
with the class: a truck's GameData UnlockByObjective names the objective, an upgrade's
GameData carries AddonUnlockByObjective="true". PROVEN on a mod truck and all its parts,
2026-11-13 (notes/unlock_test.md). vehicle_unlocks.py does the truck-mod side from this same
plan: the locks, the reward id forms, and the parked truck's map-prefixed Id.

A delivery with `"hide": "<model tag>"` switches that model from stage_visible to
stage_hidden when it completes, the same switch an obstacle zone uses. Obstacle zones
(ZonePropertyObjectConstructionInZone) can't take items: Saber marks the item list
"not implemented", and the popup counts every item as x0 (2026-11-07).

`"open_loot": {"zone", "item", "type"}` on a stage puts one item in a zone when the
stage closes. The zone must hold no loot of its own, or the item is free from the
start. It shares the reward slot with `open_base`, so not both on one stage.

The zones must exist first: add them to prebuild\\<map>\\_zones.xml and the map's
zone_settings.json. Their interaction props are written here, starting hidden.

A two-stage Expedition across two maps, with this reward, finished in game on
2026-11-05, once after_pack had corrected its references to the far map.

It refuses a plan that can trap a player: a lock nothing can open, a map with no
Free Roam, a zone that does not exist, a stage with nothing required.

    python build_objectives.py <plan.json>            report only
    python build_objectives.py <plan.json> --apply    write the map sources

Writes Media\\levels\\<map>. Objectives not in the plan are kept. Then pack each
map and run after_pack. Close the game and the editor first.
"""
import copy
import json
import os
import re
import subprocess
import sys

MEDIA = os.path.join(os.path.expanduser('~'), 'Documents', 'My Games', 'Expeditions', 'Media')
LEVELS = os.path.join(MEDIA, 'levels')
TAG = '~#@%$`*'
CUTTER = ' || '
OBJECTIVES = 'objective_settings.json'
ZONES = 'zone_settings.json'

CONTRACT = {
    '$type': 'ContractSettings', 'blockerMap': '', 'blockerObjectives': [], 'bonusRewards': {},
    'contractMap': '', 'contractType': 1, 'customMessage': '', 'description': '', 'difficulty': 0,
    'employer': 'ExpeditionsInc', 'failReasons': {}, 'forcedDaytime': None, 'isCoop': False,
    'isMarkersDisabled': False, 'isNeedRaceMusic': False, 'isNotSkippable': False,
    'isRepeatable': False, 'isSilentReward': False, 'isTutorial': False, 'minimapList': 0,
    'model': None, 'name': '', 'prepayment': 0, 'recommendedDeployZone': '', 'recommendedTruck': 0,
    'region': 'mod_region', 'replayabilityResetModels': [], 'requiredInventory': [],
    'requiredSpecialities': [], 'requiredTruckAddons': [], 'rewards': {}, 'sandboxObjetive': False,
    'stages': [], 'tips': [], 'trucksWithoutForceGrounding': [], 'uiDesc': '',
    'uiMinimapScreenshot': 'mapAllLocked', 'wayLength': 0,
}
STAGE = {
    '$type': 'ObjectiveStage', 'cutsceneInfo': None, 'freeRoamInfo': None, 'makeActionInZone': None,
    'minigameInfo': None, 'needCreateOverlay': False, 'objectConstruction': None,
    'objectInteraction': None, 'otherMapGatewaySelectionSettings': None, 'puzzleInfo': None,
    'rewards': {}, 'scoutingInfo': None, 'takeInventory': None, 'truckDelivery': [],
    'tutorialViewsOnCompleteStage': None, 'visitAllZones': None,
}
# ZoneDiscoveryState: state 0 is NOT_EXPLORED; specialist 6 is NONE, as the editor writes it.
TASK = {
    '$type': 'TaskSettings', 'additionalTaskGivers': [], 'blockerMap': '', 'blockerObjectives': [],
    'bonusRewards': {}, 'customMessage': '', 'description': '', 'forcedDaytime': None, 'isCoop': False,
    'isMarkersDisabled': False, 'isNeedRaceMusic': False, 'isNotSkippable': False,
    'isRepeatable': False, 'isSilentReward': False, 'isTutorial': False, 'minimapList': 0,
    'model': None, 'name': '', 'recommendedTruck': 0, 'requiredInventory': [],
    'requiredSpecialities': [], 'requiredTruckAddons': [], 'rewards': {}, 'sandboxObjetive': False,
    'stages': [], 'taskGiverZone': None, 'trucksWithoutForceGrounding': [], 'uiDesc': '',
}
# afterStageFinished as saved. Saber: objectives in Expeditions use destroy.
AFTER = {'reward': 0, 'detach': 1, 'destroy': 2, 'nothing': 3}
STAGE_KEYS = ('visit', 'take', 'deliver', 'truck')
NO_DEFAULT_ITEM = {2, 7, 42, 86, 87}   # typeDescs entries that are blank or null, read from a live dump
HIDDEN = {
    '$type': 'ZoneDiscoveryState', 'discoverableByBinocular': True, 'discoverableByDrone': True,
    'discoverableByTruck': True, 'discoveryMessage': 'Camp found.', 'discoveryRewardMoney': None,
    'requiredSpecialistBinocular': 6, 'requiredSpecialistDrone': 6, 'state': 0,
}


def saved_name(region, name, task_map=None):
    """The name the game saves a finished objective under: mod_<region>_<name>, for
    Tasks too. Seen in game 2026-11-08: Gate Two tasks played in region gateway_test
    were saved as mod_gateway_test_<task>, so a lock naming the map never held and
    every task was open. (The 11-06 "map id" note came from a map with no region.)
    task_map is kept only so callers need not change; it is ignored."""
    return 'mod_%s_%s' % (region, name)


def tagged(level):
    return '%s%s%s' % (TAG, level, TAG)


def split_zone(ref, home):
    return ref.split(':', 1) if ':' in ref else (home, ref)


def note(text, timeout=10):
    if not text:
        return None
    return {'$type': 'NotificationBase', 'image': '', 'textFrames': [text], 'timeout': timeout}


def pack(entry):
    return {'$type': 'InventoryPack', 'count': entry.get('count', 1), 'invId': entry.get('item', ''),
            'type': entry['type']}


def take_stage(stage, home):
    zones = []
    for v in stage['take']:
        level, zone = split_zone(v['zone'], home)
        zones.append({'$type': 'TakeInventorySettings', 'OptionalToPerform': False, 'fakeZones': [],
                      'inventoryToTake': [pack(v)], 'model': None, 'notification': note(v.get('message')),
                      'radiusSett': None, 'uiDesc': v.get('text', ''),
                      'zoneGlobalId': zone_id(level, zone)})
    return {'$type': 'TakeInventoryPackSettings', 'zoneToTakeInvFrom': zones}


def deliver_stage(stage, home):
    zones = []
    for v in stage['deliver']:
        level, zone = split_zone(v['zone'], home)
        zones.append({'$type': 'ObjectConstructionZoneSetting', 'OptionalToPerform': False,
                      'additionalUnloadZones': [], 'electroNum': v.get('power', 0), 'fakeZones': [],
                      'fuelNum': v.get('fuel', 0), 'globalZoneId': zone_id(level, zone), 'inventories': {},
                      'inventory': pack(v) if 'type' in v else None,
                      'model': hide_model(level, v['hide']) if v.get('hide') else None,
                      'modelPlacementZones': [], 'notification': note(v.get('message')),
                      'radiusSett': None, 'repairsNum': v.get('repairs', 0), 'uiDesc': v.get('text', '')})
    return {'$type': 'ObjectConstructionPackSettings', 'notification': note(stage.get('deliver_message')),
            'radiusSett': None, 'zoneToFill': zones}


def hide_model(level, tag):
    """The switch an obstacle zone uses: the tagged model shows stage_visible until the
    delivery is complete, then stage_hidden. The model's brand must carry both stages.
    The stage field points at ModelBuildingSettings; the zone-side base type
    ZoneModelBuildingSettings is nulled by the editor's save (seen 2026-11-07)."""
    return {'$type': 'ModelBuildingSettings', 'dropOnRestart': False, 'isNeedRespawnOnReplayAbility': True,
            'levelName': tagged(level), 'modelTag': tag, 'modelType': 0,
            'stagesProgress': {'stage_hidden': 1, 'stage_visible': 0}, 'unloadBlockedZoneId': None}


def loot_reward(spec, home):
    """ObjectiveRewardOpenZoneProperties that gives a zone one item to collect, once."""
    level, zone = split_zone(spec['zone'], home)
    storage = {
        '$type': 'ZonePropertyInventoryStorage', 'allowPutConsumablesToHeavyInv': False,
        'alwaysSpawn': False, 'capacity': 6, 'electroSlotSettings': None,
        'expandedConsumablesCapacity': 2, 'fuelSlotSettings': None, 'heavyInvCapacity': 0,
        'isAffectedByAdditionalAirdropResourcesAbility': False,
        'isAffectedByAdditionalOutpostResourcesAbility': False, 'isAffectedByZoneSettings': True,
        'isAtDeployZone': False, 'isModelBreakageQuestProp': False, 'isShown': True, 'model': None,
        'repairsSlotSettings': None, 'slotsSettings': [{'$type': 'ZoneItemSlot', 'possibleItems': [
            {'$type': 'ZoneItem', 'chance': 1, 'itemName': spec['item'], 'type': spec['type']}]}],
        'wheelsSlotSettings': None, 'zoneDiscoverability': None}
    return {'$type': 'ObjectiveRewardOpenZoneProperties', 'zoneSettings': {
        'ZonePropertiesSettingsObjectiveReward': {
            # A plain map name, never tagged: all 224 vanilla mapIds are plain names, and the
            # tagged one reached the game unresolved and filled nothing (2026-11-08).
            # after_pack's fix_gateway_links turns level_x into mod_level_x in the pak.
            '$type': 'ZonePropertiesSettingsObjectiveReward', 'mapId': level,
            'props': {'ZonePropertyInventoryStorage': storage}, 'zoneId': zone}}}


def truck_stage(stage, home):
    out = []
    for v in stage['truck']:
        level, zone = split_zone(v['zone'], home)
        out.append({'$type': 'TruckDeliverySettings', 'additionalDeliveryZones': [],
                    'afterStageFinished': AFTER[v.get('after', 'destroy')],
                    'globalZoneDeliveryId': zone_id(level, zone), 'isListeningTruckOnly': False,
                    'mustDestroyAfterSubstage': False, 'notification': note(v.get('message'), v.get('timeout', 10)),
                    'radiusSett': None, 'truckDeliveryEffects': None, 'truckId': v['truck'],
                    'truckUiName': v.get('name', v['truck']), 'uiDesc': v.get('text', '')})
    return out


UNLOCK_REWARDS = (('unlock', 'ObjectiveRewardUnlock', 'itemToUnlock'),
                  ('item', 'ObjectiveRewardItem', 'id'))


def rewards_of(spec, home):
    out = {}
    if 'money' in spec:
        out['ObjectiveRewardMoney'] = {'$type': 'ObjectiveRewardMoney', 'amount': spec['money']}
    if 'open_base' in spec:
        out['ObjectiveRewardOpenZoneProperties'] = base_reward(spec['open_base'], home)
    elif 'open_loot' in spec:
        out['ObjectiveRewardOpenZoneProperties'] = loot_reward(spec['open_loot'], home)
    # vanilla: a truck is ObjectiveRewardUnlock, an upgrade ObjectiveRewardItem; repeats
    # of one class are keyed <class>_1, _2 (initial.cache_block, 2026-11-13)
    for key, cls, field in UNLOCK_REWARDS:
        for i, ident in enumerate(spec.get(key, [])):
            out[cls + ('_%d' % i if i else '')] = {'$type': cls, field: ident}
    return out


def stage_doc(s, home):
    stage = copy.deepcopy(STAGE)
    if 'visit' in s:
        stage['visitAllZones'] = visit_stage(s, home)
    if 'take' in s:
        stage['takeInventory'] = take_stage(s, home)
    if 'deliver' in s:
        stage['objectConstruction'] = deliver_stage(s, home)
    if 'truck' in s:
        stage['truckDelivery'] = truck_stage(s, home)
    stage['rewards'] = rewards_of(s, home)
    return stage


def visit_stage(stage, home):
    radius = next((v['radius'] for v in stage['visit'] if 'radius' in v), None)
    zones = []
    for v in stage['visit']:
        level, zone = split_zone(v['zone'], home)
        zones.append({
            '$type': 'TaskSettingVisitZone', 'OptionalToPerform': False, 'fakeZones': [],
            'globalZoneId': {'$type': 'ZoneGlobalIdentificator',
                             'globalZoneId': tagged(level) + CUTTER + zone},
            'mayVisitZoneWithDrone': False,
            'notification': {'$type': 'NotificationBase', 'image': '',
                             'textFrames': [v['message']] if v.get('message') else [], 'timeout': 10},
            'radiusSett': None, 'truckUid': '', 'uiDesc': v.get('text', ''),
            'visitWithDroneOnly': False})
    settings = None
    if radius is not None:
        settings = {'$type': 'ZoneRadiusSettings', 'forceOffset': True, 'offsetX': 0, 'offsetY': 0,
                    'zoneRadius': radius}
    return {'$type': 'VisitAllZonesSettings', 'notification': None, 'radiusSett': settings,
            'zones': zones}


def zone_id(level, zone):
    return {'$type': 'ZoneGlobalIdentificator', 'globalZoneId': tagged(level) + CUTTER + zone}


def base_reward(spec, home):
    """ObjectiveRewardOpenZoneProperties: turns the site into a working base."""
    level, site = split_zone(spec['zone'], home)
    deploy = {
        '$type': 'ZonePropertyDeploy', 'defaultDeployPoint': False, 'deployCostMultiplier': 1,
        'fobModulesZones': [{'$type': 'FieldModulePlacementZones',
                             'moduleInteractionZone': zone_id(level, use),
                             'modulePlacementZone': zone_id(level, place)}
                            for use, place in spec.get('modules', [])],
        'fogOfWarUnlockRadius': 100, 'fogOfWarUnlockRadiusTransparent': 150,
        'isAffectedByZoneSettings': True, 'isShown': True,
        'mainBaseModuleInteractionZone': zone_id(level, site), 'maxTrucksNum': 1, 'model': None,
        'supplyShopModuleInteractionZone': zone_id(level, site), 'warningRadius': 5,
        'zoneDiscoverability': None}
    return {'$type': 'ObjectiveRewardOpenZoneProperties', 'zoneSettings': {
        'ZonePropertiesSettingsObjectiveReward': {
            '$type': 'ZonePropertiesSettingsObjectiveReward', 'mapId': tagged(level),
            'props': {'ZonePropertyDeploy': deploy}, 'zoneId': site}}}


def contract(obj, region, task_maps=None):
    kind = obj.get('kind', 'expedition')
    c = copy.deepcopy(TASK if kind == 'task' else CONTRACT)
    if kind == 'contract':
        c['contractType'] = 0
    if kind == 'task':
        level, zone = split_zone(obj['giver'], obj['map'])
        c['taskGiverZone'] = zone_id(level, zone)
    c['name'] = obj['name']
    c['description'] = c['uiDesc'] = obj.get('desc', '')
    c['blockerObjectives'] = [saved_name(region, n, (task_maps or {}).get(n))
                              for n in obj.get('after', [])]
    if obj.get('free_roam'):
        stage = copy.deepcopy(STAGE)
        stage['freeRoamInfo'] = {'$type': 'FreeRoamSettings', 'map': tagged(obj['map']),
                                 'uiDesc': obj.get('desc', '')}
        c['stages'] = [stage]
    else:
        c['stages'] = [stage_doc(s, obj['map']) for s in obj.get('stages', [])]
    c['rewards'] = rewards_of(obj, obj['map'])
    c.update(copy.deepcopy(obj.get('fields', {})))
    return c


def read_json(path):
    with open(path, 'rb') as f:
        return json.loads(f.read().decode('utf-8-sig'))


def write_json(path, doc):
    text = json.dumps(doc, indent=4, ensure_ascii=False).replace('\n', '\r\n') + '\r\n'
    with open(path, 'wb') as f:
        f.write(text.encode('utf-8'))


def load_zones(levels, level):
    return {z['name']: z.get('props') or {}
            for z in read_json(os.path.join(levels, level, ZONES))['SslValue']['zoneDesc']}


def gateway_targets(zones):
    out = set()
    for props in zones.values():
        link = (props.get('ZonePropertyGateway') or {}).get('levelZoneLink') or ''
        level = link.split(CUTTER)[0].replace(TAG, '').strip()
        if level:
            out.add(re.sub(r'^mod_', '', level))
    return out


def has_open_camp(zones, hidden):
    return not hidden and any('ZonePropertyDeploy' in p for p in zones.values())


def walk_plan(plan, zones):
    """Play the chain forward. Returns the objective names a player can never finish."""
    hidden = set(plan.get('hidden_camps', []))
    objs = plan['objectives']
    open_maps = {lv for lv, z in zones.items() if has_open_camp(z, lv in hidden)}
    done = set()
    while True:
        reached = set(open_maps)
        grow = True
        while grow:
            grow = False
            for lv in list(reached):
                for target in gateway_targets(zones.get(lv, {})):
                    if target in zones and target not in reached:
                        reached.add(target)
                        grow = True
        new = [o for o in objs if o['name'] not in done and set(o.get('after', [])) <= done
               and o['map'] in reached]
        if not new:
            return [o['name'] for o in objs if o['name'] not in done]
        done |= {o['name'] for o in new}


def problems(plan, zones):
    """Everything that would break the chain or trap the player."""
    fails, warns = [], []
    objs = plan['objectives']
    names = [o['name'] for o in objs]
    names_kind = {o['name']: o.get('kind', 'expedition') for o in objs}
    for n in sorted({n for n in names if names.count(n) > 1}):
        fails.append('two objectives are called %s' % n)
    for o in objs:
        for after in o.get('after', []):
            if after not in names:
                fails.append('%s waits for %s, which is not in the plan' % (o['name'], after))
        for spec in [o] + o.get('stages', []):
            for key, _, _ in UNLOCK_REWARDS:
                ids = spec.get(key, [])
                if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
                    fails.append('%s: %s takes a list of ids' % (o['name'], key))
            if 'open_loot' in spec:
                if 'open_base' in spec:
                    fails.append('%s: open_loot and open_base share one reward slot; '
                                 'put them on different stages' % o['name'])
                level, zone = split_zone(spec['open_loot']['zone'], o['map'])
                there = zones.get(level, {})
                if zone not in there:
                    fails.append('%s: %s has no zone called %s' % (o['name'], level, zone))
                elif 'ZonePropertyInventoryStorage' in there[zone]:
                    fails.append('%s: %s already holds loot, so the item is free from the start'
                                 % (o['name'], zone))
            if 'open_base' not in spec:
                continue
            level, site = split_zone(spec['open_base']['zone'], o['map'])
            there = zones.get(level, {})
            if site not in there:
                fails.append('%s: %s has no zone called %s' % (o['name'], level, site))
            elif 'ZonePropertyDeploy' in there[site]:
                fails.append('%s: %s is already a deploy zone' % (o['name'], site))
            for pair in spec['open_base'].get('modules', []):
                for zone in pair:
                    if zone not in there:
                        fails.append('%s: %s has no zone called %s' % (o['name'], level, zone))
        kind = o.get('kind', 'expedition')
        if kind not in ('expedition', 'contract', 'task'):
            fails.append('%s: unknown kind %s' % (o['name'], kind))
        if kind == 'task':
            if not o.get('giver'):
                fails.append('%s is a task with no giver zone' % o['name'])
            else:
                level, zone = split_zone(o['giver'], o['map'])
                if zone not in zones.get(level, {}):
                    fails.append('%s: %s has no zone called %s' % (o['name'], level, zone))
        if o.get('free_roam'):
            continue
        if not o.get('stages'):
            fails.append('%s has no stages' % o['name'])
        for s in o.get('stages', []):
            if not any(s.get(k) for k in STAGE_KEYS):
                fails.append('%s has a stage with nothing to do' % o['name'])
            for key in STAGE_KEYS:
                for v in s.get(key, []):
                    level, zone = split_zone(v['zone'], o['map'])
                    if zone not in zones.get(level, {}):
                        fails.append('%s: %s has no zone called %s' % (o['name'], level, zone))
                    if key == 'take' and ('item' not in v or 'type' not in v):
                        fails.append('%s: %s needs an item and its type number' % (o['name'], v['zone']))
                    if key == 'deliver':
                        if not any(k in v for k in ('type', 'fuel', 'repairs', 'power')):
                            fails.append('%s: %s has nothing to deliver' % (o['name'], v['zone']))
                        if 'item' in v:
                            warns.append('%s: delivering %s by name needs the named delivery patch '
                                         'in initial.pak' % (o['name'], v['item']))
                            if v.get('type') in NO_DEFAULT_ITEM:
                                fails.append('%s: type %s has no default item, so the delivery '
                                             'crashes on the key press; use type 14' % (o['name'], v['type']))
                    if key == 'truck':
                        if 'truck' not in v:
                            fails.append('%s: a truck stage needs the placed truck id' % o['name'])
                        elif v.get('after', 'destroy') not in AFTER:
                            fails.append('%s: after must be one of %s' % (o['name'], sorted(AFTER)))
                        else:
                            # the game prepends the map id to the task's truckId
                            # (PrependWorldIdForTruckDelivery); the parked truck keeps its
                            # Id as written, so it must already carry the prefix.
                            # Proven 2026-11-13 on level_unlock_test.
                            warns.append('%s: check truck %s is placed on %s with Id "mod_%s_%s" '
                                         '(the map prefix is required); this tool cannot see it'
                                         % (o['name'], v['truck'], level, level, v['truck']))
    for level in zones:
        if not any(o.get('free_roam') and o['map'] == level for o in objs):
            fails.append('%s has no Free Roam objective in this plan' % level)
        if not any('ZonePropertyDeploy' in p for p in zones[level].values()):
            fails.append('%s has no deploy zone' % level)
    for level in plan.get('hidden_camps', []):
        if level not in zones:
            fails.append('hidden camp on %s, which no objective uses' % level)
    for name in walk_plan(plan, zones):
        fails.append('%s can never be finished: its lock or its map cannot be reached' % name)
    return fails, warns


def merged(path, new, retire=()):
    """The map's objective file with the plan's objectives replaced or added."""
    doc = read_json(path)
    names = {c['name'] for c in new} | set(retire)
    kept = []
    for key, fresh in (('contractsSettings', [c for c in new if c['$type'] != 'TaskSettings']),
                       ('tasksSettings', [c for c in new if c['$type'] == 'TaskSettings'])):
        have = doc['SslValue'].get(key, [])
        old = [c for c in have if c['name'] not in names]
        kept += [c['name'] for c in old]
        doc['SslValue'][key] = old + fresh
    return doc, kept


def module_hidden():
    """Opening a module zone before its base exists breaks every popup after it
    (Show[81], 2026-11-05). Working maps start these zones NOT_EXPLORED."""
    state = copy.deepcopy(HIDDEN)
    state['discoveryMessage'] = ''
    return state


def add_module_props(doc, level, modules):
    """Interaction zones need ZonePropertyBaseBuilding, linked to their placement zone."""
    zones = {z['name']: z for z in doc['SslValue']['zoneDesc']}
    for use, place in modules:
        props = zones[use].setdefault('props', {})
        if 'ZonePropertyBaseBuilding' not in props:
            props['ZonePropertyBaseBuilding'] = {
                '$type': 'ZonePropertyBaseBuilding', 'isAffectedByZoneSettings': True,
                'isShown': True, 'modelPlacementZone': zone_id(level, place),
                'zoneDiscoverability': module_hidden()}


def add_giver_prop(doc, zone):
    zones = {z['name']: z for z in doc['SslValue']['zoneDesc']}
    zones[zone].setdefault('props', {}).setdefault('ZonePropertyTaskGiver', {
        '$type': 'ZonePropertyTaskGiver', 'isAffectedByZoneSettings': True, 'isShown': True,
        'zoneDiscoverability': None})


def hide_camp(doc):
    for z in doc['SslValue']['zoneDesc']:
        deploy = (z.get('props') or {}).get('ZonePropertyDeploy')
        if deploy is not None and not deploy.get('zoneDiscoverability'):
            deploy['zoneDiscoverability'] = copy.deepcopy(HIDDEN)


def open_programs():
    out = subprocess.run('tasklist', capture_output=True, text=True).stdout.lower()
    return 'expeditions' in out


def build(plan, levels=LEVELS):
    """{path: document} for every source file the plan changes, plus the problems."""
    maps = sorted({o['map'] for o in plan['objectives']})
    zones = {lv: load_zones(levels, lv) for lv in maps}
    fails, warns = problems(plan, zones)
    files = {}
    if not fails:
        for lv in maps:
            tasks = {o['name']: o['map'] for o in plan['objectives'] if o.get('kind') == 'task'}
            new = [contract(o, plan['region'], tasks) for o in plan['objectives'] if o['map'] == lv]
            path = os.path.join(levels, lv, OBJECTIVES)
            files[path], kept = merged(path, new, plan.get('retire', []))
            for name in kept:
                warns.append('%s keeps %s, which is not in the plan' % (lv, name))

        def zone_doc(lv):
            path = os.path.join(levels, lv, ZONES)
            if path not in files:
                files[path] = read_json(path)
            return files[path]

        for lv in plan.get('hidden_camps', []):
            hide_camp(zone_doc(lv))
        for o in plan['objectives']:
            for spec in [o] + o.get('stages', []):
                if spec.get('open_base', {}).get('modules'):
                    lv = split_zone(spec['open_base']['zone'], o['map'])[0]
                    add_module_props(zone_doc(lv), lv, spec['open_base']['modules'])
            if o.get('kind') == 'task':
                lv, zone = split_zone(o['giver'], o['map'])
                add_giver_prop(zone_doc(lv), zone)
    return files, fails, warns


def main(argv):
    paths = [a for a in argv[1:] if not a.startswith('--')]
    if len(paths) != 1:
        print(__doc__)
        return 2
    with open(paths[0], encoding='utf-8') as f:
        plan = json.load(f)
    files, fails, warns = build(plan)
    for w in warns:
        print('WARNING:', w)
    for x in fails:
        print('FAILED:', x)
    if fails:
        print('nothing written')
        return 1
    if '--apply' not in argv:
        print('would write %d file(s)' % len(files))
        return 0
    if open_programs():
        print('REFUSING: close the game and the editor, then run this again')
        return 1
    for path, doc in files.items():
        write_json(path, doc)
    print('wrote %d file(s). Pack each map, then run after_pack' % len(files))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
