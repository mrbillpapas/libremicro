"""Resolving a trigger to a binding, and running it.

Resolution order matters and is easy to get subtly wrong, so it's stated once here and used
by both `resolve` and `is_bound`:

  keys      active mode's `keys[]` override for that index, else the profile's `keys[]`
  encoder   active mode's `encoder`, else the profile's `encoder`
  touch/rear  the profile's `touch` / `rear` (modes don't override these)

`is_bound` MUST agree with `resolve` exactly. The recogniser asks `is_bound` to decide
whether to fire `press` immediately or defer it pending a possible double-tap, so a
disagreement shows up as a key that feels laggy or fires twice — a horrible bug to chase.
They're implemented in terms of the same lookup for that reason.

Mode and profile switching live here rather than in `actions.py` because they're state
transitions in the running daemon, not external side effects.
"""
from __future__ import annotations

import sys
import threading
import time

from . import events
from .actions import Actions, Context, Result
from .events import Trigger

FAIL_FLASH = "ff2200"


class Dispatcher:
    def __init__(self, daemon):
        self.d = daemon
        self._lock = threading.RLock()
        self._mode: str | None = None
        self._mode_deadline: float | None = None
        # One clock for the whole dispatcher. `feed` and `tick` accept an injected `now`, so
        # anything else reading time.monotonic() directly would be comparing two different
        # clocks — which silently broke mode timeouts and is untestable.
        self._now = time.monotonic()
        self.actions = Actions(on_profile=self.switch_profile,
                               on_reload=self._reload,
                               # getattr, not a bare attribute: the test fakes stand in for the
                               # daemon and only implement what they exercise.
                               on_release=getattr(self.d, "release_device", None),
                               on_cheat_sheet=self._cheat_sheet,
                               volume_step=int(self.cfg.device.get("volume_step", 3)),
                               # "coarse" everywhere: schema.json and actions.py agree, and this
                               # default silently being "fine" is what cost the macOS slider.
                               volume_mode=str(self.cfg.device.get("volume_mode", "coarse")),
                               on_level=self._show_level)
        self.recognizer = events.Recognizer(
            emit=self.handle, is_bound=self.is_bound,
            hold_ms=int(self.cfg.device.get("hold_ms", events.DEFAULT_HOLD_MS)),
            double_ms=int(self.cfg.device.get("double_ms", events.DEFAULT_DOUBLE_MS)),
        )

    def _show_level(self, fraction: float, label: str = "") -> None:
        """Put a level change on the pad. Colour is per-label so volume and brightness are
        distinguishable at a glance."""
        colour = {"volume": "3aa0ff", "brightness": "ffc04a"}.get(label, "ffffff")
        self.d.renderer.bar(fraction, colour)

    # --- context ------------------------------------------------------------

    @property
    def cfg(self):
        return self.d.cfg

    @property
    def mode(self) -> str | None:
        return self._mode

    def profile(self) -> dict:
        return self.cfg.profile()

    def modes(self) -> dict:
        return self.profile().get("modes") or {}

    def config_changed(self) -> None:
        """Called after the daemon swaps config. Drops in-flight input state so a key held
        across the change can't fire into the new bindings."""
        with self._lock:
            self._mode = None
            self._mode_deadline = None
            self.recognizer.reset()
            self.recognizer.hold_s = max(
                0.05, int(self.cfg.device.get("hold_ms", events.DEFAULT_HOLD_MS)) / 1000.0)
            self.recognizer.double_s = max(
                0.05, int(self.cfg.device.get("double_ms", events.DEFAULT_DOUBLE_MS)) / 1000.0)

    # --- resolution ---------------------------------------------------------

    def _key_spec(self, index: int) -> dict | None:
        """The `on` triggers object for a key, honouring any active mode override."""
        if self._mode:
            mode = self.modes().get(self._mode) or {}
            for k in mode.get("keys") or []:
                if k.get("index") == index and k.get("on"):
                    return k["on"]
        for k in self.profile().get("keys") or []:
            if k.get("index") == index:
                return k.get("on")
        return None

    def _encoder_spec(self) -> dict:
        if self._mode:
            mode = self.modes().get(self._mode) or {}
            if mode.get("encoder"):
                return mode["encoder"]
        return self.profile().get("encoder") or {}

    def resolve(self, control: str, index: int, kind: str) -> dict | None:
        """The binding for a trigger, or None."""
        if control == events.KEY:
            spec = self._key_spec(index)
            return (spec or {}).get(kind)
        if control == events.ENCODER:
            enc = self._encoder_spec()
            # The encoder's button is a `press`; rotation is cw/ccw.
            return enc.get(kind) if kind in (events.CW, events.CCW, events.PRESS) else None
        if control in (events.TOUCH, events.REAR):
            return (self.profile().get(control) or {}).get(kind)
        if control == events.JOYSTICK:
            # Eight independently bindable directions, each with the full set of trigger
            # kinds. A mode may override them, the same way it overrides keys.
            name = events.JOY_DIRS[index] if 0 <= index < len(events.JOY_DIRS) else None
            if name is None:
                return None
            if self._mode:
                mode_joy = (self.modes().get(self._mode) or {}).get("joystick") or {}
                if name in mode_joy:
                    return (mode_joy[name] or {}).get(kind)
            return ((self.profile().get("joystick") or {}).get(name) or {}).get(kind)
        return None

    def is_bound(self, control: str, index: int, kind: str) -> bool:
        try:
            return self.resolve(control, index, kind) is not None
        except Exception:
            # Resolution reads live config; a malformed document must not wedge input.
            return False

    def key_label(self, index: int) -> str:
        for k in self.profile().get("keys") or []:
            if k.get("index") == index:
                return k.get("label") or ""
        return ""

    # --- device events ------------------------------------------------------

    def feed(self, kind: str, args: list[str], now: float | None = None) -> None:
        """Consume one firmware event line."""
        now = now if now is not None else time.monotonic()
        parsed = events.parse_device_line(kind, args)
        if parsed is None:
            return
        op = parsed[0]
        if op == "battery":
            self.d.battery = {"percent": parsed[1], "charging": parsed[2]}
            return

        self.d.renderer.note_activity()
        with self._lock:
            self._now = now
            if op == "rotate":
                self._bump_mode_deadline(now)
                self.recognizer.rotate(parsed[1], now)
            elif op in ("down", "up", "tap"):
                _, control, index = parsed
                if control == events.ENCODER:
                    self._bump_mode_deadline(now)
                getattr(self.recognizer, op)(control, index, now)

    def tick(self, now: float | None = None) -> None:
        """Drive time-based triggers. Called from the render loop."""
        now = now if now is not None else time.monotonic()
        with self._lock:
            self._now = now
            self.recognizer.tick(now)
            if self._mode and self._mode_deadline and now >= self._mode_deadline:
                self._exit_mode()

    # --- execution ----------------------------------------------------------

    def handle(self, trigger: Trigger) -> Result:
        """Run the binding for a recognised trigger."""
        binding = self.resolve(trigger.control, trigger.index, trigger.kind)
        if not binding:
            return Result(False, "nothing bound")

        # Mode and profile are state transitions, handled before the generic executor.
        if "mode" in binding:
            return self._activate_mode(binding["mode"], trigger, binding.get("flash"))
        if "profile" in binding:
            return self._switch_profile_binding(binding, trigger)

        ctx = Context(
            control=trigger.control, index=trigger.index, kind=trigger.kind,
            label=self.key_label(trigger.index) if trigger.control == events.KEY else "",
            profile=self.cfg.active_profile_name, mode=self._mode or "",
        )
        result = self.actions.run(binding, ctx)
        self._feedback(trigger, binding, result)
        return result

    def _feedback(self, trigger: Trigger, binding: dict, result: Result) -> None:
        """Visual confirmation. A failed binding flashes red whether or not it asked to
        flash, because a key that appears to do nothing is indistinguishable from a broken
        daemon."""
        if trigger.control != events.KEY:
            return
        if result.ok:
            colour = binding.get("flash")
            if colour:
                self.d.renderer.flash(trigger.index, colour)
        else:
            self.d.renderer.flash(trigger.index, FAIL_FLASH, seconds=0.5)
            print(f"libremicro: {trigger.control} {trigger.index} {trigger.kind}: "
                  f"{result.detail}", file=sys.stderr, flush=True)

    # --- modes --------------------------------------------------------------

    def _activate_mode(self, name: str, trigger: Trigger, flash: str | None) -> Result:
        modes = self.modes()
        if name not in modes:
            self.d.renderer.flash(trigger.index, FAIL_FLASH, seconds=0.5)
            return Result(False, f"no mode named {name!r}")

        spec = modes[name] or {}
        with self._lock:
            # Pressing an active mode's key again leaves it — a toggle is what people expect
            # from a mode key, and it avoids being stuck when the timeout is disabled.
            if self._mode == name:
                self._exit_mode()
                self.d.renderer.flash(trigger.index, flash or spec.get("flash") or "ffffff")
                return Result(True, f"left mode {name}")

            self._mode = name
            timeout = spec.get("timeout_s")
            self._mode_deadline = (self._now + float(timeout)) if timeout else None
            self.recognizer.reset()

        self.d.renderer.set_mode(name)
        self.d.renderer.flash(trigger.index, flash or spec.get("flash") or "ffffff")
        self._refresh_cheat_sheet()
        return Result(True, f"mode {name}")

    def _exit_mode(self) -> None:
        self._mode = None
        self._mode_deadline = None
        self.recognizer.reset()
        self.d.renderer.set_mode(None)
        self._refresh_cheat_sheet()

    def _refresh_cheat_sheet(self) -> None:
        """A visible cheat sheet showing the previous mode's bindings is worse than none, so
        every mode and profile change re-renders it — but only if it's actually up."""
        sheet = getattr(self.d, "cheat_sheet", None)
        if sheet is not None:
            sheet.refresh()

    def _bump_mode_deadline(self, now: float) -> None:
        """Encoder activity keeps a timed mode alive — the timeout exists to stop you being
        stranded in desk mode, not to cut you off mid-adjustment."""
        if not self._mode:
            return
        spec = self.modes().get(self._mode) or {}
        timeout = spec.get("timeout_s")
        if timeout:
            self._mode_deadline = now + float(timeout)

    # --- profiles -----------------------------------------------------------

    def _switch_profile_binding(self, binding: dict, trigger: Trigger) -> Result:
        target = binding.get("profile") or "next"
        result = self.switch_profile(target)
        self._feedback(trigger, binding, result)
        return result

    def switch_profile(self, target: str) -> Result:
        names = self.cfg.profile_names
        if not names:
            return Result(False, "no profiles")
        if target in ("next", "prev"):
            try:
                i = names.index(self.cfg.active_profile_name)
            except ValueError:
                i = 0
            i = (i + (1 if target == "next" else -1)) % len(names)
            target = names[i]
        elif target not in names:
            return Result(False, f"no profile named {target!r}")

        with self._lock:
            self._exit_mode()
        self.d.renderer.set_profile(target)
        print(f"libremicro: profile -> {target}", flush=True)
        self._refresh_cheat_sheet()
        return Result(True, target)

    def _reload(self) -> bool:
        return self.d.reload_config()

    def _cheat_sheet(self, what: str) -> bool:
        """`toggle` / `show` / `hide`, via the daemon's one CheatSheet. getattr because the test
        fakes stand in for the daemon and only implement what they exercise."""
        sheet = getattr(self.d, "cheat_sheet", None)
        if sheet is None:
            return False
        return bool({"toggle": sheet.toggle, "show": sheet.show, "hide": sheet.hide}[what]())
