"""install_model_to_mod.py -- put a converted model into a FOB mod, with its
runtime textures, and REFUSE to finish if any of them is missing.

The map names are read out of the mesh itself rather than assumed. Doc Windsor
moved from `<name>_material_0__d` to `<name>_bakedmaterial__d` when it went
through remesh instead of decimation, and a hard-coded name list would have
shipped a model whose textures do not exist -- which the runtime renders as
flat red.

    python install_model_to_mod.py <mod folder> <model> [<model> ...]

⛔ The runtime reads `.pct`, never `.tga`. ResourceConverter's ordinary pass
emits DDS only, so each map is published explicitly and the run is gated on the
`.pct` AND its `.pct_header` existing -- then the headers are DELETED from the
mod, because a mod that ships them draws only its smallest mips (see main()).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from saber import convert as C  # noqa: E402
import check_backfacing  # noqa: E402

MEDIA = Path(os.path.expanduser('~')) / 'Documents' / 'My Games' / 'Expeditions' / 'Media'


def declared_maps(mesh: Path) -> list[str]:
    """The texture stems this mesh actually asks for."""
    d = mesh.read_bytes()
    n = struct.unpack_from('<I', d, 0)[0]
    xml = d[4:4 + n].decode('utf-8', 'replace')
    out = []
    for _, v in re.findall(r'(\w*Map)="([^"]*)"', xml):
        stem = os.path.basename(v.replace(chr(92), '/'))
        if stem.lower().endswith('.tga'):
            stem = stem[:-4]
        if stem not in out:
            out.append(stem)
    return out


def dims(mesh: Path):
    d = mesh.read_bytes()
    n = struct.unpack_from('<I', d, 0)[0]
    g = d.find(struct.pack('<I', 0x0133ef63), 4 + n)
    bb = struct.unpack_from('<I' + 'f' * 6, d, g + 4)[1:]
    return tuple(round(bb[i + 3] - bb[i], 3) for i in range(3))



def prune_stale_maps(mod: Path, models: list[str], keep: list[str]) -> list[Path]:
    """Delete this mod's maps for these brands that the current build no longer uses.

    A rebuild can rename its maps (a remesh build ships <brand>_bakedmaterial__n_d,
    a rebake build <brand>_baked__n_d), and the old files stayed in the mod and
    were packed with it (Tuck's Rover Works, 2026-10-25). Matches `<brand>_` exactly,
    so tuckyard never touches tuckyardrb.
    """
    gone = []
    for m in models:
        for d, pre, ext in ((mod / 'textures' / 'models', f'{m}_', '.tga'),
                            (mod / 'prebuild' / 'textures' / 'pct', f'models_{m}_', '.pct'),
                            (mod / 'prebuild' / 'textures' / 'nx', f'models_{m}_', '.pct')):
            if not d.is_dir():
                continue
            for f in d.iterdir():
                stem = f.name[len('models_'):] if f.name.startswith('models_') else f.name
                stem = stem.split('.')[0]
                if f.name.startswith(pre) and stem not in keep:
                    f.unlink(); gone.append(f)
    return gone

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mod', type=Path)
    ap.add_argument('models', nargs='+')
    a = ap.parse_args()
    mod = a.mod
    if not mod.is_dir():
        print(f'  !! no such mod folder: {mod}')
        return 2

    all_stems: list[str] = []
    for m in a.models:
        mesh = MEDIA / 'prebuild' / 'meshes' / f'models_{m}'
        if not mesh.is_file():
            print(f'  !! {m}: not converted ({mesh} missing)')
            return 1
        # ⛔ No model enters a mod without passing the back-face check on this exact
        # FBX. Faces seen from behind were Doc's and Platt's "scrambled texture";
        # every other check passed them. There is no override.
        ok, why = check_backfacing.passed(m, MEDIA / 'meshes' / 'models' / f'{m}.fbx')
        if not ok:
            print(f'  !! {m}: {why}')
            print('  refusing: it would show the player faces from behind')
            return 1
        print(f'  {m}  {why}')
        stems = declared_maps(mesh)
        print(f'  {m}  dims {dims(mesh)}')
        for s in stems:
            print(f'      needs {s}')
        all_stems += [s for s in stems if s not in all_stems]

        pairs = [
            (mesh, mod / 'prebuild' / 'meshes' / f'models_{m}'),
            (mesh, mod / '[meshes]' / f'models_{m}'),
            (MEDIA / 'classes' / 'models' / f'{m}.xml',
             mod / 'classes' / 'models' / f'{m}.xml'),
            (MEDIA / 'classes' / 'models' / f'{m}.xml',
             mod / '[media]' / 'classes' / 'models' / f'{m}.xml'),
        ]
        for s in stems:
            pairs.append((MEDIA / 'textures' / 'models' / f'{s}.tga',
                          mod / 'textures' / 'models' / f'{s}.tga'))
        for src, dst in pairs:
            if not src.is_file():
                print(f'      !! missing source {src}')
                return 1
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)

    print()
    for s in all_stems:
        r = C.convert_texture(mod / 'textures' / 'models' / f'{s}.tga', mod)
        print(f'  pct {s:46} ok={r["ok"]}  {r.get("pct", 0):,} B')
        if not r['ok']:
            print('     ' + (r.get('log') or '')[-300:])

    ok, msg = C.textures_published(mod, all_stems)
    print(f'\n  GATE  {ok}  {msg}')
    if not ok:
        print('  refusing: the runtime reads .pct and a missing one renders flat red')
        return 1
    # ⛔ A MOD MUST NOT SHIP .pct_header. Confirmed in game 2026-10-24: with headers
    # beside its .pct files, Doc Windsor's texture drew only its smallest mips
    # (a 32-64 px blur) on every build since this tool started publishing them;
    # removing them made it sharp. PebbleGas and Gimbo's never had any. The
    # converter still writes them, so they are gated on above and removed here.
    gone = 0
    for s in all_stems:
        for sub in ('pct', 'nx'):
            h = mod / 'prebuild' / 'textures' / sub / f'models_{s}.pct_header'
            if h.is_file():
                h.unlink(); gone += 1
    print(f'  removed {gone} .pct_header file(s): a mod ships .pct only')
    stale = prune_stale_maps(mod, a.models, all_stems)
    if stale:
        print(f'  removed {len(stale)} map(s) an earlier build of these models used: '
              + ', '.join(sorted({f.name for f in stale})))
    print(f'\n  pack next, then append these to _pc.pak:')
    for s in all_stems:
        print(f'     prebuild/textures/pct/models_{s}.pct')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
