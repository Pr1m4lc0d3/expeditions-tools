"""sculpt -- the editor's terrain brushes, as composable strokes.

Every tool here so far tried to produce a finished surface in one pass: grade the
road, smooth the road, done. That is not how terrain gets made. Sculpting is
iterative - raise a shoulder, flatten its top, smooth where it meets the hill,
then go back and lower part of it because the first pass overshot. You bounce
between brushes and converge.

A one-shot tool cannot express that. It has one set of parameters and one answer,
so when the answer is wrong there is nothing to adjust except the parameters, and
the result is the whole surface changing at once instead of one part of it getting
better.

So the unit here is a STROKE, not a result:

    s = Sculpt(height, mpp)
    s.stroke(path, Brush('height',  size=6, falloff=0.6, strength=1.0), value=+1.5)
    s.stroke(path, Brush('flatten', size=5, falloff=0.0, strength=0.8))
    s.stroke(path, Brush('smooth',  size=1.0, falloff=0.0, strength=1.0), passes=8)
    s.report()

Strokes are recorded, so a sequence can be printed, replayed, or compared against
what a human did. Measuring between strokes is the point: sculpting converges by
looking, not by getting the parameters right first time.

The three modes are the editor's Main Geometry brush:

    height   raise or lower; negative value digs
    flatten  pull toward the height at the brush centre - flat tops, benches,
             steps, 90 degree sides when falloff is 0
    smooth   pull toward the local mean; levels height differences

and the three controls are its controls: Size, Falloff, Strength. Falloff 0 is
hard - a flat top with vertical sides. Falloff 1 is a soft dome. Using a soft
brush where a hard one belongs is how a road ends up a row of domes instead of a
bench.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Brush:
    """Size in metres, falloff 0 (hard) to 1 (soft dome), strength 0..1."""
    mode: str = "smooth"
    size: float = 2.0
    falloff: float = 0.0
    strength: float = 1.0

    def kernel(self, mpp: float) -> np.ndarray:
        """Brush profile, peak 1.0 at the centre. NOT normalised.

        The profile is the per-pixel weight of the stroke, which is what Falloff
        controls. Normalising here would turn Falloff into a no-op for `height`
        and `flatten`, where what matters is the shape laid down, not an average.
        """
        rad_px = max(self.size / mpp, 0.5)
        r = int(math.ceil(rad_px))
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        d = np.hypot(xx, yy) / rad_px
        inside = d <= 1.0
        if self.falloff <= 0:
            return inside.astype(np.float64)
        e = np.clip((1.0 - d) / self.falloff, 0.0, 1.0)
        return np.where(inside, e * e * (3 - 2 * e), 0.0)

    def mean_kernel(self, mpp: float) -> np.ndarray:
        k = self.kernel(mpp)
        s = k.sum()
        return k / s if s else k


@dataclass
class Stroke:
    brush: Brush
    kind: str
    value: float
    passes: int
    changed: int
    lowered: int
    raised: int
    max_cut: float
    max_fill: float

    def __str__(self):
        b = self.brush
        return (f"{b.mode:<8} size {b.size:5.2f} falloff {b.falloff:4.2f} "
                f"strength {b.strength:4.2f} x{self.passes:<3} {self.kind:<8} "
                f"value {self.value:+6.2f}  ->  {self.changed:7d} px, "
                f"cut {self.max_cut:5.2f} fill {self.max_fill:5.2f} m")


class Sculpt:
    """A height field plus the record of how it got that way."""

    def __init__(self, height_m: np.ndarray, mpp: float):
        self.h = np.array(height_m, dtype=np.float64)
        self.mpp = float(mpp)
        self.strokes: list[Stroke] = []
        self._start = self.h.copy()

    # ---------------------------------------------------------------- masks
    def path_mask(self, path_xy, width_m, half_extent=None):
        """Mask of a corridor along a polyline given in world metres."""
        n = self.h.shape[0]
        half = half_extent if half_extent is not None else (n - 1) * self.mpp / 2
        xs = np.asarray([p[0] for p in path_xy], float)
        ys = np.asarray([p[1] for p in path_xy], float)
        seg = np.hypot(np.diff(xs), np.diff(ys))
        s = np.concatenate([[0.0], np.cumsum(seg)])
        if s[-1] <= 0:
            return np.zeros(self.h.shape, bool)
        t = np.arange(0.0, s[-1], self.mpp)
        px = (np.interp(t, s, xs) + half) / self.mpp
        py = (np.interp(t, s, ys) + half) / self.mpp
        m = np.zeros(self.h.shape, bool)
        rad = int(math.ceil(width_m / 2 / self.mpp))
        yy, xx = np.mgrid[-rad:rad + 1, -rad:rad + 1] * self.mpp
        disc = np.hypot(xx, yy) <= width_m / 2
        for c0, r0 in zip(np.round(px).astype(int), np.round(py).astype(int)):
            r1, r2 = max(0, r0 - rad), min(n, r0 + rad + 1)
            c1, c2 = max(0, c0 - rad), min(n, c0 + rad + 1)
            if r1 >= r2 or c1 >= c2:
                continue
            m[r1:r2, c1:c2] |= disc[(r1 - r0 + rad):(r2 - r0 + rad),
                                    (c1 - c0 + rad):(c2 - c0 + rad)]
        return m

    # --------------------------------------------------------------- strokes
    def apply(self, mask, brush: Brush, value: float = 0.0, passes: int = 1,
              lower_only: bool = False, raise_only: bool = False,
              kind: str = "area", target_field=None):
        """Run one brush over `mask`, `passes` times, and record it."""
        from scipy.ndimage import convolve, maximum_filter
        before = self.h.copy()
        km = brush.mean_kernel(self.mpp)
        for _ in range(passes):
            if brush.mode == "smooth":
                target = convolve(self.h, km, mode="nearest")
            elif brush.mode == "flatten":
                # The editor's Flatten takes the height UNDER THE BRUSH CENTRE and
                # levels the footprint to it. That is a local operation, and the
                # local part is the whole point: flattening a corridor toward one
                # global height levelled 150 m of hillside to a single elevation
                # (42 m cut, 73 m fill) the first time this was written.
                if target_field is not None:
                    target = target_field
                elif value:
                    target = np.full_like(self.h, value)
                else:
                    target = convolve(self.h, km, mode="nearest")
            elif brush.mode == "height":
                target = self.h + value
            else:
                raise ValueError(f"unknown brush mode {brush.mode!r}")
            nxt = self.h + brush.strength * (target - self.h)
            nxt = np.where(mask, nxt, self.h)
            if lower_only:
                nxt = np.minimum(nxt, self.h)
            if raise_only:
                nxt = np.maximum(nxt, self.h)
            self.h = nxt
        d = self.h - before
        ch = np.abs(d) > 1e-4
        self.strokes.append(Stroke(
            brush=brush, kind=kind, value=value, passes=passes,
            changed=int(ch.sum()), lowered=int((d < -1e-4).sum()),
            raised=int((d > 1e-4).sum()),
            max_cut=float(-d.min()) if ch.any() else 0.0,
            max_fill=float(d.max()) if ch.any() else 0.0))
        return self

    def centreline_field(self, path_xy, smooth_m=0.0, half_extent=None):
        """Height at the nearest point of the path, for every pixel.

        This is what `flatten` needs along a road: the bench is level ACROSS the
        corridor at the centreline's own height, while still following the road
        downhill. Optionally smooth the profile first, which is how a bench gets
        a clean grade instead of inheriting every bump the centreline sits on.
        """
        from scipy.ndimage import distance_transform_edt
        n = self.h.shape[0]
        half = half_extent if half_extent is not None else (n - 1) * self.mpp / 2
        xs = np.asarray([p[0] for p in path_xy], float)
        ys = np.asarray([p[1] for p in path_xy], float)
        seg = np.hypot(np.diff(xs), np.diff(ys))
        s = np.concatenate([[0.0], np.cumsum(seg)])
        t = np.arange(0.0, s[-1], self.mpp)
        px = np.round((np.interp(t, s, xs) + half) / self.mpp).astype(int).clip(0, n - 1)
        py = np.round((np.interp(t, s, ys) + half) / self.mpp).astype(int).clip(0, n - 1)
        prof = self.h[py, px]
        if smooth_m > 0:
            w = max(3, int(smooth_m / self.mpp) | 1)
            prof = np.convolve(np.pad(prof, w // 2, mode='edge'),
                               np.ones(w) / w, mode='valid')[:len(prof)]
        seed = np.zeros(self.h.shape)
        seed[py, px] = prof
        marker = np.zeros(self.h.shape, bool)
        marker[py, px] = True
        # nearest-centreline lookup by distance transform
        _, (ri, ci) = distance_transform_edt(~marker, return_indices=True)
        return seed[ri, ci]

    def stroke(self, path_xy, brush: Brush, width_m=None, value=0.0, passes=1,
               lower_only=False, raise_only=False, target_field=None):
        """Drag a brush along a polyline."""
        w = width_m if width_m is not None else brush.size * 2
        return self.apply(self.path_mask(path_xy, w), brush, value, passes,
                          lower_only, raise_only, kind="stroke",
                          target_field=target_field)

    # --------------------------------------------------------------- reading
    def roughness(self, path_xy, window_m=30.0, half_extent=None):
        """RMS of the centreline about a moving trend, in cm -- the thing to watch."""
        n = self.h.shape[0]
        half = half_extent if half_extent is not None else (n - 1) * self.mpp / 2
        xs = np.asarray([p[0] for p in path_xy], float)
        ys = np.asarray([p[1] for p in path_xy], float)
        seg = np.hypot(np.diff(xs), np.diff(ys))
        s = np.concatenate([[0.0], np.cumsum(seg)])
        t = np.arange(0.0, s[-1], self.mpp)
        px = np.round((np.interp(t, s, xs) + half) / self.mpp).astype(int).clip(0, n - 1)
        py = np.round((np.interp(t, s, ys) + half) / self.mpp).astype(int).clip(0, n - 1)
        v = self.h[py, px]
        w = max(3, int(window_m / self.mpp) | 1)
        tr = np.convolve(np.pad(v, w // 2, mode="edge"), np.ones(w) / w,
                         mode="valid")[:len(v)]
        return float((v - tr).std()) * 100

    def report(self):
        lines = [f"{i + 1:2d}. {s}" for i, s in enumerate(self.strokes)]
        d = self.h - self._start
        lines.append(f"    net: {int((np.abs(d) > 1e-4).sum())} px changed, "
                     f"cut {-d.min():.2f} m, fill {d.max():.2f} m")
        return "\n".join(lines)
