# test_map_models.py
import json
import struct
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import after_pack
import fix_map_model_brands as fmb
import import_model


def rec(s: str) -> bytes:
    b = s.encode() + b"\0"
    return struct.pack("<H", len(b)) + b


def scene(*strings: str) -> bytes:
    body = b"".join(rec(s) for s in strings)
    d = bytearray(b"SSLB" + b"\0" * 4 + struct.pack("<I", 0) + b"\0" * 4 + body)
    struct.pack_into("<I", d, 8, len(d))
    return bytes(d)


# --- the brand fix ---------------------------------------------------------

def test_key_is_what_the_game_registers():
    # archive cut at its first "_": mod_level_objectives.pak -> level_objectives.pak
    assert fmb.model_key("my_pump", "mod_level_objectives.pak") == "my_pump_level_objectives.pak"


def test_rewrite_renames_shipped_brands_and_fixes_the_size():
    s = scene("my_pump\\Media", "rock_stock", "landmarks/my_pump_lmk\\Media")
    rows = fmb.plan(s, ["my_pump"], "mod_level_x.pak")
    out = fmb.rewrite(s, rows)
    assert rec("my_pump_level_x.pak") in out
    assert rec("my_pump\\Media") not in out
    assert rec("landmarks/my_pump_lmk\\Media") in out          # landmarks are left alone
    assert struct.unpack_from("<I", out, 8)[0] == len(out)


def test_a_fixed_scene_has_nothing_to_do():
    s = scene("my_pump_level_x.pak")
    (_, _, _, n, done), = fmb.plan(s, ["my_pump"], "mod_level_x.pak")
    assert (n, done) == (0, 1)


def test_rewrite_refuses_a_scene_whose_size_field_is_wrong():
    s = bytearray(scene("my_pump\\Media"))
    struct.pack_into("<I", s, 8, 7)
    with pytest.raises(SystemExit):
        fmb.rewrite(bytes(s), fmb.plan(bytes(s), ["my_pump"], "mod_level_x.pak"))


# --- after_pack -------------------------------------------------------------

def test_only_master_paks_are_picked(tmp_path):
    for n in ("level_a.pak", "level_a_pc.pak", "level_a_nx64.pak", "level_a_xbox_one.pak",
              "level_a_playstation_5.pak", "level_b.pak", "BaseUpgradeDesc_x.pak"):
        (tmp_path / n).write_bytes(b"x")
    (tmp_path / "level_empty.pak").write_bytes(b"")
    assert sorted(p.name for p in after_pack.level_paks(tmp_path, [])) == ["level_a.pak", "level_b.pak"]
    assert [p.name for p in after_pack.level_paks(tmp_path, ["b"])] == ["level_b.pak"]


# --- import_model -----------------------------------------------------------

def glb(tmp_path: Path, doc: dict) -> Path:
    j = json.dumps(doc).encode()
    j += b" " * (-len(j) % 4)
    data = b"glTF" + struct.pack("<II", 2, 20 + len(j)) + struct.pack("<II", len(j), 0x4E4F534A) + j
    p = tmp_path / "m.glb"
    p.write_bytes(data)
    return p


def test_a_part_named_like_the_model_is_caught_before_converting(tmp_path):
    g = glb(tmp_path, {"meshes": [{"name": "My_Shed"}], "materials": [{"name": "wood"}]})
    assert any("same as --name" in p for p in import_model.problems(g, "my_shed"))
    assert import_model.problems(g, "my_shed_a") == []


def test_names_the_editor_cannot_take_are_refused(tmp_path):
    g = glb(tmp_path, {})
    for bad in ("My_Shed", "shed-2", "2shed", ""):
        assert import_model.problems(g, bad)


def test_the_unused_baked_normal_is_found_and_a_used_one_kept(tmp_path):
    (tmp_path / "textures" / "models").mkdir(parents=True)
    (tmp_path / "meshes" / "models").mkdir(parents=True)
    bake = tmp_path / "textures" / "models" / "shed_baked__n_d.tga"
    bake.write_bytes(b"x")
    xml = tmp_path / "meshes" / "models" / "shed.xml"
    xml.write_text('<Material NormalMap="models/shed_kit__n_d.tga"/>')
    assert import_model.stray_bake(tmp_path, "shed") == bake
    xml.write_text('<Material NormalMap="models/shed_baked__n_d.tga"/>')
    assert import_model.stray_bake(tmp_path, "shed") is None


def test_a_real_pak_round_trips_through_the_splice(tmp_path):
    pak = tmp_path / "level_t.pak"
    s = scene("my_pump\\Media")
    with zipfile.ZipFile(pak, "w", zipfile.ZIP_STORED) as z:
        z.writestr(fmb.SCENE, s)
        z.writestr("classes/models/my_pump.xml", "<ModelBrand/>")
    new = fmb.rewrite(s, fmb.plan(s, ["my_pump"], "mod_level_t.pak"))
    fmb.splice(str(pak), fmb.SCENE, new, force_running=True)
    with zipfile.ZipFile(pak) as z:
        assert z.read(fmb.SCENE) == new
        assert z.read("classes/models/my_pump.xml") == b"<ModelBrand/>"
