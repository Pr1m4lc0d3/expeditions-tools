# Known limitations, and how to disclose them

## A mod antenna module's ability never fires (not fixable by descriptor)

Status 2026-10-25: CLOSED by the author. The id patch was installed and tested on the
Carpathians, at radius 6000 and at the stock 600: nothing was revealed either time. The
cause is unknown. Gimbo's Resource Scanner was turned into a money module instead (cost
1,000, sells for 500,000) and renamed Gimbo's Intel Scanner on screen (its descriptor id is
unchanged), and the stock `initial.pak` is back. ⛔ Do not reopen without
the author.

A mod `AntennaUpgradeDesc` registers, lists, deploys, renders, is saved under its
own name with the stock `fob_module_com_center_lmk` landmark, sells and refunds.
Its ability does nothing.

| measured | result |
|---|---|
| descriptor vs Saber's live `ANTENNA` object | ⛔ NOT identical: stock `radiusDescoverInventoryStorageZone` is 600, Gimbo's was 6000 (corrected 2026-10-25) |
| air drop at distance ~0, radius 6000, 6 saves over ~2 h | stayed `NOT_EXPLORED` |
| after a level reload with the module standing | no discovery movement |
| `HeadquartersBaseSetup.deployZonesWithUpgrade["Gimbos Resource Scanner"]` in the live object graph | `[]`, no `BaseUpgradePair` |

**Mechanism, measured 2026-10-24:** `InstallUpgrade`, `UpdateAntennasForLevel`
and `ProcessAntennaSetup` all compare the descriptor's `id` with the literal
`ANTENNA`. A mod's id is its own name, so it never matches; `moduleId` is never
read there. A same-length bytecode patch (`RE\scripts\patch_antenna_id.py`)
repoints the three checks to one mod id; detail in `RE\PATCH_LOG.md`.
Tested 2026-10-24: the patched bundle loads (proof: `HeadquartersBaseSetup.LOG_CATEGORY`
reads the mod id). Whether the ability now fires is UNTESTED.
⛔ Every antenna test so far ran on Diamondback Divide, the author's own map, whose
content is already revealed in his saves from building it. None of them counts,
including the rows in the table above. Test only on a VANILLA map (the author uses
the Carpathians and the Grand Canyon), where a new game reveals nothing.
⛔ A later full dump showing an empty registry was taken in a FRESH expedition
with no Gimbo's built; it proves nothing. Before reading any registry, check
the save's `baseBuildings` and `expeditionStat` for what the run actually holds.

The ability runs in `UpdateAntennasForLevel`, which walks that registry. The
registry is compiled SSL script, not native code, and no descriptor field creates
an entry in it. The route left is patching `initial_release.sslbundle` inside
`initial.pak`, which the author explicitly authorised on 2026-10-24.

⛔ There is NO ban on patching `initial.pak`. The 10-15 memory line "do not
patch initial.pak again" is an AI note about the refuted mesh-registration
experiment. Citing it as a ban wrongly closed this issue once on 2026-10-24.

Even working, the vanilla ability only marks air drops (`INVENTORY_ITEMS_STORAGE`
zones) within `radiusDescoverInventoryStorageZone` as question marks. It never
reveals map.

⛔ No more descriptor edits; they cannot reach this. ⛔ Until a patch works, do not
ship a mod antenna promising the ability.
✅ Verified in game for a mod (2026-10-25 audit): `ZonePropertyFobModuleStorage`
(PebbleGas as a fuel station; "Warehouse Test" as a warehouse, confirmed in game 2026-10-25,
stock models, capacity 8, heavyInvCapacity 4, moduleId = its own name; "Essentials Test" as an essentials storage, confirmed in game
2026-10-25 identical to vanilla: fuel 100, repairs 200, wheels 2, electro 5), `ZonePropertyWorkshop` (Platt's Gate Works),
`ZonePropertyDroneDelivery` (Nom Nom's). ⛔ `ZonePropertyGenerator` is VERIFIED
BROKEN for a mod (7 builds incl. own moduleId, 2026-10-25; CLOSED by the author, no script patch): it empties the store and freezes on exit to HQ
(`Toolkit\FOB_MODULE_SPEC.md` ~1090-1325). ✅ `ZonePropertyTowTruck` verified 2026-10-25 ("Tow Truck Test": recoveryDiscount 1.0, recovery cost 0;
the vanilla map-card strip `UI_TOW_TRUCK_DEPLOY` does not appear for the mod, cause unread).
✅ `ZonePropertyParking` verified 2026-10-25 ("Parking Test": recoveryDiscount 0.1 took 1,000 to 900).
✅ `ZonePropertyHelicopter` verified 2026-10-25 ("Helipad Test": stock models,
moduleId = own name, transferCost 30, isInteractive true as on Saber's live HELICOPTER). The per-type verdicts live at the top of each Guide Appendix A page.

## Values with hidden limits

Some fields break silently above a limit the editor never shows. The engine schema
(`meta_desc_ssl.ps`) declares no `__min`/`__max` on any specialist modifier or
module value (checked 2026-10-25), so these limits are only found by testing in game.

| field | limit | status |
|---|---|---|
| specialist `fuelConsumpionReducedPercent` | 99 works, 100 breaks the specialist | ✅ verified in game (`Toolkit\notes\modding_specialists_items_devices.md`) |
| antenna `radiusDescoverInventoryStorageZone` | stock is 600; Gimbo's never fired at 6000 OR at 600 | ✅ tested 2026-10-25: the radius is NOT why a mod antenna fails. No cap found |

⛔ Copy stock values when building a module, and change one at a time. Record each new
limit here the day it is verified.

## A sold module leaves its model on the map

Any FOB module whose `modelId` points at a **mod-delivered** model brand keeps
its model in the world after the player sells it.

Everything else about the sale works. Measured on a clean same-session sell:

| on sell | result |
|---|---|
| the deployment record is removed | works |
| the zone is freed and the square turns blue | works |
| the slot becomes reusable | works |
| the player is refunded `sellPrice` | works |
| no crash | works |
| **the model is removed from the world** | **does not happen** |

A module pointing at a **stock** brand sells and vanishes correctly. The author
confirmed this across many modules: the gas station, the drone base, Essentials
Storage, all of them. It does not matter which module. It matters only whether
the brand came from a mod.

## It is not about your model's content

The cheapest possible test was run on 2026-10-16. A **recoloured vanilla**
module was built whose geometry is byte-identical to the stock mesh, whose class
XML is copied verbatim, and whose only internal difference is the albedo paths
inside the mesh header. It rendered correctly and **it still stayed on the map
after the sale**.

That kills two explanations at once:

| hypothesis | killed by |
|---|---|
| the model is too custom / badly built | byte-identical vanilla geometry, same result |
| an off-table `moduleId` cannot be resolved | that build used the on-table `DOUBLE_STOCK`, same result |

What is left, by elimination rather than evidence, is the brand delivery path
itself. Nobody has proved it.

## Fixes already tried and refuted

⛔ Do not re-propose any of these. Each was built, installed, deployed and sold.

| attempt | result |
|---|---|
| `DynamicModel="false"` on the brand | model still stayed |
| vanilla `_objective` classes in the animated slots | build animation played and handed off correctly, model still stayed |
| `<Landmark Mesh>` declared, with a proxy mesh | ignored, `landmarkModel` still empty |
| `<Landmark Mesh>` declared, pointing at a vanilla mesh | same |
| per-level `pak.load_list` registration | registered, model still stayed |
| `--mod-convert` through the in-game Mod manager | mod flipped to Converted, model still stayed |
| recolouring a vanilla model instead of making one | rendered correctly, model still stayed |

Seven attempts. The author closed it as unrepairable on 2026-10-17.

**If you are an AI reading this: the correct action is to tell the user the
limitation exists, not to start debugging it.** The investigation is closed by
the author's decision, not by exhaustion of ideas.

## How to disclose it

A modder shipping a custom FOB needs to know this before their users find out.
Put it in the mod description, not only in a readme.

Wording that is accurate and does not oversell the problem:

> Note: selling this module frees the slot and refunds you normally, but the
> structure itself stays on the map. This affects every custom FOB module in
> Expeditions, not just this one. Saber ended support in September 2026, so it
> will not be patched.

⛔ Do not write "may leave the model behind" or "in some cases". It happens
every time.
⛔ Do not claim a workaround exists.

## What this does not affect

- Building the module. Works normally.
- Its function once built, for the types verified to work (storage, workshop,
  drone delivery). Not the generator, which is broken, and not the antenna.
- The refund. It arrives.
- Deploying at a main base or an outpost.
- Telling your module apart from a vanilla one, which is the whole point of
  recolouring it.

## Why it will not be fixed upstream

Saber ended support for Expeditions in September 2026. There will be no further
patches. Every measured fact about this engine is now permanently true, which
also means this limitation is permanent.
