# test_install_prune.py -- a rebuild's renamed maps must not leave the old ones in the mod
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_model_to_mod as inst


def test_removes_only_this_brands_unused_maps(tmp_path):
    tex = tmp_path / "textures" / "models"; pct = tmp_path / "prebuild" / "textures" / "pct"
    tex.mkdir(parents=True); pct.mkdir(parents=True)
    for n in ("tuckyard_baked__n_d", "tuckyard_bakedmaterial__n_d", "tuckyardrb_baked__n_d", "tuckcart_bakedmaterial__d"):
        (tex / f"{n}.tga").write_bytes(b"x"); (pct / f"models_{n}.pct").write_bytes(b"x")
    gone = inst.prune_stale_maps(tmp_path, ["tuckyard"], ["tuckyard_baked__n_d"])
    assert sorted(f.name for f in gone) == ["models_tuckyard_bakedmaterial__n_d.pct", "tuckyard_bakedmaterial__n_d.tga"]
    assert (tex / "tuckyardrb_baked__n_d.tga").exists() and (tex / "tuckcart_bakedmaterial__d.tga").exists()
