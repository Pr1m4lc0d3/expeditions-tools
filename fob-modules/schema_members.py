#!/usr/bin/env python3
"""Resolve a zoneProperty's AUTHORABLE members from the engine schema.

WHY THIS EXISTS
---------------
Every custom FOB descriptor in this project so far was built by copying another
descriptor and editing it. That is how a confound got into the generator work:
`build_generator_fix.py` read the owner's 2025 file and changed 15 fields, so it
inherited 22 others unchanged. When both his pak and mine broke the base store,
"his untouched one fails too" could not isolate the property type -- a single bad
field in his original was in mine as well.

`Sources\BinEditor\meta_desc_ssl.ps` is the engine's own schema. It states, per
property type, exactly which members exist, their types, their defaults, and
which parent type it inherits. That is the authority, and it ends the copying.

    python schema_members.py ZonePropertyGenerator
    python schema_members.py ZonePropertyDroneDelivery --json

⛔ A member NOT in this list is not authorable. Several fields visible on a LIVE
object in a crash dump are runtime state (`zoneInventory`, `zoneFuel`,
`ownerPeerId`, `hasGeneratedItems`, ...) and putting them in a descriptor is
copying the engine's working memory into a config file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from saber import gamepaths  # noqa: E402

SCHEMA = os.path.join(str(gamepaths.game_dir("expeditions")), "Sources", "BinEditor", "meta_desc_ssl.ps")


def load():
    return open(SCHEMA, encoding="utf-8", errors="replace").read().split("\n")


def find_block(lines, name):
    """Return the brace-balanced block for a top-level type definition."""
    start = None
    pat = re.compile(r"^\s*" + re.escape(name) + r"\s*=\s*\{")
    for i, l in enumerate(lines):
        if pat.match(l):
            start = i
            break
    if start is None:
        return None, None
    depth = 0
    for j in range(start, min(start + 4000, len(lines))):
        depth += lines[j].count("{") - lines[j].count("}")
        if depth == 0 and j > start:
            return start, lines[start:j + 1]
    return start, lines[start:start + 4000]


def parse_members(block):
    """Members sit inside __members = { ... }, one brace-balanced entry each."""
    text = "\n".join(block)
    m = re.search(r"__members\s*=\s*\{", text)
    if not m:
        return {}, None
    i = m.end()
    depth, start = 1, i
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    body = text[start:i - 1]

    parent = None
    pm = re.search(r'__type\s*=\s*"(ZoneProperty[A-Za-z]*)"', text)
    if pm:
        parent = pm.group(1)

    out, pos = {}, 0
    while True:
        mm = re.search(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{", body[pos:], re.M)
        if not mm:
            break
        nm = mm.group(1)
        j = pos + mm.end()
        d = 1
        while j < len(body) and d:
            if body[j] == "{":
                d += 1
            elif body[j] == "}":
                d -= 1
            j += 1
        spec = body[pos + mm.start():j]
        info = {}
        t = re.search(r'__type\s*=\s*"([^"]+)"', spec)
        if t:
            info["type"] = t.group(1)
        dv = re.search(r"__defaultValue\s*=\s*([^\n]+)", spec)
        if dv:
            info["default"] = dv.group(1).strip()
        if "__nullable   =   True" in spec or re.search(r"__nullable\s*=\s*True", spec):
            info["nullable"] = True
        p = re.search(r'__ptr\s*=\s*"([^"]+)"', spec)
        if p:
            info["ptr"] = p.group(1)
        cap = re.search(r'__caption\s*=\s*"([^"]+)"', spec)
        if cap:
            info["caption"] = cap.group(1)
        out[nm] = info
        pos = j
    return out, parent


def resolve(name, lines, seen=None):
    """Walk the inheritance chain, nearest definition wins."""
    if seen is None:
        seen = []
    if name in seen:
        return {}, seen
    seen.append(name)
    _, block = find_block(lines, name)
    if block is None:
        return {}, seen
    own, parent = parse_members(block)
    inherited = {}
    if parent and parent != name:
        inherited, seen = resolve(parent, lines, seen)
    merged = dict(inherited)
    for k, v in own.items():
        v = dict(v)
        v["declaredBy"] = name
        merged[k] = v
    return merged, seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("type_name")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    lines = load()
    members, chain = resolve(a.type_name, lines)
    if not members:
        print("type %r not found in the schema" % a.type_name)
        return 2
    if a.json:
        print(json.dumps({"chain": chain, "members": members}, indent=1))
        return 0
    print("inheritance chain: " + " -> ".join(chain))
    print("authorable members: %d\n" % len(members))
    for k in sorted(members):
        v = members[k]
        bits = [v.get("type", v.get("ptr", "?"))]
        if "default" in v:
            bits.append("default=%s" % v["default"])
        if v.get("nullable"):
            bits.append("nullable")
        print("  %-36s %-34s [%s]" % (k, " ".join(bits), v.get("declaredBy", "?")))
        if v.get("caption"):
            print("      %s" % v["caption"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
