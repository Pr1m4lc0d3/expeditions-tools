"""shape_river.py -- give a river's surface profile a riffle-pool sequence.

Rewrites only the Y of each `<Node Position="(x; y; z)">` in a River subgroup.
X, Z, Width, every attribute and the file's formatting are left byte-identical,
because the plan geometry is the author's and only the long profile is being
corrected.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

from saber import river
from saber.river import MEDIA, classify

NODE = re.compile(r'(Position="\()([-\d.eE]+)(;\s*)([-\d.eE]+)(;\s*)([-\d.eE]+)(\))')


def rewrite(path: Path, new_ys, backup_dir: Path, apply=False):
    text = path.read_text(encoding="utf8")
    it = iter(new_ys)
    n = 0

    def sub(m):
        nonlocal n
        try:
            y = next(it)
        except StopIteration:
            return m.group(0)
        n += 1
        return f"{m.group(1)}{m.group(2)}{m.group(3)}{y:.5f}{m.group(5)}{m.group(6)}{m.group(7)}"

    out = NODE.sub(sub, text)
    if apply:
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_dir / (path.name + ".pre-shape-" +
                                         time.strftime("%Y%m%d-%H%M%S")))
        path.write_text(out, encoding="utf8")
    return n, out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level")
    ap.add_argument("--amplitude", type=float, default=0.10,
                    help="metres, hard cap on how far the surface may move")
    ap.add_argument("--widths", type=float, default=6.0,
                    help="riffle spacing in channel widths (natural range 5-7)")
    ap.add_argument("--min-length", type=float, default=200.0,
                    help="skip reaches shorter than this; a pond has no riffles")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    prebuild = MEDIA / "prebuild" / args.level
    bdir = Path(__file__).parent / "backups" / args.level / "subgroups"

    for path in sorted((prebuild / "subgroups").glob("River_*.xml")):
        grp = river.load_group(path)
        if not grp.reaches:
            continue
        all_ys, skip = [], False
        print(f"\n{path.name}  ({grp.name})")
        for i, rc in enumerate(grp.reaches):
            if rc.length < args.min_length:
                print(f"   [{i}] {rc.length:.0f} m  -- still water, left alone")
                all_ys += [n.y for n in rc.nodes]
                continue
            before = {}
            for *_, g in rc.segments():
                before[classify(g)] = before.get(classify(g), 0) + 1
            ys, rep = river.riffle_pool(rc, args.amplitude, args.widths)
            all_ys += ys

            # reclassify with the new profile
            import copy
            rc2 = copy.deepcopy(rc)
            for n, y in zip(rc2.nodes, ys):
                n.y = y
            after = {}
            for *_, g in rc2.segments():
                after[classify(g)] = after.get(classify(g), 0) + 1

            print(f"   [{i}] {rc.length:.0f} m, {len(rc.nodes)} nodes   "
                  f"riffle spacing {rep['wavelength']:.0f} m")
            print(f"        surface moved: max {rep['max_deviation']*100:.1f} cm, "
                  f"mean {rep['mean_deviation']*100:.1f} cm   "
                  f"({rep['changed']} of {len(ys)} nodes)")
            print(f"        total drop {rep['drop_before']:.3f} m -> "
                  f"{rep['drop_after']:.3f} m   uphill fixes: {rep['monotonic_fixes']}")
            print(f"        reaches before: {before}")
            print(f"        reaches after : {after}")
            if rep["max_deviation"] > args.amplitude + 1e-6:
                print("        !! exceeded the cap, refusing"); skip = True

        if skip:
            print("   skipped"); continue
        n, _ = rewrite(path, all_ys, bdir, apply=args.apply)
        print(f"   {'rewrote' if args.apply else 'would rewrite'} {n} node heights")

    if not args.apply:
        print("\n(dry run -- pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
