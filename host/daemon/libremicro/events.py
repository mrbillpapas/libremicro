"""Turning raw device events into bound triggers.

The firmware reports physical facts — `key 3 down`, `key 3 up`, `enc cw`. Config binds
*trigger kinds*: press, release, hold, double. Getting from one to the other is not a
rename, because the kinds interact:

  - If a control has a `double` binding, `press` cannot fire immediately — we don't yet
    know whether a second tap is coming. So press is deferred by the double-tap window.
    When nothing is bound to `double`, press fires immediately, because latency on a
    launcher key is the thing you notice most.
  - If `hold` fires, `press` must not also fire on release. Otherwise every long press
    does two things.
  - `release` is independent and always fires if bound.

The recogniser therefore has to know what's bound, which is why it takes an `is_bound`
callback rather than emitting everything and letting the dispatcher sort it out.

Nothing here touches the device or runs actions — it's a pure state machine over a clock,
so the awkward timing cases are testable without hardware.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

# Control kinds, matching the config keys they bind under.
KEY = "key"
ENCODER = "encoder"
TOUCH = "touch"
REAR = "rear"
JOYSTICK = "joystick"

#: Joystick directions, in the order the firmware names them. The index of a direction here
#: is its `Trigger.index`, so each of the eight directions binds independently — the same
#: shape as a key, which means press/release/hold/double all work on it for free.
JOY_DIRS = ("e", "ne", "n", "nw", "w", "sw", "s", "se")

PRESS, RELEASE, HOLD, DOUBLE = "press", "release", "hold", "double"
CW, CCW = "cw", "ccw"

DEFAULT_HOLD_MS = 450
DEFAULT_DOUBLE_MS = 280


@dataclass(frozen=True)
class Trigger:
    """A bound thing that happened. `index` is the logical key index, or 0 elsewhere."""
    control: str
    index: int
    kind: str

    @property
    def key(self) -> tuple[str, int]:
        return (self.control, self.index)


class Recognizer:
    """Raw down/up events in, `Trigger`s out.

    `emit` is called for each recognised trigger. `is_bound(control, index, kind)` must
    report whether config has a binding for that combination — it decides whether press can
    fire immediately and whether hold is even watched for.

    `tick()` must be called regularly (the render loop does it) for hold and deferred press
    to fire on time; everything else is edge-driven.
    """

    def __init__(self, emit: Callable[[Trigger], None],
                 is_bound: Callable[[str, int, str], bool],
                 hold_ms: int = DEFAULT_HOLD_MS,
                 double_ms: int = DEFAULT_DOUBLE_MS):
        self.emit = emit
        self.is_bound = is_bound
        self.hold_s = max(0.05, hold_ms / 1000.0)
        self.double_s = max(0.05, double_ms / 1000.0)
        # (control, index) -> state
        self._down_at: dict[tuple[str, int], float] = {}
        self._hold_fired: set[tuple[str, int]] = set()
        self._double_fired: set[tuple[str, int]] = set()
        self._pending_press: dict[tuple[str, int], float] = {}   # -> deadline
        self._last_up_at: dict[tuple[str, int], float] = {}

    # --- inputs -------------------------------------------------------------

    def down(self, control: str, index: int, now: float) -> None:
        ck = (control, index)
        self._down_at[ck] = now
        self._hold_fired.discard(ck)

        # A second tap inside the window is a double — and it cancels the deferred press,
        # so a double-tap does the double thing only, not press-then-double.
        last_up = self._last_up_at.get(ck)
        if (last_up is not None and now - last_up <= self.double_s
                and self.is_bound(control, index, DOUBLE)):
            self._pending_press.pop(ck, None)
            self._last_up_at.pop(ck, None)
            # Remember it so the release that ends this second tap doesn't queue a press —
            # otherwise every double-tap is followed by a stray single.
            self._double_fired.add(ck)
            self._fire(control, index, DOUBLE)

    def up(self, control: str, index: int, now: float) -> None:
        ck = (control, index)
        down_at = self._down_at.pop(ck, None)
        held = ck in self._hold_fired
        self._hold_fired.discard(ck)
        doubled = ck in self._double_fired
        self._double_fired.discard(ck)

        if self.is_bound(control, index, RELEASE):
            self._fire(control, index, RELEASE)

        if held or doubled or down_at is None:
            # Hold or double already acted on this press; press must not also fire.
            return

        if self.is_bound(control, index, DOUBLE):
            # Wait out the double window before committing to press.
            self._pending_press[ck] = now + self.double_s
            self._last_up_at[ck] = now
        elif self.is_bound(control, index, PRESS):
            self._fire(control, index, PRESS)

    def tap(self, control: str, index: int, now: float) -> None:
        """A control that only reports one edge (the touch pad, the rear button)."""
        self.down(control, index, now)
        self.up(control, index, now)

    def rotate(self, direction: str, now: float) -> None:
        """Encoder detent. Fires immediately — a dial must not feel laggy."""
        if direction in (CW, CCW) and self.is_bound(ENCODER, 0, direction):
            self._fire(ENCODER, 0, direction)

    # --- clock --------------------------------------------------------------

    def tick(self, now: float) -> None:
        for ck, deadline in list(self._pending_press.items()):
            if now >= deadline:
                del self._pending_press[ck]
                control, index = ck
                if self.is_bound(control, index, PRESS):
                    self._fire(control, index, PRESS)

        for ck, down_at in list(self._down_at.items()):
            if ck in self._hold_fired:
                continue
            control, index = ck
            if now - down_at >= self.hold_s and self.is_bound(control, index, HOLD):
                self._hold_fired.add(ck)
                self._fire(control, index, HOLD)

    # --- state --------------------------------------------------------------

    def is_down(self, control: str, index: int) -> bool:
        return (control, index) in self._down_at

    def reset(self) -> None:
        """Drop all in-flight state — used when the profile or device changes so a key
        that was held across the switch can't fire into the new config."""
        self._down_at.clear()
        self._hold_fired.clear()
        self._double_fired.clear()
        self._pending_press.clear()
        self._last_up_at.clear()

    def _fire(self, control: str, index: int, kind: str) -> None:
        self.emit(Trigger(control, index, kind))


def parse_device_line(kind: str, args: list[str]) -> tuple[str, ...] | None:
    """Normalise a firmware event line into a recogniser call.

    Returns a tuple describing what to do, or None if the line isn't an input event:
      ("down", control, index) / ("up", control, index) / ("tap", control, index)
      ("rotate", direction)
      ("battery", percent, charging)

    The grammar is in docs/PROTOCOL.md. Malformed lines are dropped rather than raising —
    a garbled byte on the wire must not take the daemon down.
    """
    try:
        if kind == "key" and len(args) >= 2:
            idx = int(args[0])
            if args[1] == "down":
                return ("down", KEY, idx)
            if args[1] == "up":
                return ("up", KEY, idx)
            return None

        if kind == "enc" and args:
            what = args[0]
            if what in (CW, CCW):
                return ("rotate", what)
            if what == "press":
                return ("down", ENCODER, 0)
            if what == "release":
                return ("up", ENCODER, 0)
            return None

        if kind == "joy" and len(args) >= 2:
            try:
                idx = JOY_DIRS.index(args[0])
            except ValueError:
                return None
            if args[1] in ("down", "up"):
                return (args[1], JOYSTICK, idx)
            return None

        if kind == "touch":
            # Two forms accepted: bare `touch` (a tap) and `touch down|up`.
            if args and args[0] in ("down", "up"):
                return (args[0], TOUCH, 0)
            return ("tap", TOUCH, 0)

        if kind == "rear":
            if args and args[0] in ("down", "up"):
                return (args[0], REAR, 0)
            return ("tap", REAR, 0)

        if kind == "batt" and args:
            return ("battery", int(args[0]), bool(len(args) > 1 and args[1] == "1"))
    except (ValueError, IndexError):
        return None
    return None


def bound_kinds(spec: dict | None) -> Iterable[str]:
    """The trigger kinds present in a config `triggers` object."""
    if not spec:
        return ()
    return tuple(k for k in (PRESS, RELEASE, HOLD, DOUBLE) if spec.get(k))
