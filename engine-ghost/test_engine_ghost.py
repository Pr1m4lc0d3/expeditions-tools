"""Checks that need neither the game nor Blender: python -m pytest test_engine_ghost.py"""
import numpy as np
import pytest

import engine_ghost as eg
import meshread

GB = eg.GHOSTS["env/engine_default"]


def truck(hood=1.3, nose=2.0, shield=0.6, bumper=True):
    """a point-cloud truck: a flat hood from the windshield base to the nose, a cab behind, a low bumper ahead"""
    rng = np.random.default_rng(1)
    pts = []
    for x in np.arange(-2.0, nose, 0.02):
        top = hood if x > shield else hood + 0.5
        for z in (-0.3, 0.0, 0.3):
            pts += [(x, top, z), (x, 0.6, z)]
    if bumper:                                                   # lower and further out than the grille
        pts += [(x, 0.8, z) for x in np.arange(nose, nose + 0.2, 0.02) for z in (-0.3, 0.3)]
    glass = np.array([(x, hood + 0.05 + (shield - x) * 0.8, z) for x in np.arange(0.0, shield, 0.02) for z in (-0.3, 0.3)])
    P = np.array(pts) + rng.normal(0, 0.001, (len(pts), 3))
    return P, glass


def test_bay_ignores_the_bumper_and_finds_the_windshield():
    P, glass = truck()
    X, T = eg.profile(P)
    B = eg.bay(X, T, glass)
    assert abs(B["nose"] - 2.0) < 0.06                          # not the bumper at 2.2
    assert abs(B["windshield"] - 0.6) < 0.03
    assert abs(B["firewall"] - (0.6 + eg.FIREWALL)) < 0.03
    assert abs(B["hood"] - 1.3) < 0.02


def test_bay_without_glass_uses_the_rise():
    P, _ = truck()
    X, T = eg.profile(P)
    B = eg.bay(X, T, None)
    assert abs(B["windshield"] - 0.6) < 0.08


def test_suggestion_is_centred_and_under_the_hood():
    P, glass = truck()
    X, T = eg.profile(P)
    B = eg.bay(X, T, glass)
    at = eg.suggest(X, T, B, 0.9, GB)
    lo, hi = eg.ghost_box(at, 0.9, GB)
    assert abs((lo[0] + hi[0]) / 2 - (B["firewall"] + B["radiator"]) / 2) < 1e-6
    assert abs((lo[2] + hi[2]) / 2) < 1e-6                     # the box, not the origin, on the centre line
    assert abs(hi[1] - (1.3 - eg.GAP)) < 0.03
    assert eg.fits(X, T, B, at, 0.9, GB) == []


def test_fits_names_what_is_wrong():
    P, glass = truck()
    X, T = eg.profile(P)
    B = eg.bay(X, T, glass)
    msgs = eg.fits(X, T, B, (1.9, 1.3, -0.2), 0.9, GB)
    assert any("nose" in m for m in msgs)
    assert any("above the hood" in m for m in msgs)
    assert any("off the centre line" in m for m in msgs)


XML = """<Truck>
	<TruckData EngineIconMesh="env/engine_default" EngineIconScale="0.9" />
	<PhysicsModel Mesh="trucks/my_truck">
	<Sounds
		_template="Default"
		MinDist="10.0"
		Origin="(1.4; 1.3; 0)"
	>
	</Sounds>
	<Steam Origin="(1.45; 0.95; 0)" />
</Truck>"""


def test_read_and_set_sound():
    f = eg.read_xml(XML)
    assert f["mesh"] == "env/engine_default" and f["scale"] == 0.9
    assert f["physics_mesh"] == "trucks/my_truck"
    assert list(f["sound"]) == [1.4, 1.3, 0.0]
    out = eg.set_sound(XML, (1.12, 0.89, 0), steam=True, scale=0.8)
    f2 = eg.read_xml(out)
    assert list(f2["sound"]) == [1.12, 0.89, 0.0] and list(f2["steam"]) == [1.12, 0.89, 0.0]
    assert f2["scale"] == 0.8
    assert out.count("Origin=") == XML.count("Origin=")       # nothing else touched


def test_set_sound_refuses_a_truck_without_one():
    with pytest.raises(SystemExit):
        eg.set_sound("<Truck><Sounds MinDist=\"8\"></Sounds></Truck>", (1, 1, 0))


def test_place_prefers_the_frame_chain_on_a_tie():
    P = np.array([[0.2, 0.5, 0.0], [0.5, 0.6, 0.1]])
    W = np.eye(4); W[3, 1] = 1.0                                # +1 m up
    rec = {"local": np.eye(4), "world": W}
    box = np.array([-3, 0.3, -1, 3, 2.0, 1])                    # both readings fit inside
    out = meshread._place(P, rec, box)
    assert np.allclose(out[:, 1], P[:, 1] + 1.0)


def test_place_keeps_raw_when_the_chain_leaves_the_mesh():
    P = np.array([[1.5, 1.0, 0.0], [1.6, 1.1, 0.1]])
    W = np.eye(4); W[3, 0] = -3.0                               # would push it out of the box
    rec = {"local": np.eye(4) @ W, "world": W}
    box = np.array([-1.0, 0.0, -1, 1.8, 1.7, 1])
    out = meshread._place(P, rec, box)
    assert np.allclose(out, P)
