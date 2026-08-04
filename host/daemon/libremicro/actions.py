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

import os
import shlex
import subprocess
import sys
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
    "vol_up": "volume_up", "vol_down": "volume_down", "mute": "mute",
    "play_pause": "play_pause", "next_track": "next_track", "prev_track": "prev_track",
    "bright_up": "brightness_up", "bright_down": "brightness_down",
}

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

    def __init__(self, on_profile=None, on_reload=None):
        self._on_profile = on_profile
        self._on_reload = on_reload
        self._lock = threading.Lock()

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
        caps = getattr(keys, "capabilities", None)
        if callable(caps):
            try:
                state = caps() or {}
            except Exception:
                state = {}
            if state and not state.get("available", True):
                _warn_once("keys-caps", f"keyboard synthesis unavailable: "
                                        f"{state.get('reason', 'helper not ready')}")
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
