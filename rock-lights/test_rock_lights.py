"""Checks that need neither the game nor Blender:  python -m pytest test_rock_lights.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import add_rock_lights as r  # noqa: E402
import pods  # noqa: E402

TRUCK = """<_templates Include="trucks" />
<Truck>
\t<TruckData>
\t\t<Wheels>
\t\t\t<Wheel Pos="(1.2; 0.4; 0.6)" />
\t\t\t<Wheel Pos="(1.2; 0.4; -0.6)" />
\t\t\t<Wheel Pos="(-1.3; 0.4; 0.6)" />
\t\t\t<Wheel Pos="(-1.3; 0.4; -0.6)" />
\t\t</Wheels>
\t</TruckData>
\t<PhysicsModel Mesh="trucks/test_truck" />
\t<GameData>
\t\t<UiDesc UiName="Test" />
\t</GameData>
</Truck>
"""


def rail(z0, z1, y0, y1, x0=-2.0, x1=2.0):
    """a box frame rail as triangles, in 10 cm sections like a real mesh (the mount search only looks at
    triangles whose centres are near the pod)"""
    F = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
         [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]]
    out = []
    for xa in np.arange(x0, x1, 0.1):
        c = np.array([[x, y, z] for x in (xa, xa + 0.1) for y in (y0, y1) for z in (z0, z1)], float)
        out.append(c[np.array(F)])
    return np.concatenate(out)


def test_layout_from_wheels():
    L = pods.layout(TRUCK, 0.45)
    assert len(L) == 4
    xs = [p[0] for p, _ in L]
    assert -1.3 < min(xs) and max(xs) < 1.2           # all between the axles
    assert all(abs(np.linalg.norm(a) - 1) < 1e-9 for _, a in L)


def test_pods_mount_on_the_rail():
    tris = rail(0.35, 0.45, 0.45, 0.60)               # left rail, 10 cm wide, bottom at y 0.45
    for p, a in pods.layout(TRUCK, 0.50):
        q, aim, sdir, slen, found = pods.mount(tris, p, a)
        assert found
        back = q - aim * pods.POD_BACK
        tip = back + sdir * slen                      # the tab lands on the rail
        assert 0.34 <= tip[2] <= 0.46 + 1e-6 and 0.44 <= tip[1] <= 0.61
        assert slen <= pods.REACH


def test_no_steel_falls_back():
    q, _a, sdir, slen, found = pods.mount(rail(5, 6, 5, 6), np.array([0, 0.5, 0.5]), np.array([0, -1.0, 0]))
    assert not found and slen == pods.FALLBACK_STEM and sdir[1] == 1


def test_socket_round_trip_keeps_bytes():
    for nl in ("\n", "\r\n"):
        x = TRUCK.replace("\n", nl)
        y = r.add_socket(x, "test_truck", "BoneChassis_cdt")
        assert 'Socket Names="RockLightsTestTruck" ParentFrame="BoneChassis_cdt"' in y
        assert y.index(r.MARK) < y.index("</GameData>")
        assert r.add_socket(y, "test_truck", None) == y          # never twice
        assert r.remove_socket(y) == x


def test_kit_has_no_body_and_eight_lights():
    m = [(np.array([0.5, 0.4, 0.5]), np.array([0, -1.0, 0]), np.array([0, 1.0, 0]), 0.05, True)] * 4
    x = r.kit_xml("test_truck", "amber", "BoneChassis_cdt", pods.lights(m))
    assert "<Body" not in x                           # a Body here makes Buy silently fail
    assert x.count("<Light ") == 8 and x.count("<Flare ") == 8
    assert 'InstallSocket Type="RockLightsTestTruck"' in x
    assert 'Mesh="trucks/rock_lights_pods_test_truck"' in x
    assert "ParentFrame" not in r.kit_xml("t", "red", None, pods.lights(m))


def test_pod_mesh_is_mirrored_and_closed():
    m = [(np.array([0.5, 0.4, 0.5]), pods.unit([0, -1.0, 0.3]), np.array([0, 1.0, 0]), 0.05, True)] * 4
    mesh = pods.pod_mesh(m)
    V = np.array(mesh.v)
    assert np.isclose(V[:, 2].max(), -V[:, 2].min())
    assert len(mesh.f) == len(mesh.sw)
