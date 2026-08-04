"""Animated lighting effects, rendered on the host.

Every effect reduces to the same two steps: give each LED a scalar position in 0..1
(from `layout`, so the ragged 2/4/4/3 key rows and the 8-LED underglow ring are handled
once), then sample a palette at that position offset by time. Keeping the spatial part in
normalised coordinates is what lets one effect definition work across both zones.

Effects are objects rather than functions because some carry state — sparkle needs an RNG,
ripple needs a list of live wavefronts. `render(t)` is pure with respect to that state
apart from the state it owns.
"""
from __future__ import annotations

import math
import random
from typing import Callable

from .color import RGB, Palette, resolve_palette, scale_lightness
from .frame import BLACK, Frame
from .layout import (KEY_N, UNDERGLOW_N, Layout, key_xy, underglow_angle,
                     underglow_xy)

EFFECT_NAMES = ("solid", "gradient", "rainbow", "breathe", "chase", "ripple",
                "sparkle", "wipe", "comet", "off")


def _positions(layout: Layout, direction: str) -> tuple[list[float], list[float]]:
    """Scalar 0..1 position per LED for keys and underglow, given a direction."""
    if direction == "ring":
        # Keys have no ring, so walk them in index order; underglow uses true ring angle.
        keys = [i / max(1, KEY_N - 1) for i in range(KEY_N)]
        under = [underglow_angle(i) for i in range(UNDERGLOW_N)]
        return keys, under

    if direction == "vertical":
        keys = [key_xy(i)[1] for i in range(KEY_N)]
        under = [underglow_xy(i)[1] for i in range(UNDERGLOW_N)]
    elif direction == "radial":
        keys = [_radius(*key_xy(i)) for i in range(KEY_N)]
        under = [_radius(*underglow_xy(i)) for i in range(UNDERGLOW_N)]
    else:  # horizontal
        keys = [key_xy(i)[0] for i in range(KEY_N)]
        under = [underglow_xy(i)[0] for i in range(UNDERGLOW_N)]
    return keys, under


def _radius(x: float, y: float) -> float:
    """Distance from the pad's centre, normalised so a corner is 1.0."""
    return min(1.0, math.hypot(x - 0.5, y - 0.5) / math.hypot(0.5, 0.5))


class Effect:
    """One configured effect instance. Construct via `build`."""

    def __init__(self, spec: dict, palettes: dict[str, Palette] | None = None):
        self.name: str = spec.get("name", "solid")
        self.palette: Palette = resolve_palette(spec.get("palette"), palettes)
        self.speed: float = float(spec.get("speed", 0.3))
        self.intensity: float = float(spec.get("intensity", 0.5))
        self.direction: str = spec.get("direction", "horizontal")
        self.reverse: bool = bool(spec.get("reverse", False))
        self.target: str = spec.get("target", "all")
        self.blend: str = spec.get("blend", "replace")
        self._rng = random.Random(0xC12)
        self._sparkles: dict[tuple[str, int], float] = {}
        self._ripples: list[tuple[float, float, float]] = []  # (t0, x, y)

    # --- public ------------------------------------------------------------

    def render(self, t: float, layout: Layout) -> Frame:
        """Frame for time `t` in seconds since the effect started."""
        fn: Callable[[float, Layout], Frame] = getattr(self, f"_r_{self.name}", self._r_solid)
        return fn(t, layout)

    def trigger_at(self, t: float, x: float, y: float) -> None:
        """Seed a ripple at a normalised position — used when a key is pressed."""
        self._ripples.append((t, x, y))

    # --- effects -----------------------------------------------------------

    def _r_off(self, t: float, layout: Layout) -> Frame:
        return Frame.blank()

    def _r_solid(self, t: float, layout: Layout) -> Frame:
        c = self.palette.sample(0.0)
        return Frame([c] * KEY_N, [c] * UNDERGLOW_N)

    def _r_gradient(self, t: float, layout: Layout) -> Frame:
        keys_p, under_p = _positions(layout, self.direction)
        # intensity compresses the gradient so it repeats across the pad; 0.5 == one pass.
        spread = 0.25 + self.intensity * 3.75
        off = t * self.speed * (-1 if self.reverse else 1)
        return Frame(
            [self.palette.sample(p * spread + off) for p in keys_p],
            [self.palette.sample(p * spread + off) for p in under_p],
        )

    def _r_rainbow(self, t: float, layout: Layout) -> Frame:
        saved, self.palette = self.palette, resolve_palette("rainbow")
        try:
            return self._r_gradient(t, layout)
        finally:
            self.palette = saved

    def _r_breathe(self, t: float, layout: Layout) -> Frame:
        # Sine on perceptual lightness, so the swell reads as smooth rather than
        # spending most of its time looking bright (which is what an RGB sine does).
        phase = 0.5 - 0.5 * math.cos(2 * math.pi * t * max(0.01, self.speed))
        floor = 0.08 + (1.0 - self.intensity) * 0.25
        level = floor + (1.0 - floor) * phase
        base = self.palette.sample((t * self.speed * 0.1) % 1.0)
        c = scale_lightness(base, level)
        return Frame([c] * KEY_N, [c] * UNDERGLOW_N)

    def _r_chase(self, t: float, layout: Layout) -> Frame:
        keys_p, under_p = _positions(layout, self.direction)
        head = (t * self.speed) % 1.0
        width = 0.06 + self.intensity * 0.30
        if self.reverse:
            head = 1.0 - head

        def lit(p: float) -> RGB:
            d = abs(p - head)
            d = min(d, 1.0 - d)          # wrap, so the chase has no seam
            if d > width:
                return BLACK
            return scale_lightness(self.palette.sample(p), 1.0 - d / width)

        return Frame([lit(p) for p in keys_p], [lit(p) for p in under_p])

    def _r_comet(self, t: float, layout: Layout) -> Frame:
        keys_p, under_p = _positions(layout, self.direction)
        head = (t * self.speed) % 1.0
        tail = 0.10 + self.intensity * 0.65
        if self.reverse:
            head = 1.0 - head

        def lit(p: float) -> RGB:
            d = (head - p) % 1.0        # only trails behind the head
            if d > tail:
                return BLACK
            return scale_lightness(self.palette.sample(p), (1.0 - d / tail) ** 2)

        return Frame([lit(p) for p in keys_p], [lit(p) for p in under_p])

    def _r_wipe(self, t: float, layout: Layout) -> Frame:
        keys_p, under_p = _positions(layout, self.direction)
        cycle = (t * self.speed) % 2.0
        filling = cycle < 1.0
        edge = cycle if filling else cycle - 1.0
        if self.reverse:
            edge = 1.0 - edge

        def lit(p: float) -> RGB:
            covered = (p <= edge) if not self.reverse else (p >= edge)
            on = covered if filling else not covered
            return self.palette.sample(p) if on else BLACK

        return Frame([lit(p) for p in keys_p], [lit(p) for p in under_p])

    def _r_sparkle(self, t: float, layout: Layout) -> Frame:
        # Each LED holds a decaying brightness; density controls how often new ones light.
        density = 0.02 + self.intensity * 0.35
        decay = 0.6 + (1.0 - self.intensity) * 2.4
        rate = max(0.05, self.speed)

        def zone(tag: str, n: int) -> list[RGB]:
            out: list[RGB] = []
            for i in range(n):
                key = (tag, i)
                born = self._sparkles.get(key)
                if born is None or (t - born) * decay * rate > 1.0:
                    if self._rng.random() < density * rate * 0.25:
                        self._sparkles[key] = t
                        born = t
                    else:
                        self._sparkles.pop(key, None)
                        out.append(BLACK)
                        continue
                level = max(0.0, 1.0 - (t - born) * decay * rate)
                out.append(scale_lightness(self.palette.sample(self._rng.random()), level))
            return out

        return Frame(zone("k", KEY_N), zone("u", UNDERGLOW_N))

    def _r_ripple(self, t: float, layout: Layout) -> Frame:
        # Wavefronts expand from trigger points; with none live, pulse from the centre.
        speed = max(0.05, self.speed)
        life = 1.0 / speed
        self._ripples = [r for r in self._ripples if t - r[0] < life]
        sources = self._ripples or [(t - (t % life), 0.5, 0.5)]
        width = 0.05 + self.intensity * 0.30

        def lit(x: float, y: float) -> RGB:
            best = BLACK
            best_level = 0.0
            for t0, sx, sy in sources:
                age = t - t0
                radius = age * speed
                d = abs(math.hypot(x - sx, y - sy) - radius)
                if d > width:
                    continue
                level = (1.0 - d / width) * max(0.0, 1.0 - age / life)
                if level > best_level:
                    best_level = level
                    best = scale_lightness(self.palette.sample(radius), level)
            return best

        return Frame(
            [lit(*key_xy(i)) for i in range(KEY_N)],
            [lit(*underglow_xy(i)) for i in range(UNDERGLOW_N)],
        )


def build(spec: dict | None, palettes: dict[str, Palette] | None = None) -> Effect | None:
    """Effect from a config `effect` object, or None if there isn't one."""
    if not spec or spec.get("name") in (None, "off"):
        return None if not spec else Effect({"name": "off"}, palettes)
    if spec.get("name") not in EFFECT_NAMES:
        raise ValueError(f"unknown effect {spec.get('name')!r}; expected one of {EFFECT_NAMES}")
    return Effect(spec, palettes)
