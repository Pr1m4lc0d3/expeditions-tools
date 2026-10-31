# test_gamepaths.py
# With both games installed and no toolkit.json, game_dir() returned None while
# media_root() picked a game, so the converter was "not found" on any such
# machine. Both now resolve the game through which_game().
import pytest

from saber import gamepaths


@pytest.fixture
def two_games(tmp_path, monkeypatch):
    for g in gamepaths.GAMES.values():
        (tmp_path / "My Games" / g.my_games / "Media").mkdir(parents=True)
    monkeypatch.setattr(gamepaths, "_documents", lambda: tmp_path)
    monkeypatch.setattr(gamepaths, "config", lambda: {})
    for k in ("SABER_GAME", "SABER_GAME_DIR", "SABER_MEDIA"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def test_both_games_and_one_in_use_resolves_the_install(two_games, monkeypatch):
    work = two_games / "My Games" / gamepaths.GAMES["expeditions"].my_games / "Media" / "prebuild" / "level_x"
    work.mkdir(parents=True)
    monkeypatch.setattr(gamepaths, "looks_like_install", lambda p: True)
    assert gamepaths.which_game() == "expeditions"
    assert gamepaths.game_dir() is not None
    assert gamepaths.media_root().parent.name == gamepaths.GAMES["expeditions"].my_games


def test_both_games_and_nothing_to_choose_by_finds_no_install(two_games):
    with pytest.raises(RuntimeError):
        gamepaths.which_game()
    assert gamepaths.game_dir() is None   # a courtesy lookup never raises


def test_an_explicit_game_wins(two_games, monkeypatch):
    monkeypatch.setenv("SABER_GAME", "snowrunner")
    assert gamepaths.which_game() == "snowrunner"
    assert gamepaths.which_game("expeditions") == "expeditions"
