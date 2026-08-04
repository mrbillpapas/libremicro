"""Executing a binding: launch an app, fire a chord, type text, run a script, or do a
built-in action.

Two rules shape everything here.

**Nothing blocks.** These run on the input path, so a slow script must not stall the pad.
Every external command is spawned and left to run; we never wait for output. The one
exception is a bounded wait when we genuinely need an exit status to report failure.

**Failures are visible.** A binding that silently does nothing is the worst outcome — the
user can't tell a misconfigured key from a broken daemon. Every call returns a `Result`, so
the dispatcher can flash the key red and the reason reaches the log once.

Note that config can run arbitrary shell commands. That's the entire point of a macropad,
and it's also why the web UI binds to loopback only: this file is the reason that matters.
"""
from __future__ import annotations

import math
import os
import shlex
import subprocess
import sys
import time
import threading
from dataclasses import dataclass
from pathlib import Path

HOOK_DIR = Path(os.path.expanduser("~/.config/libremicro/hooks"))

#: Built-in actions with no portable implementation — a standing desk has no standard API.
#: These resolve to an executable hook the user drops in HOOK_DIR, which keeps the action
#: vocabulary stable without pretending we can talk to arbitrary hardware.
HOOK_ACTIONS = frozenset({"desk_up", "desk_down", "stand_sit"})

#: Built-ins handled by synthesising the corresponding system media key.
MEDIA_ACTIONS = {
    "mute": "mute",
    "play_pause": "play_pause", "next_track": "next_track", "prev_track": "prev_track",
    "bright_up": "brightness_up", "bright_down": "brightness_down",
}

#: Volume has two implementations with a genuine trade between them, so it's a config choice
#: rather than a decision made on the user's behalf:
#:
#:   "coarse" (default) presses the real media key, so macOS shows its own volume slider.
#:            The cost is macOS's 16-step grid, ~6.25% a press, which is chunky under a dial —
#:            but seeing the level move matters more than resolution.
#:   "fine"   sets the level directly. Any step size you like via device.volume_step, but
#:            macOS shows NO overlay for a programmatic set.
#:
#: Either way the pad shows its own bar across the underglow, so there is feedback even in
#: fine mode.
#:
#: In coarse mode the level shown on the pad is *predicted* — macOS snaps every press to its
#: 16-step grid, so the landing spot is arithmetic, not a mystery — and then trued up by one
#: real read shortly after the dial goes quiet. The previous design read the level back
#: synchronously after every detent, which both blocked the dispatch path (~60 ms of
#: osascript per detent; a fast spin queued them and the bar lagged) and raced the media key
#: itself, which is sent through an asynchronous queue and usually hadn't landed yet.
#:
#: What is NOT on offer, despite being the obvious idea: the media key with shift+option
#: held, which is how a human gets quarter steps. Measured on hardware, that does nothing for
#: a synthesised event — macOS doesn't consult held modifier state when sizing the step of an
#: injected aux event — and putting the modifiers onto the event itself latches the key, which
#: then auto-repeats and drives volume to a rail. See host/swift/lmkey.swift.
#:
#: Nor is a native overlay for fine mode: on current macOS the volume HUD is a ControlCenter
#: system banner presented only by its own media-key hot-key observer (confirmed by log
#: tracing — OSDManager, the old private-API route, no longer draws it). If the level is set
#: directly, no system UI will show it, whoever does the setting.
VOLUME_STEP_DEFAULT = 3
VOLUME_MODE_DEFAULT = "coarse"

#: macOS's media-key volume grid: 16 steps, 6.25% a press.
_VOL_GRID = 100.0 / 16.0

#: How long after the last detent the coarse-mode true-up read fires. Long enough that a
#: spinning dial coalesces into one read, short enough that a drifted prediction is corrected
#: before anyone stares at the bar wondering.
_VOL_TRUEUP_S = 0.35

_VOL_GET = 'output volume of (get volume settings)'
_VOL_MUTED = 'output muted of (get volume settings)'

#: host/swift/lmvol — CoreAudio volume helper. Reads in ~10 ms where osascript takes ~60,
#: sets the level synchronously with no AppleScript in the path, and needs no Accessibility
#: permission. Everything here falls back to osascript when it isn't built.
LMVOL_ENV = "LIBREMICRO_LMVOL"
DEFAULT_LMVOL = Path(__file__).resolve().parents[2] / "swift" / "lmvol"
LMVOL_BUILD_HINT = "build it with: cd host/swift && swiftc -O -o lmvol lmvol.swift"


def lmvol_path() -> Path | None:
    """Where the built lmvol helper is, or None. Checked per call, like keys.helper_path,
    so building it while the daemon runs just starts working."""
    override = os.environ.get(LMVOL_ENV)
    if override is not None:
        p = Path(override).expanduser()
    else:
        p = DEFAULT_LMVOL
    try:
        return p if (p.is_file() and os.access(p, os.X_OK)) else None
    except OSError:
        return None

_warned: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    if key not in _warned:
        _warned.add(key)
        print(f"libremicro: {message}", file=sys.stderr, flush=True)


@dataclass
class Result:
    ok: bool
    detail: str = ""

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class Context:
    """What was actuated. Passed to scripts as LM_* environment variables so one script can
    serve several bindings and still know which one fired."""
    control: str = "key"
    index: int = 0
    kind: str = "press"
    label: str = ""
    profile: str = ""
    mode: str = ""

    def env(self) -> dict[str, str]:
        return {
            "LM_CONTROL": self.control,
            "LM_INDEX": str(self.index),
            "LM_KIND": self.kind,
            "LM_LABEL": self.label,
            "LM_PROFILE": self.profile,
            "LM_MODE": self.mode,
        }


class Actions:
    """Executes bindings. `on_profile` and `on_reload` are supplied by the daemon, because
    profile switching and config reload are its business, not this module's."""

    def __init__(self, on_profile=None, on_reload=None,
                 volume_step: int = VOLUME_STEP_DEFAULT,
                 volume_mode: str = VOLUME_MODE_DEFAULT,
                 on_level=None):
        self._on_profile = on_profile
        self._on_reload = on_reload
        # Called with (fraction 0..1, label) whenever a level changes, so the daemon can show
        # it somewhere. macOS gives no overlay for a programmatic volume set, and a dial with
        # no feedback feels broken — the pad's own underglow is the answer.
        self._on_level = on_level
        self._lock = threading.Lock()
        self.volume_step = volume_step
        self.volume_mode = volume_mode
        self._volume: float | None = None
        self._volume_at = 0.0
        self._vol_trueup: threading.Timer | None = None

    # --- entry point --------------------------------------------------------

    def run(self, binding: dict, ctx: Context | None = None) -> Result:
        """Execute a config `binding` object. Exactly one action key is expected."""
        ctx = ctx or Context()
        if not isinstance(binding, dict):
            return Result(False, "binding is not an object")

        if "launch" in binding:
            return self.launch(binding["launch"])
        if "shortcut" in binding:
            return self.shortcut(binding["shortcut"])
        if "text" in binding:
            return self.text(binding["text"])
        if "shell" in binding:
            return self.shell(binding["shell"], ctx)
        if "script" in binding:
            return self.script(binding["script"], ctx)
        if "applescript" in binding:
            return self.applescript(binding["applescript"])
        if "action" in binding:
            return self.action(binding["action"], ctx)
        # `mode` and `profile` are handled by the dispatcher, which owns that state.
        if "mode" in binding or "profile" in binding:
            return Result(True, "handled by dispatcher")
        return Result(False, f"no recognised action in binding: {sorted(binding)}")

    # --- individual actions -------------------------------------------------

    def launch(self, app: str) -> Result:
        if not app:
            return Result(False, "empty launch target")
        return self._spawn(["open", "-a", app], what=f"launch {app!r}")

    def shortcut(self, spec: str) -> Result:
        keys = self._keys()
        if keys is None:
            return Result(False, "key synthesis unavailable")
        try:
            ok = keys.send_shortcut(spec)
        except ValueError as exc:
            return Result(False, f"bad shortcut {spec!r}: {exc}")
        except Exception as exc:
            return Result(False, f"shortcut {spec!r} failed: {exc}")
        return Result(bool(ok), "" if ok else f"shortcut {spec!r} did not fire")

    def text(self, s: str) -> Result:
        keys = self._keys()
        if keys is None:
            return Result(False, "key synthesis unavailable")
        try:
            return Result(bool(keys.send_text(s)))
        except Exception as exc:
            return Result(False, f"text insert failed: {exc}")

    def shell(self, command: str, ctx: Context | None = None) -> Result:
        if not command.strip():
            return Result(False, "empty shell command")
        return self._spawn(command, shell=True, ctx=ctx or Context(), what="shell command")

    def script(self, path: str, ctx: Context | None = None) -> Result:
        target = Path(os.path.expanduser(path))
        if not target.exists():
            return Result(False, f"script not found: {target}")
        if not os.access(target, os.X_OK):
            # Common enough to be worth naming the fix rather than just failing.
            return Result(False, f"script is not executable (chmod +x {target})")
        return self._spawn([str(target)], ctx=ctx or Context(), what=f"script {target.name}")

    def applescript(self, source: str) -> Result:
        if not source.strip():
            return Result(False, "empty applescript")
        return self._spawn(["osascript", "-e", source], what="applescript")

    def action(self, token: str, ctx: Context) -> Result:
        if token in ("vol_up", "vol_down"):
            direction = +1 if token == "vol_up" else -1
            if self.volume_mode == "coarse":
                keys = self._keys()
                if keys is None:
                    return Result(False, "key synthesis unavailable")
                ok = keys.send_media("vol_up" if direction > 0 else "vol_down")
                if ok:
                    self._coarse_feedback(direction)
                return Result(bool(ok))
            return self.nudge_volume(direction)

        if token in MEDIA_ACTIONS:
            keys = self._keys()
            if keys is None:
                return Result(False, "key synthesis unavailable")
            try:
                return Result(bool(keys.send_media(MEDIA_ACTIONS[token])))
            except Exception as exc:
                return Result(False, f"{token} failed: {exc}")

        if token in HOOK_ACTIONS:
            return self._hook(token, ctx)

        if token == "sleep":
            return self._spawn(["pmset", "sleepnow"], what="sleep")
        if token == "lock":
            return self._spawn(
                ["osascript", "-e",
                 'tell application "System Events" to keystroke "q" using {command down, control down}'],
                what="lock screen")

        if token in ("profile_next", "profile_prev"):
            if self._on_profile is None:
                return Result(False, "profile switching not wired up")
            self._on_profile("next" if token == "profile_next" else "prev")
            return Result(True)

        if token == "reload_config":
            if self._on_reload is None:
                return Result(False, "config reload not wired up")
            return Result(bool(self._on_reload()))

        return Result(False, f"unknown action token: {token!r}")

    def nudge_volume(self, direction: int) -> Result:
        """Set system volume directly, by `volume_step` percent — "fine" mode.

        The level is cached and advanced locally so a fast spin doesn't have to wait on a
        read per detent. The cache is refreshed whenever it's stale, so anything that changes
        volume elsewhere is picked up. The write is an *absolute* set of the predicted level
        rather than a relative nudge, so if two writes ever land out of order the later
        target still wins and nothing is double-counted.
        """
        step = max(1, int(self.volume_step)) * (1 if direction >= 0 else -1)
        with self._lock:
            now = time.monotonic()
            level = self._volume
            if level is None or now - self._volume_at > 2.0:
                level = self._read_volume()
                if level is None:
                    return Result(False, "could not read system volume")
            level = max(0, min(100, round(level) + step))
            self._volume = float(level)
            self._volume_at = now

        # Unmute on the way up, or raising volume from muted appears to do nothing.
        helper = lmvol_path()
        if helper is not None:
            cmd = [str(helper), "set", str(level), "--no-osd"]
            if direction > 0:
                cmd.append("--unmute")
            result = self._spawn(cmd, what="set volume")
        else:
            _warn_once("lmvol", f"lmvol not built, falling back to osascript for volume "
                                f"(slower under a spinning dial) — {LMVOL_BUILD_HINT}")
            script = f"set volume output volume {level}"
            if direction > 0:
                script += " without output muted"
            result = self._spawn(["osascript", "-e", script], what="set volume")
        if result and self._on_level is not None:
            try:
                self._on_level(level / 100.0, "volume")
            except Exception:
                pass          # feedback must never be able to break the action itself
        return result

    def _coarse_feedback(self, direction: int) -> None:
        """Show a predicted level on the pad after a coarse detent, then true it up.

        macOS snaps every media-key press to its 16-step grid — the next multiple of 6.25%
        in the pressed direction — so the landing spot is arithmetic. Predicting it keeps
        the dispatch path free of any blocking read (only the first detent after a quiet
        spell pays one, to seed the cache), and one deferred read after the dial goes quiet
        corrects any drift: a press that landed while muted, a rail, another app moving
        volume at the same time.
        """
        with self._lock:
            level, fresh = self._volume, time.monotonic() - self._volume_at <= 2.0
        if level is None or not fresh:
            level = self._read_volume()
            if level is None:
                return                       # can't predict; the true-up may still land
        # Snap to the next grid line in the pressed direction. The epsilon keeps a level
        # already sitting on the grid moving by a full step instead of not at all.
        eps = 1e-6
        if direction > 0:
            level = min(100.0, (math.floor(level / _VOL_GRID + eps) + 1) * _VOL_GRID)
        else:
            level = max(0.0, (math.ceil(level / _VOL_GRID - eps) - 1) * _VOL_GRID)
        with self._lock:
            self._volume, self._volume_at = level, time.monotonic()
        if self._on_level is not None:
            try:
                self._on_level(level / 100.0, "volume")
            except Exception:
                pass
        self._schedule_trueup()

    def _schedule_trueup(self) -> None:
        """(Re)arm the deferred read: one read per burst of detents, not one per detent."""
        with self._lock:
            if self._vol_trueup is not None:
                self._vol_trueup.cancel()
            t = threading.Timer(_VOL_TRUEUP_S, self._trueup)
            t.daemon = True
            self._vol_trueup = t
            t.start()

    def _trueup(self) -> None:
        level = self._read_volume()
        if level is None:
            return
        with self._lock:
            drifted = self._volume is None or abs(self._volume - level) >= 0.5
            self._volume, self._volume_at = float(level), time.monotonic()
        if drifted and self._on_level is not None:
            try:
                self._on_level(level / 100.0, "volume")
            except Exception:
                pass

    def _read_volume(self) -> float | None:
        """The current output volume, 0-100, or None. Prefers lmvol (~10 ms); falls back to
        osascript (~60 ms), which also covers a machine where lmvol was never built."""
        helper = lmvol_path()
        if helper is not None:
            try:
                out = subprocess.run([str(helper), "get"],
                                     capture_output=True, text=True, timeout=1.0)
                return max(0.0, min(100.0, float(out.stdout.split()[0])))
            except (OSError, subprocess.SubprocessError, ValueError, IndexError):
                pass                         # fall through to osascript
        try:
            out = subprocess.run(["osascript", "-e", _VOL_GET],
                                 capture_output=True, text=True, timeout=2.0)
        except (OSError, subprocess.SubprocessError):
            return None
        try:
            return max(0.0, min(100.0, float(int(out.stdout.strip()))))
        except ValueError:
            return None

    # --- helpers ------------------------------------------------------------

    def _hook(self, name: str, ctx: Context) -> Result:
        """Run a user-supplied hook for an action we can't implement natively."""
        target = HOOK_DIR / name
        if not target.exists():
            _warn_once(
                f"hook:{name}",
                f"action {name!r} needs a hook: create an executable at {target}. "
                f"It receives LM_* context in its environment.")
            return Result(False, f"no hook for {name} (expected {target})")
        if not os.access(target, os.X_OK):
            return Result(False, f"hook is not executable (chmod +x {target})")
        return self._spawn([str(target)], ctx=ctx, what=f"hook {name}")

    def _spawn(self, cmd, shell: bool = False, ctx: Context | None = None,
               what: str = "command") -> Result:
        """Start a process and leave it running. Never waits for completion."""
        env = dict(os.environ)
        if ctx is not None:
            env.update(ctx.env())
        try:
            subprocess.Popen(
                cmd,
                shell=shell,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,   # a slow child must not die with the daemon
            )
        except (OSError, ValueError) as exc:
            return Result(False, f"{what} failed to start: {exc}")
        return Result(True)

    def _keys(self):
        """The key-synthesis module, or None with a one-time explanation.

        Imported lazily so the daemon starts and the lighting works even if the native
        helper was never built.
        """
        try:
            from . import keys
        except ImportError as exc:
            _warn_once("keys-import", f"keyboard synthesis unavailable: {exc}")
            return None
        probe = getattr(keys, "available", None)
        if callable(probe):
            try:
                if not probe():
                    caps = getattr(keys, "capabilities", lambda: {})() or {}
                    if not caps.get("built"):
                        detail = ("native helper not built — run "
                                  "swiftc -O -o lmkey lmkey.swift in host/swift/")
                    elif not caps.get("accessibility"):
                        # TCC attributes trust to the *responsible* process, so the entry to
                        # grant is the terminal or launchd job running the daemon, not lmkey.
                        detail = ("macOS Accessibility permission not granted to the app "
                                  "running this daemon (Terminal/iTerm, or the launchd job)")
                    else:
                        detail = "helper not ready"
                    _warn_once("keys-caps", f"keyboard synthesis unavailable: {detail}")
                    return None
            except Exception as exc:
                _warn_once("keys-probe", f"keyboard synthesis probe failed: {exc}")
                return None
        return keys


def describe_command(command: str) -> str:
    """A short, log-safe rendering of a shell command."""
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    head = parts[0] if parts else command
    return head if len(command) <= 60 else f"{head} …"
