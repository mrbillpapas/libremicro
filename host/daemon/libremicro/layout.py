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

#: Which columns of the 4x4 physical grid each row's keys actually occupy — CONFIRMED
#: from the faceplate (docs/HARDWARE.md). The three empty slots are taken by non-key
#: controls, which is why the short rows sit where they do:
#:   row 0 col 0 = rotary encoder,   row 0 col 3 = joystick
#:   row 3 col 0 = capacitive touch pad (with the 3 status LEDs beside it)
KEY_GRID_COLS: tuple[tuple[int, ...], ...] = (
    (1, 2),
    (0, 1, 2, 3),
    (0, 1, 2, 3),
    (1, 2, 3),
)

#: Groups of logical keys that sit under ONE physical keycap. The bottom row's wide cap
#: covers two independent switches with two independent LEDs — so the pad has 13 switches
#: but only 12 caps. A user can't reliably choose which half they press, so binding the
#: two to different actions is a footgun; the UI draws them as one cap for that reason.
SHARED_KEYCAPS: tuple[tuple[int, ...], ...] = ((10, 11),)

#: Non-key controls, as (row, col) in the same 4x4 grid. Not addressable as key LEDs;
#: recorded so the UI can draw an accurate board and so nothing tries to light them.
FEATURES: dict[str, tuple[int, int]] = {
    "encoder": (0, 0),
    "joystick": (0, 3),
    "touch": (3, 0),
}

#: Underglow is a 3x3 grid with no centre LED, all eight the same physical size, evenly
#: spaced around the square — CONFIRMED. Ring order here is clockwise from top-left.
#: STILL PROVISIONAL: which strip index lands on which position, and the wiring direction.
UNDERGLOW_RING: tuple[tuple[int, int], ...] = (
    (0, 0), (1, 0), (2, 0),
    (2, 1),
    (2, 2), (1, 2), (0, 2),
    (0, 1),
)


#: Strip index -> (row, ordinal within row) for the per-key chain on GPIO 7.
#: CONFIRMED on hardware by identify sweep. The strip is wired as a serpentine starting at
#: the bottom-right and snaking upward: row 3 right-to-left, row 2 left-to-right, row 1
#: right-to-left, row 0 left-to-right. Every consecutive index is physically adjacent.
#:
#: This is a property of the board, not of the user — every Creator Micro 2 is wired the
#: same way — so it ships as a default rather than something each owner rediscovers.
#: `layout.key_positions` in config still overrides it, for a future hardware revision or a
#: unit that turns out to differ.
DEFAULT_KEY_POSITIONS: tuple[tuple[int, int], ...] = (
    (3, 2), (3, 1), (3, 0),
    (2, 0), (2, 1), (2, 2), (2, 3),
    (1, 3), (1, 2), (1, 1), (1, 0),
    (0, 0), (0, 1),
)

#: Strip index -> (gx, gy) for the underglow chain on GPIO 6. CONFIRMED on hardware.
#: Also starts at the bottom-right — consistent with a single wiring entry point — and runs
#: continuously around the ring: along the bottom, up the left, across the top, down the
#: right. Overridable via `layout.underglow_positions`.
DEFAULT_UNDERGLOW_POSITIONS: tuple[tuple[int, int], ...] = (
    (2, 2), (1, 2), (0, 2),
    (0, 1),
    (0, 0), (1, 0), (2, 0),
    (2, 1),
)


def grid_col(row: int, ordinal: int) -> int:
    """(row, ordinal within row) -> column in the 4x4 physical grid.

    Config stores positions as [row, ordinal] so the format didn't have to change when the
    faceplate confirmed the alignment; this is the translation to real geometry.
    """
    try:
        return KEY_GRID_COLS[row][ordinal]
    except (IndexError, TypeError):
        raise IndexError(f"no key at row {row} ordinal {ordinal}") from None


def shared_cap_for(idx: int) -> tuple[int, ...] | None:
    """The group of logical keys sharing a cap with `idx`, or None if it has its own."""
    for group in SHARED_KEYCAPS:
        if idx in group:
            return group
    return None


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
    """Normalised (x, y) in 0..1 for a logical key index, in true physical position.

    Spatial effects (gradients, ripples, wipes) work in this space so they don't have to
    know about the ragged 2/4/4/3 row widths or which grid columns each row occupies.
    """
    row, ordinal = logical_to_rowcol(idx)
    col = grid_col(row, ordinal)
    x = col / (MATRIX_COLS - 1)
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
    physical position. When absent, the hardware-confirmed defaults above are used — the
    wiring is the same on every Creator Micro 2, so a user shouldn't have to rediscover it.
    Config is for overriding a unit that differs, not for supplying what we already know.
    """

    def __init__(self, spec: dict | None = None):
        spec = spec or {}
        rows = spec.get("key_rows") or list(KEY_ROWS)
        self.key_rows: tuple[int, ...] = tuple(int(r) for r in rows)
        self._key_pos: list[Sequence[int] | None] = list(
            spec.get("key_positions") or [list(p) for p in DEFAULT_KEY_POSITIONS])
        self._amb_pos: list[Sequence[int] | None] = list(
            spec.get("underglow_positions") or [list(p) for p in DEFAULT_UNDERGLOW_POSITIONS])
        # Defaults are confirmed, so verification is the default state. An explicit false in
        # config still means "I overrode this and haven't checked it".
        self.verified: bool = bool(spec.get("verified", True))

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
