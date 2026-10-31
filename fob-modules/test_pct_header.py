"""Gate for saber.pct against the game's own files. Run after touching pct.py.

The reference is not a spec, it is the editor's own output: every `.pct_header`
member it wrote into a packed level, beside the `.pct` it was built from. If
`header_from_pct` reproduces all of them byte for byte, the rule is right.

A negative control is included because a plain truncation reproduces the first
62 bytes of every file and would otherwise look convincing.

    python test_pct_header.py [path\to\level.pak]
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from saber import pct  # noqa: E402

DEFAULT_PAK = (Path.home() / "Documents" / "My Games" / "Expeditions" / "Media"
               / "Mods" / "level_shafer_trail.pak")

HDR = ".pct_header"


def main(argv: list[str]) -> int:
    pak = Path(argv[1]) if len(argv) > 1 else DEFAULT_PAK
    if not pak.exists():
        print("no reference pak at %s" % pak)
        print("pass one on the command line; any packed level with terrain works")
        return 2

    z = zipfile.ZipFile(pak)
    names = z.namelist()
    stems = sorted({n[:-len(HDR)] for n in names if n.endswith(HDR)})
    if not stems:
        print("%s carries no .pct_header members -- wrong reference" % pak.name)
        return 2

    print("reference: %s" % pak.name)
    print("header members to reproduce: %d\n" % len(stems))

    failures = []
    for stem in stems:
        body = stem + ".pct"
        if body not in names:
            print("  %-54s SKIP (no .pct beside it)" % Path(stem).name)
            continue
        want = z.read(stem + HDR)
        try:
            got = pct.header_from_pct(z.read(body))
        except pct.NotPct as exc:
            failures.append((stem, "NotPct: %s" % exc))
            print("  %-54s ERROR %s" % (Path(stem).name, exc))
            continue
        if got == want:
            print("  %-54s %4d B  MATCH" % (Path(stem).name, len(want)))
        else:
            failures.append((stem, "built %d B, want %d B" % (len(got), len(want))))
            print("  %-54s %4d B  MISMATCH (built %d B)"
                  % (Path(stem).name, len(want), len(got)))

    # NEGATIVE CONTROL. Truncation shares the first 62 bytes with every real
    # header; if it ever "passes", the comparison is not actually comparing.
    print("\nnegative control -- plain truncation must fail every file:")
    fooled = []
    for stem in stems:
        if stem + ".pct" not in names:
            continue
        want = z.read(stem + HDR)
        if z.read(stem + ".pct")[:len(want)] == want:
            fooled.append(stem)
    if fooled:
        print("  %d file(s) matched a truncation -- the test is not discriminating"
              % len(fooled))
        failures.append(("negative control", "truncation matched"))
    else:
        print("  all differ, as they must")

    # Shared prefix, stated so nobody is tempted by truncation again.
    stem0 = stems[0]
    if stem0 + ".pct" in names:
        w = z.read(stem0 + HDR)
        b = z.read(stem0 + ".pct")
        common = next((i for i in range(len(w)) if w[i] != b[i]), len(w))
        print("  (header and .pct agree for the first %d of %d bytes)"
              % (common, len(w)))

    print("")
    if failures:
        print("FAIL -- %d problem(s)" % len(failures))
        for stem, why in failures:
            print("   %s: %s" % (stem, why))
        return 1
    print("PASS -- %d header(s) reproduced byte for byte" % len(stems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
