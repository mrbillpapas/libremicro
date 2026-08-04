"""Built-in palette corpus.

The naming and spirit follow WLED's palette set (itself inherited from FastLED and
cpt-city), so gradients people already know translate over and WLED custom-palette JSON
imports cleanly. `import_wled` handles WLED's flat [pos, r, g, b, ...] form where pos is
0..255.

These are stop lists rather than 256-entry tables because sampling is perceptual
(see color.Palette) — a handful of stops produces a smoother ramp than a baked table.
"""
from __future__ import annotations

from .color import Palette

BUILTIN: dict[str, Palette] = {
    "rainbow": Palette(
        [(0.00, "ff0000"), (0.17, "ffff00"), (0.33, "00ff00"), (0.50, "00ffff"),
         (0.67, "0000ff"), (0.83, "ff00ff")],
        cyclic=True, label="Rainbow"),

    "sunset": Palette(
        [(0.00, "2b0a3d"), (0.45, "d1495b"), (0.75, "ff9505"), (1.00, "ffd60a")],
        label="Sunset"),

    "ocean": Palette(
        [(0.00, "03045e"), (0.40, "0077b6"), (0.75, "00b4d8"), (1.00, "caf0f8")],
        label="Ocean"),

    "forest": Palette(
        [(0.00, "081c15"), (0.40, "1b4332"), (0.70, "40916c"), (1.00, "95d5b2")],
        label="Forest"),

    "lava": Palette(
        [(0.00, "1a0000"), (0.35, "8c1c03"), (0.70, "e85d04"), (1.00, "ffea00")],
        label="Lava"),

    "fire": Palette(
        [(0.00, "000000"), (0.25, "8b0000"), (0.60, "ff4500"), (0.85, "ffa500"),
         (1.00, "ffffcc")],
        label="Fire"),

    "ice": Palette(
        [(0.00, "0b1d3a"), (0.45, "2a6f97"), (0.80, "89c2d9"), (1.00, "ffffff")],
        label="Ice"),

    "aurora": Palette(
        [(0.00, "011627"), (0.30, "1b998b"), (0.55, "39ff14"), (0.80, "5f0f8f"),
         (1.00, "c084fc")],
        cyclic=True, label="Aurora"),

    "party": Palette(
        [(0.00, "ff006e"), (0.25, "fb5607"), (0.50, "ffbe0b"), (0.75, "8338ec"),
         (1.00, "3a86ff")],
        cyclic=True, label="Party"),

    "candy": Palette(
        [(0.00, "ff70a6"), (0.33, "ff9770"), (0.66, "ffd670"), (1.00, "70d6ff")],
        cyclic=True, label="Candy"),

    "pastel": Palette(
        [(0.00, "cdb4db"), (0.25, "ffc8dd"), (0.50, "bde0fe"), (0.75, "a2d2ff"),
         (1.00, "d0f4de")],
        cyclic=True, label="Pastel"),

    "heat": Palette(
        [(0.00, "0000ff"), (0.30, "00ffff"), (0.55, "00ff00"), (0.78, "ffff00"),
         (1.00, "ff0000")],
        label="Heat"),

    "cloud": Palette(
        [(0.00, "1e3a5f"), (0.50, "6d9dc5"), (1.00, "eef4f8")],
        label="Cloud"),

    "magenta": Palette(
        [(0.00, "2d00f7"), (0.50, "bc00dd"), (1.00, "f20089")],
        label="Magenta"),

    "retro": Palette(
        [(0.00, "ff2a6d"), (0.35, "d1f7ff"), (0.70, "05d9e8"), (1.00, "005678")],
        cyclic=True, label="Retro"),

    "mono": Palette(
        [(0.00, "000000"), (1.00, "ffffff")],
        label="Mono"),
}


def import_wled(data: dict | list) -> Palette:
    """Convert a WLED custom palette into a Palette.

    WLED's palette*.json is {"palette": [pos, r, g, b, pos, r, g, b, ...]} with pos 0..255.
    A bare list is accepted too. Raises ValueError on a malformed run.
    """
    flat = data.get("palette", []) if isinstance(data, dict) else data
    if len(flat) < 4 or len(flat) % 4 != 0:
        raise ValueError("WLED palette must be a flat list of [pos, r, g, b] quadruples")
    stops: list[tuple[float, tuple[float, float, float]]] = []
    for i in range(0, len(flat), 4):
        pos, r, g, b = flat[i:i + 4]
        stops.append((pos / 255.0, (r / 255.0, g / 255.0, b / 255.0)))
    return Palette(stops)


def catalog() -> dict[str, dict]:
    """The corpus in config/schema shape, for the web UI's GET /api/palettes."""
    return {name: p.to_config() for name, p in BUILTIN.items()}
