"""A single frame of LED state: 13 key pixels, 8 underglow pixels, 3 status duties.

Frames are the only thing the renderer sends to the device, and every layer (base
colours, effects, transient flashes) is expressed as a frame so layers can be composited
with plain arithmetic instead of special cases.
"""
from __future__ import annotations

from .color import RGB, mix, parse_hex, to_hex
from .layout import KEY_N, STATUS_N, UNDERGLOW_N

BLACK: RGB = (0.0, 0.0, 0.0)


class Frame:
    __slots__ = ("keys", "under", "status")

    def __init__(self, keys: list[RGB] | None = None, under: list[RGB] | None = None,
                 status: list[int] | None = None):
        self.keys: list[RGB] = keys if keys is not None else [BLACK] * KEY_N
        self.under: list[RGB] = under if under is not None else [BLACK] * UNDERGLOW_N
        self.status: list[int] = status if status is not None else [0] * STATUS_N

    @classmethod
    def blank(cls) -> "Frame":
        return cls()

    def copy(self) -> "Frame":
        return Frame(list(self.keys), list(self.under), list(self.status))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Frame):
            return NotImplemented
        return (self.keys == other.keys and self.under == other.under
                and self.status == other.status)

    # --- compositing --------------------------------------------------------

    def composite(self, over: "Frame", mode: str = "replace", target: str = "all",
                  amount: float = 1.0) -> "Frame":
        """Return self with `over` composited on top, restricted to `target`.

        `amount` cross-fades the result back toward self, which is what a fading flash or
        a dimmed idle state uses.
        """
        out = self.copy()
        if amount <= 0.0:
            return out
        if target in ("keys", "all"):
            out.keys = [_blend(b, o, mode, amount) for b, o in zip(out.keys, over.keys)]
        if target in ("underglow", "all"):
            out.under = [_blend(b, o, mode, amount) for b, o in zip(out.under, over.under)]
        return out

    # --- serialisation ------------------------------------------------------

    def to_hex(self) -> dict:
        return {
            "keys": [to_hex(c) for c in self.keys],
            "underglow": [to_hex(c) for c in self.under],
            "status": list(self.status),
        }

    @classmethod
    def from_hex(cls, data: dict) -> "Frame":
        keys = [parse_hex(c) for c in data.get("keys", [])][:KEY_N]
        under = [parse_hex(c) for c in data.get("underglow", [])][:UNDERGLOW_N]
        status = [max(0, min(255, int(v))) for v in data.get("status", [])][:STATUS_N]
        keys += [BLACK] * (KEY_N - len(keys))
        under += [BLACK] * (UNDERGLOW_N - len(under))
        status += [0] * (STATUS_N - len(status))
        return cls(keys, under, status)


def _blend(base: RGB, top: RGB, mode: str, amount: float) -> RGB:
    if mode == "replace":
        result = top
    elif mode == "multiply":
        result = tuple(b * t for b, t in zip(base, top))  # type: ignore[assignment]
    elif mode == "screen":
        result = tuple(1.0 - (1.0 - b) * (1.0 - t) for b, t in zip(base, top))  # type: ignore[assignment]
    elif mode == "overlay":
        result = tuple(  # type: ignore[assignment]
            2 * b * t if b < 0.5 else 1.0 - 2 * (1.0 - b) * (1.0 - t)
            for b, t in zip(base, top)
        )
    else:
        result = top
    return result if amount >= 1.0 else mix(base, result, amount)
