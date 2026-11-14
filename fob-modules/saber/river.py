"""Riverbed reading and design for Expeditions maps.

A river in this engine is not painted terrain. It is a spline object stored as
XML in `Media\\prebuild\\<Map>\\subgroups\\River_<name>.xml`:

    <SubGroup Name="cottonmouth river">
        <River Flow="0" NeedFlowMapLoading="false" Clean="clay-orange" Muddy="brown" ...>
            <Node Position="(x; y; z)" Width="30.69" />
            ...
        </River>
    </SubGroup>

One subgroup may hold several `<River>` reaches. Each `Node` carries the water
*surface* height in its Y, so the drop between consecutive nodes is the surface
gradient of that reach - which is what decides whether the water is a pool, a
riffle or a rapid.

The bed underneath is separate: it lives in the terrain heightmap, and it is
where the craft is. Real channels are not flat-bottomed troughs. On a bend, the
fast thread of water rides the outside, scours it deep, and drops its load on
the inside as a shallow point bar. `meander_profile()` computes that asymmetry
from the spline's own curvature so a bed can be cut to match.

Thresholds here are deliberately the same ones `expeditions.sound.river_voice`
uses, so a reach that is cut as a rapid is also voiced as one.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    from game_paths import user_dir
    MEDIA = Path(user_dir()) / "Media"          # follows a moved / OneDrive Documents
except ImportError:
    MEDIA = Path.home() / "Documents/My Games/Expeditions/Media"

#: Reach classes by surface gradient (rise over run), shared with sound.py.
REACH_BANDS = [
    (0.004, "pool"),     # still water; deposition, silt, murk
    (0.010, "glide"),    # smooth moving water
    (0.020, "riffle"),   # broken surface, gravel, rocks near-surface
    (0.040, "rapid"),    # standing waves, exposed boulders, foam
    (math.inf, "chute"),  # steep drop, heavy whitewater
]


def classify(grade: float) -> str:
    for limit, name in REACH_BANDS:
        if grade < limit:
            return name
    return "chute"


@dataclass
class Node:
    x: float
    y: float
    z: float
    width: float


@dataclass
class Reach:
    """One `<River>` element: a continuous run of nodes."""
    nodes: list[Node]
    flow: str = "0"
    flow_map: bool = False
    clean: str = ""
    muddy: str = ""

    def segments(self):
        """Yield (a, b, run, drop, grade) for each consecutive node pair."""
        for a, b in zip(self.nodes, self.nodes[1:]):
            run = math.hypot(b.x - a.x, b.z - a.z)
            drop = a.y - b.y
            yield a, b, run, drop, (drop / run if run else 0.0)

    @property
    def length(self) -> float:
        return sum(s[2] for s in self.segments())

    @property
    def drop(self) -> float:
        return self.nodes[0].y - self.nodes[-1].y if self.nodes else 0.0

    @property
    def grade(self) -> float:
        return self.drop / self.length if self.length else 0.0

    def uphill_segments(self) -> list[tuple[int, float]]:
        """Segments where the surface rises downstream.

        A small rise reads as a pool or backwater and is fine. A large one means
        the node order or a height is wrong, and the water will look broken.
        """
        return [(i, -d) for i, (_, _, _, d, _) in enumerate(self.segments()) if d < -0.01]


@dataclass
class RiverGroup:
    name: str
    reaches: list[Reach] = field(default_factory=list)
    source: Path | None = None


_NUM = re.compile(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?")


def load_group(path: Path) -> RiverGroup:
    import xml.etree.ElementTree as ET

    path = Path(path)
    root = ET.parse(path).getroot()
    grp = RiverGroup(name=root.get("Name", path.stem), source=path)
    for r in root.findall("River"):
        nodes = []
        for n in r.findall("Node"):
            v = [float(m) for m in _NUM.findall(n.get("Position", ""))]
            if len(v) < 3:
                continue
            nodes.append(Node(v[0], v[1], v[2], float(n.get("Width", 0))))
        if nodes:
            grp.reaches.append(Reach(
                nodes=nodes,
                flow=r.get("Flow", "0"),
                flow_map=r.get("NeedFlowMapLoading", "false").lower() == "true",
                clean=r.get("Clean", ""),
                muddy=r.get("Muddy", ""),
            ))
    return grp


def load_map_rivers(level: str, media: Path = MEDIA) -> list[RiverGroup]:
    sub = media / "prebuild" / level / "subgroups"
    return [load_group(p) for p in sorted(sub.glob("River_*.xml"))
            if load_group(p).reaches]


# --------------------------------------------------------------------------
# micro-topography
# --------------------------------------------------------------------------

@dataclass
class BedSample:
    """A cross-section prescription at one point along the channel."""
    x: float
    z: float
    surface_y: float
    width: float
    grade: float
    reach: str
    curvature: float          # +ve bends left, -ve bends right, 0 straight
    thalweg_offset: float     # metres from centreline toward the outer bank
    outer_depth: float        # below surface at the outer bank
    inner_depth: float        # below surface at the inner point bar


def meander_profile(reach: Reach,
                    base_depth: float = 1.6,
                    scour_gain: float = 2.2,
                    bar_fraction: float = 0.25) -> list[BedSample]:
    """Prescribe an asymmetric bed from the spline's own curvature.

    The deep thread of a river - the thalweg - does not sit in the middle. It
    swings to the outside of every bend, because that is where the water has the
    most momentum, and it cuts there. The inside of the same bend collects what
    the water drops, forming a shallow point bar you can often walk on.

    `base_depth` is the depth of a straight reach. `scour_gain` multiplies it on
    a hard bend. `bar_fraction` is how much of the base depth survives on the
    inside - a quarter, by default, which is shallow enough to show gravel.

    Returned offsets are in metres, signed left-positive relative to the
    downstream direction, so they can be applied directly to a bed carve.
    """
    out: list[BedSample] = []
    nodes = reach.nodes
    for i, n in enumerate(nodes):
        prev_n = nodes[max(i - 1, 0)]
        next_n = nodes[min(i + 1, len(nodes) - 1)]

        v1 = (n.x - prev_n.x, n.z - prev_n.z)
        v2 = (next_n.x - n.x, next_n.z - n.z)
        l1 = math.hypot(*v1) or 1.0
        l2 = math.hypot(*v2) or 1.0
        # signed turn angle, normalised by the distance it turns over
        cross = (v1[0] * v2[1] - v1[1] * v2[0]) / (l1 * l2)
        curvature = max(-1.0, min(1.0, cross))

        run = math.hypot(next_n.x - prev_n.x, next_n.z - prev_n.z)
        grade = abs(prev_n.y - next_n.y) / run if run else 0.0

        bend = abs(curvature)
        outer = base_depth * (1.0 + scour_gain * bend)
        inner = base_depth * bar_fraction * (1.0 - 0.5 * bend)
        # thalweg rides the outside; sign is opposite the turn direction
        offset = -math.copysign(n.width * 0.30 * bend, curvature)

        out.append(BedSample(
            x=n.x, z=n.z, surface_y=n.y, width=n.width, grade=grade,
            reach=classify(grade), curvature=curvature,
            thalweg_offset=offset, outer_depth=outer, inner_depth=inner,
        ))
    return out


# --------------------------------------------------------------------------
# hydraulics
# --------------------------------------------------------------------------

@dataclass
class Flowstate:
    """Velocity, depth and Froude number at one chainage."""
    s: float          # metres along the channel from the head
    x: float
    z: float
    surface_y: float
    width: float
    grade: float
    depth: float
    velocity: float   # m/s
    froude: float
    reach: str


def hydraulics(samples, discharge: float = 40.0, roughness: float = 0.040,
               min_grade: float = 5e-4) -> list[Flowstate]:
    """Solve steady uniform flow along a channel.

    Manning for a wide channel, where the hydraulic radius is close enough to the
    depth that the difference is lost in the choice of `roughness`:

        Q = (1/n) * W * d^(5/3) * sqrt(S)   ->   d = (Q*n / (W*sqrt(S)))^(3/5)
        v = Q / (W*d)

    Holding Q constant down the reach is what makes the result behave like water
    rather than like a gradient ramp: where the channel steepens the flow goes
    shallow AND fast, where it flattens it pools deep AND slow. That coupling is
    the whole reason a river looks different from a painted stripe.

    The Froude number v/sqrt(g*d) is what decides foam. Below ~0.5 the surface is
    glassy; approaching 1 it stands up into waves; above 1 the flow is
    supercritical and genuinely white. Foam keyed to Froude puts whitewater where
    the physics puts it, which is what `REACH_BANDS` classifies by eye.

    `min_grade` floors the gradient so a dead-flat pool does not divide by zero
    and report infinite depth.

    Defaults suit a mid-size gravel-bed river roughly 50 m wide. `roughness`
    0.035-0.05 is the normal range for a natural channel with boulders; a smooth
    canal is nearer 0.025.
    """
    g = 9.81
    out: list[Flowstate] = []
    for sm in samples:
        S = max(abs(sm.grade), min_grade)
        W = max(sm.width, 1.0)
        depth = (discharge * roughness / (W * math.sqrt(S))) ** 0.6
        velocity = discharge / (W * depth)
        out.append(Flowstate(
            s=getattr(sm, "s", 0.0), x=sm.x, z=sm.z, surface_y=sm.surface_y,
            width=W, grade=S, depth=depth, velocity=velocity,
            froude=velocity / math.sqrt(g * depth), reach=classify(S),
        ))
    return out


def riffle_pool(reach: Reach, max_amplitude: float = 0.10,
                wavelength_widths: float = 6.0, energy_gain: float = 0.40):
    """Superimpose a riffle-pool sequence on a reach's surface profile.

    A natural channel does not hold one gradient for hundreds of metres. It
    alternates between short steep riffles and long slack pools, and the spacing
    is famously regular: five to seven channel widths, whatever the size of the
    river. A profile drawn as a straight ramp between two heights is the one
    thing that reads instantly as man-made, because no real water does it.

    The correction is a zero-mean sinusoid, so the reach keeps its total drop and
    both end heights exactly. Amplitude is capped two ways:

    * `max_amplitude` metres outright, so the water surface never moves far from
      the level the banks were cut for;
    * `energy_gain` times the drop the base gradient itself produces over a
      quarter wavelength, so a slack pool gets a whisper of structure and a steep
      reach gets the full amount. Without this the sinusoid would out-run the
      gradient in the flattest water and tilt the surface UPHILL.

    A final pass enforces a monotonically falling surface regardless, because
    water running uphill is worse than water running straight.

    Returns (new_ys, report).
    """
    nodes = reach.nodes
    if len(nodes) < 4:
        return [n.y for n in nodes], {"changed": 0, "reason": "too few nodes"}

    s, ys, ws = [0.0], [nodes[0].y], [nodes[0].width]
    for a, b in zip(nodes, nodes[1:]):
        s.append(s[-1] + math.hypot(b.x - a.x, b.z - a.z))
        ys.append(b.y)
        ws.append(b.width)
    total = s[-1]
    lam = wavelength_widths * (sum(ws) / len(ws))

    out = list(ys)
    for i in range(1, len(nodes) - 1):
        # local gradient from the neighbours, i.e. the energy available here
        run = s[i + 1] - s[i - 1]
        grade = (ys[i - 1] - ys[i + 1]) / run if run else 0.0
        energy_cap = energy_gain * max(grade, 0.0) * lam / (2 * math.pi)
        amp = min(max_amplitude, energy_cap)
        # taper to nothing at both ends so the reach still meets its neighbours
        taper = math.sin(math.pi * s[i] / total) ** 0.5 if total else 0.0
        out[i] = ys[i] + amp * taper * math.sin(2 * math.pi * s[i] / lam)

    # never let the surface climb downstream
    fixed = 0
    for i in range(1, len(out)):
        if out[i] > out[i - 1]:
            out[i] = out[i - 1]
            fixed += 1

    dev = [abs(a - b) for a, b in zip(out, ys)]
    return out, {
        "changed": sum(1 for d in dev if d > 1e-4),
        "max_deviation": max(dev),
        "mean_deviation": sum(dev) / len(dev),
        "wavelength": lam,
        "monotonic_fixes": fixed,
        "drop_before": ys[0] - ys[-1],
        "drop_after": out[0] - out[-1],
    }


def audit(groups: list[RiverGroup]) -> str:
    """Human-readable report of every river on a map."""
    lines: list[str] = []
    no_flowmap = 0
    total_reaches = 0
    for g in groups:
        lines.append(f"\n{g.name}  ({len(g.reaches)} reach(es))")
        for i, r in enumerate(g.reaches):
            total_reaches += 1
            if not r.flow_map:
                no_flowmap += 1
            widths = [n.width for n in r.nodes]
            bands: dict[str, int] = {}
            for *_, grade in r.segments():
                bands[classify(grade)] = bands.get(classify(grade), 0) + 1
            uphill = r.uphill_segments()
            lines.append(
                f"   [{i}] {len(r.nodes):>3} nodes  {r.length:8.1f} m  "
                f"drop {r.drop:7.2f} m  grade {r.grade:.4f} ({classify(r.grade)})")
            lines.append(
                f"        width {min(widths):6.1f}-{max(widths):6.1f} m   "
                f"segments: {bands}   flowmap={'on' if r.flow_map else 'OFF'}"
                f"   {r.clean}/{r.muddy}")
            if uphill:
                worst = max(u[1] for u in uphill)
                lines.append(
                    f"        !! {len(uphill)} segment(s) rise downstream, worst {worst:.2f} m"
                    f"  (small = pool/backwater, large = bad node order or height)")
    lines.append(f"\n{total_reaches} reaches total; {no_flowmap} with the flow map OFF.")
    return "\n".join(lines)
