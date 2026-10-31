"""gamepaths.py -- find the game, its Media folder and its tools, on ANY machine.

These tools target Saber's map editor, which ships with both **SnowRunner** and
**Expeditions: A MudRunner Game**. The two use the same editor lineage and the
same on-disk shape, so one resolver serves both:

    <Documents>/My Games/<Game>/Media/prebuild/   the map projects
    <Documents>/My Games/<Game>/Media/levels/     packed output
    <Documents>/My Games/<Game>/Media/classes/    class XML

NOTHING HERE MAY BE HARDCODED TO ONE MACHINE OR ONE PERSON. This module exists
because the package previously carried

    MEDIA = Path(r"C:/Users/Daladim/Documents/My Games/Expeditions/Media")

an absolute path with a username in it, in the module every other tool imports.
That works on exactly one computer.

RESOLUTION ORDER, most explicit first
-------------------------------------
    1. an explicit argument
    2. $SABER_GAME / $SABER_MEDIA / $SABER_GAME_DIR
    3. toolkit.json beside the toolkit
    4. autodetect: whichever games are actually installed

If both games are present and nothing says which, that is ambiguous and it
raises rather than guessing -- picking one silently is how a tool writes a
SnowRunner asset into an Expeditions map.

    python -m expeditions.gamepaths          show what resolves on this machine
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "toolkit.json"


@dataclass(frozen=True)
class Game:
    key: str
    title: str
    my_games: str                       # folder under Documents/My Games
    exe_names: tuple[str, ...]          # to recognise an install directory
    editor_subdir: str = "Sources/BinEditor"
    bin_subdir: str = "Sources/Bin"
    install_hints: tuple[str, ...] = field(default_factory=tuple)


GAMES: dict[str, Game] = {
    "expeditions": Game(
        key="expeditions",
        title="Expeditions: A MudRunner Game",
        my_games="Expeditions",
        exe_names=("ExpeditionsAMudRunnerGame.exe", "Expeditions.exe"),
        install_hints=(
            r"Steam/steamapps/common/Expeditions A MudRunner Game",
            r"Epic Games/ExpeditionsAMudRunnerGame",
            r"Games/ExpeditionsAMudRunnerGame",
        ),
    ),
    "snowrunner": Game(
        key="snowrunner",
        title="SnowRunner",
        my_games="SnowRunner",
        exe_names=("SnowRunner.exe",),
        install_hints=(
            r"Steam/steamapps/common/SnowRunner",
            r"Epic Games/SnowRunner",
            r"Games/SnowRunner",
        ),
    ),
}


def _documents() -> Path:
    """Documents, honouring a redirected/OneDrive location when present."""
    for env in ("SABER_DOCUMENTS", "USERPROFILE", "HOME"):
        base = os.environ.get(env)
        if not base:
            continue
        if env == "SABER_DOCUMENTS":
            return Path(base)
        for cand in (Path(base) / "Documents", Path(base) / "OneDrive" / "Documents"):
            if (cand / "My Games").is_dir():
                return cand
    return Path.home() / "Documents"


def config() -> dict:
    p = Path(__file__).resolve().parent.parent / CONFIG_NAME
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf8"))
        except Exception:                                       # noqa: BLE001
            pass
    return {}


def asset_store() -> Path:
    """Where BULK DOWNLOADS go. Never beside the toolkit.

    ⛔ THE TOOLKIT DRIVE IS NOT A DOWNLOAD CACHE.

    `F:` here is a 117 GB removable card that holds the toolkit source, and the
    download tools defaulted to `models/` beside this file. One afternoon of
    model fetching put 0.82 GB there with the drive already at 0.5% free, on top
    of 15.47 GB the toolkit had accumulated across sessions. His words: "The F
    drive is just a small drive used for projects with you. It's not your
    be-all, end-all storage device for every bit of shit that you want to
    download off of the internet."

    Resolution order, so this stays correct on a machine that is not his:
        1. $SABER_ASSET_STORE
        2. "asset_store" in toolkit.json
        3. `<Media>/../_assets`, beside the game's own data
    """
    if os.environ.get("SABER_ASSET_STORE"):
        return Path(os.environ["SABER_ASSET_STORE"])
    cfg = config().get("asset_store")
    if cfg:
        return Path(cfg)
    return media_root().parent / "_assets"


def blender() -> Path:
    """blender.exe: $BLENDER, then "blender" in toolkit.json, then the newest
    Blender Foundation install under Program Files, then whatever is on PATH."""
    import shutil
    for cand in (os.environ.get("BLENDER"), config().get("blender")):
        if cand and Path(cand).is_file():
            return Path(cand)
    found = []
    for root in (os.environ.get("ProgramFiles", "C:/Program Files"),
                 os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")):
        found += Path(root, "Blender Foundation").glob("Blender */blender.exe")
    if found:
        def version(p: Path) -> tuple:
            return tuple(int(x) for x in re.findall(r"\d+", p.parent.name))
        return max(found, key=version)
    on_path = shutil.which("blender")
    if on_path:
        return Path(on_path)
    raise SystemExit("Blender not found. Install it, set BLENDER, or add \"blender\" to toolkit.json.")


def meshy_store() -> Path:
    """Where Meshy downloads go: $MESHY_STORE, "meshy_store" in toolkit.json,
    else <asset store>/meshy."""
    if os.environ.get("MESHY_STORE"):
        return Path(os.environ["MESHY_STORE"])
    cfg = config().get("meshy_store")
    return Path(cfg) if cfg else asset_store() / "meshy"


def media_root(game: str | None = None) -> Path:
    """The Media folder for the chosen game."""
    if os.environ.get("SABER_MEDIA"):
        return Path(os.environ["SABER_MEDIA"])
    cfg = config()
    if not game:
        game = os.environ.get("SABER_GAME") or cfg.get("game")
    if not game:
        found = installed_games()
        if len(found) == 1:
            game = found[0]
        elif len(found) > 1:
            # Tie-break on which one is actually being modded: a game with map
            # projects under Media/prebuild is the one in use. This is a
            # reasoned choice rather than a coin flip, and it announces itself
            # -- silently picking is how an asset lands in the wrong game.
            docs = _documents() / "My Games"
            withwork = [k for k in found
                        if any((docs / GAMES[k].my_games / "Media" / "prebuild")
                               .glob("*")) ]
            if len(withwork) == 1:
                game = withwork[0]
                import sys as _sys
                print(f"[gamepaths] both games present; using '{game}' because it "
                      f"has map projects. Set SABER_GAME to override.",
                      file=_sys.stderr)
            else:
                raise RuntimeError(
                    "Both games are installed and nothing says which to use. "
                    "Set SABER_GAME=expeditions|snowrunner, pass game=..., or "
                    f'put {{"game": "expeditions"}} in {CONFIG_NAME}. '
                    "Guessing would write one game's asset into the other's map.")
        else:
            raise RuntimeError(
                "No supported game found under Documents/My Games. "
                "Set SABER_MEDIA to the Media folder directly.")
    if cfg.get("media"):
        return Path(cfg["media"])
    g = GAMES[game.lower()]
    return _documents() / "My Games" / g.my_games / "Media"


def installed_games() -> list[str]:
    """Games whose Media folder exists for this user."""
    docs = _documents() / "My Games"
    return [k for k, g in GAMES.items() if (docs / g.my_games / "Media").is_dir()]


def game_dir(game: str | None = None) -> Path | None:
    """The game's INSTALL directory, which holds the editor and the paks.

    Unlike Media this is not in a predictable place -- Steam, Epic, or any
    drive -- so an explicit setting wins and autodetection is a courtesy.
    """
    if os.environ.get("SABER_GAME_DIR"):
        return Path(os.environ["SABER_GAME_DIR"])
    cfg = config()
    if cfg.get("game_dir"):
        return Path(cfg["game_dir"])
    if not game:
        game = os.environ.get("SABER_GAME") or cfg.get("game")
        if not game:
            found = installed_games()
            game = found[0] if len(found) == 1 else None
    if not game:
        return None
    g = GAMES[game.lower()]
    roots = [Path(f"{d}:/") for d in "CDEFGH" if Path(f"{d}:/").exists()]
    roots += [Path(os.environ.get("ProgramFiles", "C:/Program Files")),
              Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))]
    for root in roots:
        for hint in g.install_hints:
            cand = root / hint
            if looks_like_install(cand):
                return cand
    return None


def looks_like_install(p: Path) -> bool:
    """Recognise an install by its STRUCTURE, not by an executable name.

    The exe is not reliably at the top level -- an Epic install of Expeditions
    has only .egstore / Media / preload / Sources there, and no .exe at all, so
    an exe-name check finds nothing and silently reports "not installed".
    Both games ship the same shape underneath:

        preload/paks/client/*.pak      the game data
        Sources/Bin, Sources/BinEditor the tools
    """
    if not p.is_dir():
        return False
    return ((p / "preload" / "paks" / "client").is_dir()
            or (p / "Sources" / "Bin").is_dir())


def resource_converter(game: str | None = None) -> Path | None:
    d = game_dir(game)
    if not d:
        return None
    g = GAMES[(game or os.environ.get("SABER_GAME") or "expeditions").lower()]
    p = d / g.bin_subdir / "ResourceConverter.exe"
    return p if p.is_file() else None


def bin_editor(game: str | None = None) -> Path | None:
    d = game_dir(game)
    if not d:
        return None
    g = GAMES[(game or os.environ.get("SABER_GAME") or "expeditions").lower()]
    p = d / g.editor_subdir
    return p if p.is_dir() else None


def describe() -> dict:
    out: dict = {"documents": str(_documents()),
                 "installed": installed_games(),
                 "config_file": str(Path(__file__).resolve().parent.parent / CONFIG_NAME),
                 "config": config(), "games": {}}
    for key in installed_games():
        try:
            m = media_root(key)
        except Exception as e:                                  # noqa: BLE001
            m = f"<{e}>"
        out["games"][key] = {
            "title": GAMES[key].title,
            "media": str(m),
            "game_dir": str(game_dir(key) or ""),
            "resource_converter": str(resource_converter(key) or ""),
            "bin_editor": str(bin_editor(key) or ""),
        }
    return out


if __name__ == "__main__":
    print(json.dumps(describe(), indent=2))
