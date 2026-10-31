# test_model_gates.py -- the three refusals that keep a broken model or popup out of a mod.
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_backfacing
import check_fob_module


# --- verdict: calibrated 2026-10-25 against six builds the owner judged in game ---
def test_owner_judged_clean_builds_pass():
    for glb, fbx in [(3.45, 0.07), (0.16, 0.00), (0.05, 0.05), (0.28, 0.17), (0.13, 0.07)]:
        assert check_backfacing.verdict(glb, fbx)[0], (glb, fbx)


def test_docbase_streaks_fail_even_though_meshy_has_them_too():
    # Doc's container walls: the owner saw vertical scramble; 1.95% vs GLB 1.94%.
    assert not check_backfacing.verdict(1.94, 1.95)[0]
    # ...and the per-face fixer's 0.25% build, which still showed it.
    assert not check_backfacing.verdict(1.94, 0.25)[0]


def test_conversion_that_turns_faces_around_fails():
    # recalc_face_normals: a build far worse than its source.
    ok, msg = check_backfacing.verdict(0.10, 0.60)
    assert not ok and "turned faces around" in msg


# --- passed(): install refuses without a record for THIS fbx ---
def _record(tmp_path, monkeypatch, **over):
    monkeypatch.setattr(check_backfacing, "REPORTS", tmp_path / "reports")
    fbx = tmp_path / "m.fbx"
    fbx.write_bytes(b"x" * 10)
    st = fbx.stat()
    rec = {"glb_pct": 0.2, "fbx_pct": 0.1, "pass": True, "fbx_size": st.st_size, "fbx_mtime": st.st_mtime}
    rec.update(over)
    d = tmp_path / "reports" / "m"
    d.mkdir(parents=True)
    (d / "backface.json").write_text(json.dumps(rec))
    return fbx


def test_install_accepts_a_pass_on_this_fbx(tmp_path, monkeypatch):
    assert check_backfacing.passed("m", _record(tmp_path, monkeypatch))[0]


def test_install_refuses_without_a_record(tmp_path, monkeypatch):
    monkeypatch.setattr(check_backfacing, "REPORTS", tmp_path)
    fbx = tmp_path / "m.fbx"
    fbx.write_bytes(b"x")
    assert not check_backfacing.passed("m", fbx)[0]


def test_install_refuses_a_failed_check(tmp_path, monkeypatch):
    assert not check_backfacing.passed("m", _record(tmp_path, monkeypatch, **{"pass": False}))[0]


def test_install_refuses_an_fbx_rebuilt_after_its_check(tmp_path, monkeypatch):
    fbx = _record(tmp_path, monkeypatch)
    fbx.write_bytes(b"y" * 11)
    ok, msg = check_backfacing.passed("m", fbx)
    assert not ok and "changed" in msg


# --- popupModuleBg: the game loads mod PNGs only for bg / bgSmall / bgRight ---
def _desc(popup):
    return json.dumps({"SslType": "BaseUpgradeDesc", "name": "T",
                       "SslValue": {"bg": "t_bg", "popupModuleBg": popup}})


def _rules(raw, tmp_path):
    (tmp_path / "ui" / "textures").mkdir(parents=True)
    for n in ("t_bg", "t_popup"):
        (tmp_path / "ui" / "textures" / f"{n}.png").write_bytes(b"")
    return {r for _, r, _ in check_fob_module.check_descriptor(raw, "t", "t", mod_dir=tmp_path)}


def test_custom_popup_png_is_an_error(tmp_path):
    if check_fob_module.REFS.symbols is None:
        return  # no game gfx bundle on this machine; the rule cannot run
    assert "symbol.customPopup" in _rules(_desc("t_popup"), tmp_path)


def test_empty_popup_is_fine(tmp_path):
    assert "symbol.customPopup" not in _rules(_desc(""), tmp_path)
