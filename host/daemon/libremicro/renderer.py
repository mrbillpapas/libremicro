"""The render loop: composite lighting layers and stream frames to the device.

Layers, bottom to top:

  base       per-key colours and underglow colour from the active profile and mode
  effect     the configured animated effect, composited with its own blend mode
  pulses     long-lived per-key attention pulses (notification watchers)
  flashes    short-lived per-key confirmations (a binding fired, a mode activated)
  preview    a web UI live preview, which overrides everything while active

Idle dimming is applied last, as a brightness scale rather than a colour change, so
recovering from idle is one `bright` command instead of a full frame.

The firmware also enforces its own idle timeouts (it has to — it must work untethered),
so this is the tethered fast path, not the authority.
"""
from __future__ import annotations

import threading
import time

from . import effects
from .color import RGB, mix, parse_hex, scale_lightness
from .config import Config
from .frame import BLACK, Frame
from .layout import KEY_N, STATUS_N, UNDERGLOW_N
from .transport import Link

_FLASH_SECONDS = 0.35


class Renderer:
    def __init__(self, link: Link, config: Config, on_tick=None):
        self.link = link
        self.cfg = config
        # Called once per frame. The input recogniser uses it to fire hold and deferred-press
        # on time, which needs frame resolution rather than the daemon's idle loop.
        self.on_tick = on_tick
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._mode: str | None = None
        self._effect: effects.Effect | None = None
        self._effect_t0 = time.monotonic()
        self._base = Frame.blank()

        self._flashes: dict[int, tuple[float, RGB]] = {}       # logical -> (until, colour)
        self._pulses: dict[int, tuple[RGB, float]] = {}         # logical -> (colour, period)
        self._preview: Frame | None = None
        self._preview_until = 0.0
        self._preview_effect: effects.Effect | None = None
        # A transient level bar — volume, brightness, anything 0..1. Drawn on the underglow
        # ring, which is the one zone that reads as a scale rather than a set of buttons.
        self._bar: tuple[float, float, RGB] | None = None   # (until, fraction, colour)

        self._last_activity = time.monotonic()
        self._dim_level = 1.0
        self._hold_until = 0.0

        self._rebuild()

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="lm-render", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    # --- state changes ------------------------------------------------------

    def set_config(self, config: Config) -> None:
        with self._lock:
            self.cfg = config
            self._rebuild()

    def set_profile(self, name: str) -> None:
        with self._lock:
            self.cfg.doc["active_profile"] = name
            self._mode = None
            self._rebuild()

    def set_mode(self, name: str | None) -> None:
        with self._lock:
            self._mode = name
            self._rebuild()

    @property
    def mode(self) -> str | None:
        return self._mode

    def flash(self, logical_index: int, color: str | RGB,
              seconds: float = _FLASH_SECONDS) -> None:
        rgb = parse_hex(color) if isinstance(color, str) else color
        with self._lock:
            self._flashes[int(logical_index)] = (time.monotonic() + seconds, rgb)

    def pulse(self, logical_index: int, color: str | RGB | None,
              period: float = 1.4) -> None:
        """Start or stop a sustained pulse on a key. `color=None` clears it."""
        with self._lock:
            if color is None:
                self._pulses.pop(int(logical_index), None)
                return
            rgb = parse_hex(color) if isinstance(color, str) else color
            self._pulses[int(logical_index)] = (rgb, max(0.2, period))

    def bar(self, fraction: float, color: str | RGB = "ffffff",
            seconds: float = 1.6) -> None:
        """Show a transient level bar across the underglow.

        This exists because macOS shows no on-screen overlay when volume is set
        programmatically, and a dial with no feedback feels broken. The pad is sitting right
        under the user's hand, so it is a better place for the indicator than the screen —
        and with 8 segments plus a partially-lit leading segment the resolution is finer than
        the eight steps would suggest.
        """
        rgb = parse_hex(color) if isinstance(color, str) else color
        with self._lock:
            self._bar = (time.monotonic() + max(0.1, seconds),
                         max(0.0, min(1.0, float(fraction))), rgb)

    def note_activity(self) -> None:
        with self._lock:
            self._last_activity = time.monotonic()

    # --- preview (web UI) ---------------------------------------------------

    def preview_frame(self, frame: Frame, ttl: float = 5.0) -> None:
        with self._lock:
            self._preview = frame
            self._preview_effect = None
            self._preview_until = time.monotonic() + ttl

    def preview_effect(self, spec: dict, ttl: float = 300.0) -> None:
        with self._lock:
            self._preview_effect = effects.build(spec, self.cfg.palettes)
            self._preview = None
            self._effect_t0 = time.monotonic()
            self._preview_until = time.monotonic() + ttl

    def preview_stop(self) -> None:
        with self._lock:
            self._preview = None
            self._preview_effect = None
            self._preview_until = 0.0
            self._hold_until = 0.0
            self._bar = None

    def hold(self, seconds: float) -> None:
        """Stop writing frames entirely for `seconds`, leaving the strips as they are.

        The identify sweep needs this. It writes a single raw pixel by strip index, and a
        preview frame can't express that — a blank preview would still be a frame, so the
        render loop would push it and blank the pixel we just lit. Holding is the only way
        to hand the strips over to something else.
        """
        with self._lock:
            self._preview = None
            self._preview_effect = None
            self._preview_until = 0.0
            self._hold_until = time.monotonic() + max(0.0, seconds)

    @property
    def held(self) -> bool:
        return time.monotonic() < self._hold_until

    @property
    def previewing(self) -> bool:
        return (self._preview is not None or self._preview_effect is not None) \
            and time.monotonic() < self._preview_until

    # --- composition --------------------------------------------------------

    def _rebuild(self) -> None:
        """Recompute the base frame and effect from the active profile and mode."""
        profile = self.cfg.profile()
        lighting = dict(profile.get("lighting") or {})
        keys: list[RGB] = [BLACK] * KEY_N

        def apply_keys(specs: list[dict]) -> None:
            for k in specs or []:
                idx = int(k.get("index", -1))
                if 0 <= idx < KEY_N and k.get("color"):
                    keys[idx] = parse_hex(k["color"])

        apply_keys(profile.get("keys") or [])

        if self._mode:
            mode_spec = (profile.get("modes") or {}).get(self._mode) or {}
            apply_keys(mode_spec.get("keys") or [])
            lighting.update(mode_spec.get("lighting") or {})

        under_base = parse_hex(lighting["underglow"]) if lighting.get("underglow") else BLACK
        status = list(lighting.get("status_leds") or [])[:STATUS_N]
        status += [0] * (STATUS_N - len(status))

        self._base = Frame(keys, [under_base] * UNDERGLOW_N, status)
        self._effect = effects.build(lighting.get("effect"), self.cfg.palettes)
        self._effect_t0 = time.monotonic()

    def compose(self, now: float | None = None) -> Frame:
        """The frame that should be on the device right now."""
        now = now or time.monotonic()
        with self._lock:
            if self.previewing:
                if self._preview is not None:
                    return self._preview
                if self._preview_effect is not None:
                    return self._preview_effect.render(now - self._effect_t0, self.cfg.layout)

            frame = self._base.copy()

            if self._effect is not None:
                layer = self._effect.render(now - self._effect_t0, self.cfg.layout)
                frame = frame.composite(layer, self._effect.blend, self._effect.target)

            bar = self._bar
            if bar is not None:
                until, fraction, colour = bar
                if now >= until:
                    self._bar = None
                else:
                    # Fade the whole bar out over its last 0.4s so it doesn't just vanish.
                    fade = min(1.0, (until - now) / 0.4)
                    exact = fraction * UNDERGLOW_N
                    full = int(exact)
                    for i in range(UNDERGLOW_N):
                        if i < full:
                            level = 1.0
                        elif i == full:
                            level = exact - full      # the partially-lit leading segment
                        else:
                            level = 0.0
                        lit = scale_lightness(colour, level * fade)
                        # Unlit segments keep a dim trace of the colour so the bar reads as a
                        # scale with a track, not a handful of disconnected dots.
                        frame.under[i] = lit if level > 0.02 else scale_lightness(colour, 0.05 * fade)

            for idx, (colour, period) in self._pulses.items():
                if 0 <= idx < KEY_N:
                    # Never fully dark: an ambient notifier that blinks off looks broken.
                    level = 0.35 + 0.65 * (0.5 - 0.5 * _cos_cycle(now, period))
                    frame.keys[idx] = scale_lightness(colour, level)

            expired = [i for i, (until, _) in self._flashes.items() if until <= now]
            for i in expired:
                del self._flashes[i]
            for idx, (until, colour) in self._flashes.items():
                if 0 <= idx < KEY_N:
                    remaining = (until - now) / _FLASH_SECONDS
                    frame.keys[idx] = mix(frame.keys[idx], colour, max(0.0, min(1.0, remaining)))

            return frame

    # --- idle ---------------------------------------------------------------

    def _idle_scale(self, now: float) -> float:
        power = self.cfg.power
        idle = now - self._last_activity
        off_after = float(power.get("idle_off_after_s", 0) or 0)
        dim_after = float(power.get("idle_dim_after_s", 0) or 0)
        if off_after and idle >= off_after:
            return 0.0
        if dim_after and idle >= dim_after:
            dim = int(power.get("dim_brightness", 40))
            return max(0.0, min(1.0, dim / max(1, self.cfg.brightness)))
        return 1.0

    # --- loop ---------------------------------------------------------------

    def _loop(self) -> None:
        interval = 1.0 / max(1.0, self.cfg.fps)
        while not self._stop.is_set():
            started = time.monotonic()
            if self.on_tick is not None:
                try:
                    self.on_tick(started)
                except Exception:
                    # Input dispatch must not be able to stop rendering, or a bad binding
                    # would take the lights down with it.
                    pass
            try:
                self._tick(started)
            except Exception:
                # A render bug must not kill the loop; the next frame gets a fresh try.
                pass
            time.sleep(max(0.0, interval - (time.monotonic() - started)))

    def _tick(self, now: float) -> None:
        if self.held:
            return
        scale = 1.0 if self.previewing else self._idle_scale(now)
        self._dim_level = scale
        self.link.set_brightness(round(self.cfg.brightness * scale))
        if scale <= 0.0:
            self.link.send_frame(Frame.blank())
            return
        self.link.send_frame(self.compose(now))


def _cos_cycle(now: float, period: float) -> float:
    import math
    return math.cos(2 * math.pi * (now % period) / period)
