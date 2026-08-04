"""Colour maths for LibreMicro lighting.

Everything perceptual goes through OKLab/OKLCh (Björn Ottosson's spaces) rather than
raw RGB or HSV. That matters here for one concrete reason: interpolating two colours in
sRGB passes through a desaturated muddy middle, and stepping lightness in HSV doesn't
look evenly spaced to the eye. Gradients across 13 keys are short enough that both
artefacts are obvious. In OKLab a straight line between two colours stays vivid, and
equal L steps look equal.

sRGB values here are floats 0..1 unless a function name says `hex` or `bytes`.
"""
from __future__ import annotations

import math
import re
from typing import Sequence

RGB = tuple[float, float, float]

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


# --- sRGB gamma transfer ----------------------------------------------------

def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_gamma(c: float) -> float:
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


# --- hex <-> rgb ------------------------------------------------------------

def parse_hex(s: str) -> RGB:
    """'rrggbb' or '#rrggbb' -> (r, g, b) floats 0..1. Invalid input raises ValueError."""
    m = _HEX_RE.match(s.strip())
    if not m:
        raise ValueError(f"not an rrggbb colour: {s!r}")
    v = int(m.group(1), 16)
    return ((v >> 16 & 0xFF) / 255.0, (v >> 8 & 0xFF) / 255.0, (v & 0xFF) / 255.0)


def to_hex(rgb: RGB) -> str:
    """(r, g, b) floats -> 'rrggbb', clamped."""
    return "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in rgb)


def to_bytes(rgb: RGB) -> tuple[int, int, int]:
    return tuple(round(max(0.0, min(1.0, c)) * 255) for c in rgb)  # type: ignore[return-value]


# --- OKLab / OKLCh ---------------------------------------------------------

def rgb_to_oklab(rgb: RGB) -> RGB:
    r, g, b = (_to_linear(c) for c in rgb)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l, m, s))
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def oklab_to_rgb(lab: RGB) -> RGB:
    L, a, b = lab
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = (v ** 3 for v in (l_, m_, s_))
    lin = (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )
    return tuple(max(0.0, min(1.0, _to_gamma(max(0.0, c)))) for c in lin)  # type: ignore[return-value]


def oklch_to_rgb(L: float, C: float, h_deg: float) -> RGB:
    h = math.radians(h_deg)
    return oklab_to_rgb((L, C * math.cos(h), C * math.sin(h)))


def rgb_to_oklch(rgb: RGB) -> tuple[float, float, float]:
    L, a, b = rgb_to_oklab(rgb)
    return (L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360.0)


# --- mixing ----------------------------------------------------------------

def mix(a: RGB, b: RGB, t: float) -> RGB:
    """Perceptual blend of two sRGB colours, t=0 -> a, t=1 -> b."""
    t = max(0.0, min(1.0, t))
    la, lb = rgb_to_oklab(a), rgb_to_oklab(b)
    return oklab_to_rgb(tuple(x + (y - x) * t for x, y in zip(la, lb)))  # type: ignore[arg-type]


def scale_lightness(rgb: RGB, factor: float) -> RGB:
    """Scale perceptual lightness, keeping hue and chroma. Used for dimming and breathing.

    Chroma is scaled alongside L because a very dark colour at full chroma falls outside
    sRGB and clips to a different hue than the one asked for.
    """
    L, C, h = rgb_to_oklch(rgb)
    f = max(0.0, factor)
    return oklch_to_rgb(L * f, C * min(1.0, f), h)


# --- palettes --------------------------------------------------------------

class Palette:
    """A gradient defined by (pos, colour) stops, sampled perceptually.

    `cyclic` makes position 1.0 wrap back to the first stop, which is what ring and
    rainbow effects need in order not to show a seam.
    """

    __slots__ = ("stops", "cyclic", "label")

    def __init__(self, stops: Sequence[tuple[float, str | RGB]], cyclic: bool = False,
                 label: str | None = None):
        if not stops:
            raise ValueError("palette needs at least one stop")
        norm: list[tuple[float, RGB]] = []
        for pos, col in stops:
            rgb = parse_hex(col) if isinstance(col, str) else col
            norm.append((max(0.0, min(1.0, float(pos))), rgb))
        self.stops = sorted(norm, key=lambda s: s[0])
        self.cyclic = cyclic
        self.label = label

    @classmethod
    def from_config(cls, spec: dict) -> "Palette":
        stops = [(s["pos"], s["color"]) for s in spec["stops"]]
        return cls(stops, bool(spec.get("cyclic", False)), spec.get("label"))

    def to_config(self) -> dict:
        out: dict = {"stops": [{"pos": p, "color": to_hex(c)} for p, c in self.stops]}
        if self.cyclic:
            out["cyclic"] = True
        if self.label:
            out["label"] = self.label
        return out

    def sample(self, t: float) -> RGB:
        """Colour at position t. t is wrapped for cyclic palettes, clamped otherwise."""
        stops = self.stops
        if len(stops) == 1:
            return stops[0][1]
        t = t % 1.0 if self.cyclic else max(0.0, min(1.0, t))

        if self.cyclic:
            # Treat the span from the last stop back to the first as one more segment.
            first_p, first_c = stops[0]
            last_p, last_c = stops[-1]
            if t < first_p:
                span = first_p + (1.0 - last_p)
                return mix(last_c, first_c, 0.0 if span == 0 else (t + 1.0 - last_p) / span)
            if t >= last_p:
                span = first_p + (1.0 - last_p)
                return mix(last_c, first_c, 0.0 if span == 0 else (t - last_p) / span)
        else:
            if t <= stops[0][0]:
                return stops[0][1]
            if t >= stops[-1][0]:
                return stops[-1][1]

        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= t <= p1:
                span = p1 - p0
                return c0 if span == 0 else mix(c0, c1, (t - p0) / span)
        return stops[-1][1]

    def ramp(self, n: int) -> list[RGB]:
        """n evenly spaced samples. Cyclic palettes skip the duplicate endpoint."""
        if n <= 1:
            return [self.sample(0.0)]
        div = n if self.cyclic else n - 1
        return [self.sample(i / div) for i in range(n)]


def resolve_palette(name: str | None, extra: dict[str, Palette] | None = None) -> Palette:
    """Look a palette up by name in the config's palettes, then the built-in corpus."""
    from .palettes import BUILTIN  # local import: palettes imports Palette from here

    if name:
        if extra and name in extra:
            return extra[name]
        if name in BUILTIN:
            return BUILTIN[name]
    return BUILTIN["rainbow"]
