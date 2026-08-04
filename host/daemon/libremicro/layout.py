"""Physical geometry of the CM2's LEDs, and the index mappings that go with it.

Three separate numbering schemes exist and conflating them is the easiest way to get
lighting wrong:

  strip index   0..12 for per-key LEDs (GPIO 7), 0..7 for underglow (GPIO 6). This is
                what the firmware's `k <i>` / `u <i>` commands take — contiguous, because
                a WS2812 chain has no gaps.
  matrix index  4*row + col over a 4x4 scan, so 0..15 with THREE UNPOPULATED SLOTS
                (docs/HARDWARE.md). This is what a naive key-scan event would report.
  logical index 0..12, the stable identity used in config (`keys[].index`) and in the
                web UI. Assigned row-major over populated slots only.

`logical` is the config's contract. Strip and matrix indices are device details that get
translated at the edges. See `docs/HARDWARE.md` — the strip-to-physical mappings are NOT
yet verified on hardware, so every mapping here is overridable from config and the
defaults are marked provisional.
"""
from __future__ import annotations

from typing import Callable, Sequence

KEY_ROWS: tuple[int, ...] = (2, 4, 4, 3)
KEY_N = sum(KEY_ROWS)          # 13
UNDERGLOW_N = 8
STATUS_N = 3
MATRIX_COLS = 4

#: Underglow is a 3x3 grid with no centre LED. Ring order, clockwise from top-left.
#: PROVISIONAL: the real strip order and direction are unverified.
UNDERGLOW_RING: tuple[tuple[int, int], ...] = (
    (0, 0), (1, 0), (2, 0),
    (2, 1),
    (2, 2), (1, 2), (0, 2),
    (0, 1),
)


def logical_to_rowcol(idx: int) -> tuple[int, int]:
    """Logical key index -> (row, position within that row)."""
    if not 0 <= idx < KEY_N:
        raise IndexError(f"logical key index out of range: {idx}")
    n = idx
    for row, count in enumerate(KEY_ROWS):
        if n < count:
            return row, n
        n -= count
    raise AssertionError("unreachable")


def rowcol_to_logical(row: int, pos: int) -> int:
    """(row, position within row) -> logical key index."""
    if not 0 <= row < len(KEY_ROWS) or not 0 <= pos < KEY_ROWS[row]:
        raise IndexError(f"no key at row {row} position {pos}")
    return sum(KEY_ROWS[:row]) + pos


def key_xy(idx: int) -> tuple[float, float]:
    """Normalised (x, y) in 0..1 for a logical key index, rows centred horizontally.

    Spatial effects (gradients, ripples, wipes) work in this space so they don't have to
    know about the ragged 2/4/4/3 row widths.
    """
    row, pos = logical_to_rowcol(idx)
    width = KEY_ROWS[row]
    widest = max(KEY_ROWS)
    # Centre narrow rows against the widest row.
    x_slot = pos + (widest - width) / 2.0
    x = x_slot / (widest - 1) if widest > 1 else 0.5
    y = row / (len(KEY_ROWS) - 1) if len(KEY_ROWS) > 1 else 0.5
    return x, y


def underglow_xy(idx: int) -> tuple[float, float]:
    """Normalised (x, y) in 0..1 for an underglow ring position."""
    gx, gy = UNDERGLOW_RING[idx % UNDERGLOW_N]
    return gx / 2.0, gy / 2.0


def underglow_angle(idx: int) -> float:
    """Position around the ring as 0..1, for ring-direction effects."""
    return (idx % UNDERGLOW_N) / UNDERGLOW_N


def _bijection(n: int, positions: Sequence[Sequence[int] | None],
               resolve: "Callable[[Sequence[int]], int]") -> list[int]:
    """Build a total strip-index -> slot mapping from a possibly partial config list.

    Explicitly mapped strip indices win. Whatever is left over is filled in ascending
    order, which keeps the result a bijection (every LED addressable exactly once) even
    when the identify sweep has only confirmed some of them. Unparseable or duplicate
    entries are dropped rather than raising — a bad mapping shouldn't stop the daemon, and
    `verified` stays false so the UI keeps nagging.
    """
    explicit: dict[int, int] = {}
    for strip_i, pos in enumerate(positions[:n]):
        if pos is None:
            continue
        try:
            slot = resolve(pos)
        except (IndexError, TypeError, ValueError):
            continue
        if not 0 <= slot < n or slot in explicit.values():
            continue
        explicit[strip_i] = slot

    leftover = iter([s for s in range(n) if s not in explicit.values()])
    return [explicit.get(strip_i) if strip_i in explicit else next(leftover)
            for strip_i in range(n)]  # type: ignore[misc]


def _invert(mapping: list[int]) -> list[int]:
    out = [0] * len(mapping)
    for src, dst in enumerate(mapping):
        out[dst] = src
    return out


class Layout:
    """Resolved geometry, including any hardware mappings supplied by config.

    `key_positions` / `underglow_positions` come from config and map STRIP index to a
    physical position. When absent (the common case today, since nothing is verified) the
    identity mapping is used and `verified` stays False so the UI can say so.
    """

    def __init__(self, spec: dict | None = None):
        spec = spec or {}
        rows = spec.get("key_rows") or list(KEY_ROWS)
        self.key_rows: tuple[int, ...] = tuple(int(r) for r in rows)
        self.verified: bool = bool(spec.get("verified", False))
        self._key_pos: list[Sequence[int] | None] = list(spec.get("key_positions") or [])
        self._amb_pos: list[Sequence[int] | None] = list(spec.get("underglow_positions") or [])

        self.strip_to_logical = _bijection(
            KEY_N, self._key_pos, lambda p: rowcol_to_logical(int(p[0]), int(p[1])))
        self.logical_to_strip = _invert(self.strip_to_logical)

        self.strip_to_ring = _bijection(
            UNDERGLOW_N, self._amb_pos, lambda p: UNDERGLOW_RING.index((int(p[0]), int(p[1]))))
        self.ring_to_strip = _invert(self.strip_to_ring)

    @property
    def key_count(self) -> int:
        return sum(self.key_rows)

    def to_config(self) -> dict:
        out: dict = {"key_rows": list(self.key_rows), "verified": self.verified}
        if self._key_pos:
            out["key_positions"] = [list(p) if p is not None else None for p in self._key_pos]
        if self._amb_pos:
            out["underglow_positions"] = [list(p) if p is not None else None for p in self._amb_pos]
        return out
