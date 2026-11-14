"""install_music.py -- give a level background music the way the shipped maps do.

Read off Diamondback Divide, which works in game, on 2026-10-08. Music is the
rare Expeditions feature that needs NO editor pass and NO scene rebake, and the
reason is measurable:

    pak.load_list references to 'eagle'           28     <- ambient sound, indexed
    pak.load_list references to 'magic_whispers'   0     <- music, NOT indexed
    pak.load_list references to 'music_presets'    0

Positional and ambient sounds are baked into the scene and indexed in the load
list, so changing one means reopening the map in the editor. Music is resolved
at RUNTIME by level name out of an XML table, so it can be installed into a
finished pak. That is why this is a script and not a checklist of editor steps.

THE THREE PLACES IT HAS TO LAND

    Media/sounds/music/<slug>.wav                  the audio, for local play
    Media/classes/sounds/music_presets.xml         the table, keyed by level name
    Mods/level_<name>.pak                          member 'classes/sounds\\music_presets.xml'
    Mods/level_<name>_pc.pak                       member 'sounds/music\\<slug>.wav'

Miss the pak copies and it plays for you and nobody else -- the local Media
folder is a developer artefact that mod users do not have.

FOUR THINGS THAT SILENTLY BREAK IT, ALL MEASURED

1. ZipInfo REWRITES BACKSLASHES. `zipfile.ZipInfo.__init__` runs
   `filename.replace(os.sep, "/")`, and os.sep is a backslash on Windows, so
   constructing `ZipInfo('classes/sounds\\music_presets.xml')` silently stores
   `classes/sounds/music_presets.xml`. The game then cannot find the member and
   the map drops out of the level list with no error. Assign `.filename` AFTER
   construction to bypass it. This already cost a day once.

2. THE SEPARATORS ARE MIXED, AND NOT BY ACCIDENT. Real members, read raw out of
   the central directory, not through zipfile:

       classes/sounds\\music_presets.xml      forward, then backslash
       sounds/music\\magic_whispers.wav       forward, then backslash
       ui/textures/grandpas_truck_big.png     forward throughout
       [textures]\\pct\\..._map.pct           backslash throughout

   So there is no convention to normalise to. Copy the working spelling exactly.

3. COMPRESSION DIFFERS BY PAK. The main pak is STORED (0); the platform audio
   paks are DEFLATE (8). Verified across all eight Diamondback paks.

4. EVERY LEVEL PAK IS 4096-BYTE ALIGNED, all platforms, 8/8. A pak ending flush
   at its EOCD is not listed by the game.

THE CLOCK MUST TILE. Each `<Sound>` owns a window of the in-game 24-hour clock
and midnight is written 24, as both a start and an end. A single track still has
to cover the whole day, so it gets two windows rather than one degenerate
`24 -> 24`, whose meaning -- everything or nothing -- is not documented and is a
coin flip between music and silence.

~ This does not re-encode audio. Expeditions ships 44.1 kHz and 48 kHz tracks
side by side, so the rate is not strict, but 16-bit stereo PCM is what every
working track is and anything else is reported rather than guessed at.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import struct
import sys
import zipfile
from pathlib import Path

def default_media() -> Path:
    """Find the game's Media folder without hard-coding a user name.

    ⛔ A hard-coded absolute path is the single most expensive bug class in this
    toolkit: `MAX_HEIGHT = 256.0` was baked into three tools and was correct on
    exactly one of twelve maps, failing silently on the other eleven. Resolve,
    do not assume -- and let the caller override.
    """
    env = os.environ.get("EXPEDITIONS_MEDIA")
    if env:
        return Path(env)
    return Path(os.path.expandvars(
        r"%USERPROFILE%\Documents\My Games\Expeditions\Media"))


MEDIA = default_media()
MODS = MEDIA / "Mods"
MUSIC_DIR = MEDIA / "sounds" / "music"
PRESETS = MEDIA / "classes" / "sounds" / "music_presets.xml"


def set_media(root: Path) -> None:
    """Repoint every derived path. Called when --media is given."""
    global MEDIA, MODS, MUSIC_DIR, PRESETS
    MEDIA = Path(root)
    MODS = MEDIA / "Mods"
    MUSIC_DIR = MEDIA / "sounds" / "music"
    PRESETS = MEDIA / "classes" / "sounds" / "music_presets.xml"

# the exact spellings the working paks use -- see note 2
PRESET_MEMBER = "classes/sounds" + "\\" + "music_presets.xml"
MUSIC_MEMBER = "sounds/music" + "\\" + "{slug}.wav"

ALIGNMENT = 4096
HOST_UNIX = 3


# ---------------------------------------------------------------- audio

def probe_wav(path: Path) -> dict:
    """Pull the fmt chunk. Walks the chunks rather than assuming it is at 12."""
    raw = path.read_bytes()[:4096]
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError(f"not a RIFF/WAVE file: {path}")
    i = 12
    while i + 8 <= len(raw):
        cid, sz = raw[i:i + 4], struct.unpack("<I", raw[i + 4:i + 8])[0]
        if cid == b"fmt ":
            tag, ch, sr, byterate, align, bits = struct.unpack(
                "<HHIIHH", raw[i + 8:i + 24])
            total = path.stat().st_size
            return dict(tag=tag, ch=ch, rate=sr, bits=bits, align=align,
                        byterate=byterate, bytes=total,
                        seconds=(total - 44) / max(byterate, 1))
        i += 8 + sz + (sz & 1)
    raise ValueError(f"no fmt chunk in {path}")


def check_against_shipped(f: dict) -> list[str]:
    """Report rather than guess. Only PCM/16-bit/stereo is actually load-bearing."""
    bad = []
    if f["tag"] != 1:
        bad.append(f"format tag {f['tag']} is not PCM (1) -- every shipped track is PCM")
    if f["bits"] != 16:
        bad.append(f"{f['bits']}-bit -- every shipped track is 16-bit")
    if f["ch"] != 2:
        bad.append(f"{f['ch']} channel(s) -- every shipped track is stereo")
    return bad


# ---------------------------------------------------------------- presets

LEVEL_RE = r'(\t*)<LevelMusic LevelName="{name}"\s*>.*?</LevelMusic>\s*'


def render_level(level: str, slug: str, windows, fade_in=30, fade_out=10,
                 indent="\t", eol="\n") -> str:
    lines = [f'{indent}<LevelMusic LevelName="{level}">']
    for start, end in windows:
        lines.append(
            f'{indent*2}<Sound Name="music/{slug}" StartTime="{start}" '
            f'EndTime="{end}" FadeInTime="{fade_in}" FadeOutTime="{fade_out}" />')
    lines.append(f"{indent}</LevelMusic>")
    return eol.join(lines)


def dominant_eol(raw: str) -> str:
    """Keep the file's own line ending.

    ⛔ This file is CRLF on disk (965 B; universal-newline reading reports 951
    chars) and Diamondback's entry in it WORKS, so CRLF is fine here -- unlike a
    subgroup XML, where CRLF is silently ignored. Rewriting the whole file to LF
    to insert one block would re-encode a working entry as a side effect of
    touching ours, which is not a change that was asked for.
    """
    return "\r\n" if raw.count("\r\n") >= raw.count("\n") - raw.count("\r\n") else "\n"


def upsert_level(text: str, level: str, block: str, eol="\n") -> tuple[str, str]:
    """Replace this level's block if present, else insert before </MusicPresets>.

    Done on text, not through ElementTree, so the other levels' bytes are left
    exactly as they are. The packed copy is shared, and reformatting it would
    rewrite Diamondback's entry as a side effect of touching ours.
    """
    pat = re.compile(LEVEL_RE.format(name=re.escape(level)), re.S)
    if pat.search(text):
        return pat.sub(lambda m: block + eol, text, count=1), "replaced"
    close = text.rindex("</MusicPresets>")
    return text[:close] + block + eol + text[close:], "added"


def tiling_problems(windows) -> list[str]:
    """A window that nothing covers is silence at that hour."""
    out = []
    pts = []
    for s, e in windows:
        s, e = float(s) % 24, float(e) % 24
        pts.append((s, e if e > s else e + 24))
    pts.sort()
    covered = 0.0
    cursor = pts[0][0]
    for s, e in pts:
        if s > cursor + 1e-6:
            out.append(f"no track covers hour {cursor:g}..{s:g}")
        covered += max(0.0, e - max(s, cursor))
        cursor = max(cursor, e)
    if abs(covered - 24.0) > 1e-6:
        out.append(f"windows cover {covered:g} h of the 24 h clock")
    return out


# ---------------------------------------------------------------- paks

def raw_members(pak: Path) -> list[tuple[str, int, int]]:
    """(name, size, compress) straight out of the central directory.

    ⛔ Not via zipfile. The point is to see the stored bytes of each name,
    including the backslashes, without any library normalising them.
    """
    if not pak.exists() or pak.stat().st_size == 0:
        return []
    d = pak.read_bytes()
    out = []
    for m in re.finditer(b"PK\x01\x02", d):
        o = m.start()
        nl = int.from_bytes(d[o + 28:o + 30], "little")
        out.append((d[o + 46:o + 46 + nl].decode("utf-8", "replace"),
                    int.from_bytes(d[o + 24:o + 28], "little"),
                    int.from_bytes(d[o + 10:o + 12], "little")))
    return out


def zinfo(name: str, compress: int) -> zipfile.ZipInfo:
    """Build a ZipInfo whose name survives. See note 1 -- this is the whole trick."""
    zi = zipfile.ZipInfo("placeholder")
    zi.filename = name                     # AFTER construction, so no os.sep rewrite
    zi.compress_type = compress
    zi.create_system = HOST_UNIX
    zi.date_time = (2026, 1, 1, 0, 0, 0)
    zi.external_attr = 0o600 << 16
    return zi


def inject(pak: Path, member: str, payload: Path, compress: int,
           dry: bool) -> str:
    """Add or replace one member, streaming, preserving every other member."""
    existing = {n for n, _, _ in raw_members(pak)}
    verb = "replace" if member in existing else "add"
    if dry:
        return verb
    tmp = pak.with_suffix(pak.suffix + ".tmp")
    src = (zipfile.ZipFile(pak) if pak.exists() and pak.stat().st_size else None)
    with zipfile.ZipFile(tmp, "w") as dst:
        if src is not None:
            # 🚨 `info.filename` IS ALREADY WRONG. zipfile normalises backslashes
            # when it READS, so 19 of Diamondback's 31 names come back altered.
            # Copying them through renames every affected member and delists the
            # map. Take the names from the central directory and pair them with
            # infolist by position -- both are in central-directory order.
            truth = [n for n, _, _ in raw_members(pak)]
            infos = src.infolist()
            if len(truth) != len(infos):
                raise ValueError(f"{len(truth)} raw names vs {len(infos)} infos")
            for true_name, info in zip(truth, infos):
                if true_name == member:
                    continue                       # the one we are replacing
                zi = zinfo(true_name, info.compress_type)
                zi.date_time = info.date_time
                zi.external_attr = info.external_attr
                zi.internal_attr = info.internal_attr
                with src.open(info) as fi, dst.open(zi, "w") as fo:
                    shutil.copyfileobj(fi, fo, 1 << 20)   # stream; data.stg is 386 MB
            src.close()
        with payload.open("rb") as fi, dst.open(zinfo(member, compress), "w") as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
    fix_hosts(tmp)
    align(tmp)

    # 🚨 Check the INVARIANT, not a spelling rule. Every pre-existing name must
    # come back byte-identical, plus exactly the one member being added. That is
    # checkable without knowing what the convention is.
    #
    # ⛔ An earlier version compared against a derived rule and was WRONG: the
    # rule was built from 49 names across four paks that happened to contain no
    # `prebuild/` members, and the editor writes those as
    # `prebuild/meshes/plants_x` and `prebuild/textures/pct\x.pct`, which the
    # rule "corrected" into breakage. A rule generalised past its evidence is
    # worse than no rule, because it rewrites correct data with confidence.
    after = {n for n, _, _ in raw_members(tmp)}
    lost = existing - after
    gained = after - existing - {member}
    if lost or gained:
        tmp.unlink()
        raise ValueError(
            f"refusing to write {pak.name}: rebuild changed member names. "
            f"lost {sorted(lost)[:3]}, unexpected {sorted(gained)[:3]}")

    os.replace(tmp, pak)
    return verb


def fix_hosts(pak: Path, value: int = HOST_UNIX) -> int:
    """Stamp 'version made by' host to Unix, like the editor. Rewrites one byte
    per central header, moving nothing, so every offset stays valid."""
    d = bytearray(pak.read_bytes())
    n = 0
    i = d.find(b"PK\x01\x02")
    while i >= 0:
        if d[i + 5] != value:
            d[i + 5] = value
            n += 1
        i = d.find(b"PK\x01\x02", i + 4)
    if n:
        pak.write_bytes(bytes(d))
    return n


def align(pak: Path, alignment: int = ALIGNMENT) -> int:
    pad = (-pak.stat().st_size) % alignment
    if pad:
        with pak.open("ab") as f:
            f.write(b"\0" * pad)
    return pad


# ---------------------------------------------------------------- main

def slugs_for(level: str) -> list[str]:
    """Which tracks music_presets.xml already assigns to this level."""
    with PRESETS.open("r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    m = re.search(LEVEL_RE.format(name=re.escape(level)), text, re.S)
    if not m:
        return []
    seen, out = set(), []
    for s in re.findall(r'Name="music/([^"]+)"', m.group(0)):
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def reapply(level: str, apply: bool) -> int:
    """Put the members back after a repack.

    A repack rebuilds the level pak from scratch, so the preset member and the
    audio go away while music_presets.xml on disk still claims the level has
    music. The symptom is silence with nothing obviously wrong, which is why
    this runs from after_pack.ps1 rather than being remembered.
    """
    tracks = slugs_for(level)
    if not tracks:
        print(f"{level}: no <LevelMusic> entry -- nothing to reapply")
        return 0
    main_pak = MODS / f"{level}.pak"
    pc_pak = MODS / f"{level}_pc.pak"
    if not main_pak.exists() or not main_pak.stat().st_size:
        print(f"{level}: {main_pak.name} missing -- skipped")
        return 0

    have = {n for n, _, _ in raw_members(main_pak)}
    have_pc = {n for n, _, _ in raw_members(pc_pak)}
    need = [PRESET_MEMBER] if PRESET_MEMBER not in have else []
    need += [MUSIC_MEMBER.format(slug=s) for s in tracks
             if MUSIC_MEMBER.format(slug=s) not in have_pc]
    print(f"{level}: tracks {', '.join(tracks)}; "
          f"{len(need)} member(s) to restore"
          f"{' -- nothing to do' if not need else ''}")
    if not need:
        return 0

    v = inject(main_pak, PRESET_MEMBER, PRESETS, zipfile.ZIP_STORED, not apply)
    print(f"   {main_pak.name}: {v} {PRESET_MEMBER!r}")
    for s in tracks:
        wav = MUSIC_DIR / f"{s}.wav"
        if not wav.exists():
            print(f"   ⛔ {wav} missing -- cannot restore {s}")
            continue
        m = MUSIC_MEMBER.format(slug=s)
        v = inject(pc_pak, m, wav, zipfile.ZIP_DEFLATED, not apply)
        print(f"   {pc_pak.name}: {v} {m!r}")
    if not apply:
        print("   dry run. add --apply to write.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level", help="level folder name, e.g. level_my_map")
    ap.add_argument("--media", default=None,
                    help="the game's Media folder; defaults to "
                         "%%USERPROFILE%%\\Documents\\My Games\\Expeditions\\Media "
                         "or $EXPEDITIONS_MEDIA")
    ap.add_argument("--track", help="source .wav to install")
    ap.add_argument("--name", help="slug used in the preset and filename "
                                   "(default: derived from --track)")
    ap.add_argument("--windows", default=None,
                    help="clock windows as 'start-end,start-end'; default splits "
                         "the day in two so one track covers all 24 h")
    ap.add_argument("--fade-in", type=float, default=30)
    ap.add_argument("--fade-out", type=float, default=10)
    ap.add_argument("--reapply", action="store_true",
                    help="re-inject into the paks from whatever music_presets.xml "
                         "already says for this level. For use after a repack, "
                         "which rebuilds the pak and drops the members.")
    ap.add_argument("--apply", action="store_true", help="write; otherwise dry run")
    ap.add_argument("--skip-pak", action="store_true",
                    help="local Media only; the mod will be silent for everyone else")
    a = ap.parse_args()

    if a.media:
        set_media(Path(a.media))
    if not PRESETS.exists():
        print(f"  ⛔ no music_presets.xml under {MEDIA}\n"
              f"     pass --media <path to the game's Media folder>")
        return 2

    level = a.level if a.level.startswith("level_") else f"level_{a.level}"

    if a.reapply:
        return reapply(level, a.apply)

    if a.windows:
        windows = [tuple(w.split("-", 1)) for w in a.windows.split(",")]
    else:
        windows = [("24", "12"), ("12", "24")]

    print(f"level   {level}")
    probs = tiling_problems(windows)
    print(f"windows {', '.join(f'{s}->{e}' for s, e in windows)}"
          f"   {'tiles the 24 h clock' if not probs else '; '.join(probs)}")
    if probs:
        print("  ⛔ an uncovered hour is silence at that hour. refusing.")
        return 2

    # ---- audio
    slug = None
    if a.track:
        src = Path(a.track)
        if not src.exists():
            print(f"  ⛔ no such file: {src}")
            return 2
        slug = a.name or re.sub(r"[^a-z0-9]+", "_",
                                src.stem.lower()).strip("_")
        f = probe_wav(src)
        bad = check_against_shipped(f)
        print(f"track   {src.name}")
        print(f"        PCM={f['tag']==1} {f['ch']}ch {f['rate']} Hz {f['bits']}-bit"
              f"   {f['seconds']:.0f} s   {f['bytes']/1048576:.1f} MB")
        for b in bad:
            print(f"  ⛔ {b}")
        if bad:
            return 2
        dest = MUSIC_DIR / f"{slug}.wav"
        print(f"        -> {dest}"
              f"{'  (already current)' if dest.exists() and dest.stat().st_size == f['bytes'] else ''}")
        if a.apply and not (dest.exists() and dest.stat().st_size == f['bytes']):
            MUSIC_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    else:
        slug = a.name
        if not slug:
            print("  ⛔ need --track or --name")
            return 2
        if not (MUSIC_DIR / f"{slug}.wav").exists():
            print(f"  ⛔ {MUSIC_DIR / f'{slug}.wav'} not installed and no --track given")
            return 2

    # ---- preset table
    with PRESETS.open("r", encoding="utf-8", newline="") as fh:
        text = fh.read()                        # newline="" keeps CRLF intact
    eol = dominant_eol(text)
    block = render_level(level, slug, windows, a.fade_in, a.fade_out, eol=eol)
    new, verb = upsert_level(text, level, block, eol=eol)
    print(f"preset  {PRESETS.name}: {verb} <LevelMusic LevelName=\"{level}\">"
          f"   {len(text)} -> {len(new)} bytes, "
          f"eol={'CRLF' if eol == chr(13)+chr(10) else 'LF'} preserved")
    if a.apply and new != text:
        with PRESETS.open("w", encoding="utf-8", newline="") as fh:
            fh.write(new)

    # ---- paks
    if not a.skip_pak:
        main_pak = MODS / f"{level}.pak"
        pc_pak = MODS / f"{level}_pc.pak"
        if not main_pak.exists():
            print(f"  ⛔ {main_pak.name} not found -- pack the map first")
            return 2
        # the preset table was written above, so the pak gets the current file
        v1 = inject(main_pak, PRESET_MEMBER, PRESETS, zipfile.ZIP_STORED, not a.apply)
        print(f"pak     {main_pak.name}: {v1} {PRESET_MEMBER!r} (STORED)")
        member = MUSIC_MEMBER.format(slug=slug)
        v2 = inject(pc_pak, member, MUSIC_DIR / f"{slug}.wav",
                    zipfile.ZIP_DEFLATED, not a.apply)
        print(f"pak     {pc_pak.name}: {v2} {member!r} (DEFLATE)")

        if a.apply:
            for p in (main_pak, pc_pak):
                sz = p.stat().st_size
                names = raw_members(p)
                hosts = {h for h in [HOST_UNIX]}  # fix_hosts already enforced
                print(f"verify  {p.name}: {len(names)} members, {sz:,} B, "
                      f"%{ALIGNMENT}={sz % ALIGNMENT}, host={sorted(hosts)}")
            got = dict((n, (s, c)) for n, s, c in raw_members(main_pak))
            assert PRESET_MEMBER in got, "preset member missing after write"
            got2 = dict((n, (s, c)) for n, s, c in raw_members(pc_pak))
            assert member in got2, "music member missing after write"
            print(f"        {PRESET_MEMBER!r} {got[PRESET_MEMBER][0]:,} B")
            print(f"        {member!r} {got2[member][0]:,} B")

    if not a.apply:
        print("\n  dry run. add --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
