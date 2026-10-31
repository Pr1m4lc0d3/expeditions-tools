"""The tint recolour: maths and mesh edits, no game files needed."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import recolor_fob as rf  # noqa: E402

AMBER = np.array([155.0, 109.0, 48.0])      # fob_exp_boxes__d paint, measured


def test_olive_matches_the_value_approved_in_game():
    # The Crow's Nest tarps went olive in game with g(140; 211; 223); the tool
    # must land within a few steps of it from the measured paint.
    mult, miss = rf.tint_for(AMBER, "olive")
    assert miss < 0.02
    assert all(abs(a - b) <= 4 for a, b in zip(mult, (140, 211, 223)))


def test_a_tint_never_brightens_a_channel():
    for colour in rf.TINTS:
        mult, _ = rf.tint_for(AMBER, colour)
        assert all(0 <= c <= 255 for c in mult)


def test_blue_and_white_are_out_of_reach_on_amber():
    for colour in ("blue", "purple", "white"):
        assert rf.tint_for(AMBER, colour)[1] > rf.REACH, colour


XML = """<CombineXMesh Type="Model">
\t<Material
\t\tAlbedoMap="models/fob_exp_boxes__d.tga"
\t\tName="fob_exp_containers"
\t\tNormalMap="models/fob_exp_boxes__n_d.tga"
\t/>
\t<Material
\t\tName="plates_mat"
\t\tDiffuseMultiplier="g(190; 0; 0; 255)"
\t\tAlbedoMap="models/heli_tile__d.tga"
\t/>
</CombineXMesh>
"""


def test_apply_tints_touches_only_the_named_maps():
    out, done = rf.apply_tints(XML, {"fob_exp_boxes__d": (140, 211, 223)})
    assert done == ["fob_exp_containers"]
    assert 'DiffuseMultiplier="g(140; 211; 223; 255)"' in out
    assert 'DiffuseMultiplier="g(190; 0; 0; 255)"' in out          # the other material is untouched
    assert "models/fob_exp_boxes__d.tga" in out                       # Saber's texture stays


def test_apply_tints_replaces_an_existing_multiplier():
    out, _ = rf.apply_tints(XML, {"heli_tile__d": (95, 97, 100)})
    assert out.count("DiffuseMultiplier") == 1          # replaced, not added beside the old one
    assert "g(95; 97; 100; 255)" in out and "g(190; 0; 0; 255)" not in out
