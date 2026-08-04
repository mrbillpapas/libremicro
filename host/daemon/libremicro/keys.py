"""Synthesising keyboard input on macOS — the `shortcut`, `text` and media `action` bindings.

A macropad key is only useful if pressing it can do what a keyboard does, so this is the
layer under `binding.shortcut`, `binding.text`, and the media half of `binding.action` in
host/config/schema.json.

**Why a helper binary.** Chords could be faked with `osascript`/System Events, but media keys
cannot: play/pause, next/prev track, volume and brightness are not virtual keycodes at all,
they are NX "system-defined" aux-control events. Those have to be built natively, so the
actual posting lives in `host/swift/lmkey` (build: `swiftc -O -o lmkey lmkey.swift`) and this
module is a thin, safe wrapper over its CLI.

**Two rules shape the API.**

*Never block the dispatch path.* These calls come from the input recogniser, on the serial
reader thread, where a keypress that takes 200 ms to register feels broken. So `send_*` only
queues the work — it hands a request to one background sender thread and returns in about a
millisecond. That thread, not the caller, waits for the helper. The single thread is also what
keeps sends from overlapping, which matters more than it sounds: see the comment above `_pump`.
The only place that ever waits is `capabilities()`, which answers the web UI, not a keypress.

*Never explode because the helper isn't there.* A user who has cloned the repo but not run
swiftc, or not granted Accessibility, should get one clear line telling them what to do — not
a traceback per keypress and not a dead daemon. So every `send_*` returns a bool and warns at
most once per cause.

`parse_shortcut` is deliberately pure Python and works with no helper present, because
validating a config must not depend on a compiled artefact. It normalises *names*; the helper
owns keycodes and layout semantics.
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# --- names ----------------------------------------------------------------------
#
# These tables mirror `lmkey.swift`. They are duplicated rather than queried from the binary
# so a config can be validated with nothing built; tests/test_keys.py compares the two sets
# whenever the binary happens to be present, which is what keeps them from drifting.

#: Canonical modifier names, in the order a normalised spec lists them (Apple's ⌃⌥⇧⌘, fn first).
MODIFIERS: tuple[str, ...] = ("fn", "ctrl", "opt", "shift", "cmd")

MODIFIER_ALIASES: dict[str, str] = {
    "cmd": "cmd", "command": "cmd", "⌘": "cmd", "meta": "cmd", "super": "cmd", "win": "cmd",
    "ctrl": "ctrl", "control": "ctrl", "ctl": "ctrl", "⌃": "ctrl",
    "opt": "opt", "option": "opt", "alt": "opt", "⌥": "opt",
    "shift": "shift", "shft": "shift", "⇧": "shift",
    "fn": "fn", "function": "fn",
}

#: Canonical key names — one per physical key on a US ANSI layout.
KEY_NAMES: frozenset[str] = frozenset(
    list("abcdefghijklmnopqrstuvwxyz")
    + [str(d) for d in range(10)]
    + [f"f{n}" for n in range(1, 21)]
    + ["escape", "tab", "return", "space", "delete", "forwarddelete",
       "home", "end", "pageup", "pagedown", "left", "right", "down", "up",
       "help", "capslock",
       "minus", "equal", "leftbracket", "rightbracket", "backslash",
       "semicolon", "quote", "comma", "period", "slash", "grave",
       "kp0", "kp1", "kp2", "kp3", "kp4", "kp5", "kp6", "kp7", "kp8", "kp9",
       "kpdecimal", "kpplus", "kpminus", "kpmultiply", "kpdivide", "kpequals",
       "kpenter", "kpclear",
       # The shifted legend of `equal`; nameable because '+' is also the separator.
       "plus"]
)

KEY_ALIASES: dict[str, str] = {
    "esc": "escape",
    "enter": "return", "ret": "return", "cr": "return",
    "spc": "space", "spacebar": "space",
    "backspace": "delete", "bksp": "delete", "bs": "delete",
    "fwddelete": "forwarddelete", "forward_delete": "forwarddelete", "fdel": "forwarddelete",
    "pgup": "pageup", "page_up": "pageup",
    "pgdn": "pagedown", "pagedn": "pagedown", "page_down": "pagedown",
    "uparrow": "up", "arrowup": "up", "downarrow": "down", "arrowdown": "down",
    "leftarrow": "left", "arrowleft": "left", "rightarrow": "right", "arrowright": "right",
    "caps": "capslock", "caps_lock": "capslock",
    "-": "minus", "dash": "minus", "hyphen": "minus",
    "=": "equal", "equals": "equal",
    "[": "leftbracket", "lbracket": "leftbracket", "left_bracket": "leftbracket",
    "]": "rightbracket", "rbracket": "rightbracket", "right_bracket": "rightbracket",
    "\\": "backslash",
    ";": "semicolon", "'": "quote", "apostrophe": "quote",
    ",": "comma", ".": "period", "/": "slash",
    "`": "grave", "backtick": "grave", "backquote": "grave", "tilde": "grave",
    "num_enter": "kpenter", "clear": "kpclear",
    "+": "plus",
}

#: The media/volume/brightness half of the schema's `action` enum, plus what the same NX
#: mechanism gives us for free. Anything else in that enum is not this module's business.
MEDIA_ACTIONS: tuple[str, ...] = (
    "vol_up", "vol_down", "mute", "play_pause", "next_track", "prev_track",
    "bright_up", "bright_down",
    "eject", "fast_forward", "rewind", "illum_up", "illum_down", "illum_toggle",
)

#: The eight tokens the schema promises. `MEDIA_ACTIONS` is a superset.
SCHEMA_MEDIA_ACTIONS: tuple[str, ...] = MEDIA_ACTIONS[:8]

MEDIA_ALIASES: dict[str, str] = {
    **{a: a for a in MEDIA_ACTIONS},
    "volup": "vol_up", "volume_up": "vol_up", "volumeup": "vol_up",
    "voldown": "vol_down", "volume_down": "vol_down", "volumedown": "vol_down",
    "vol_mute": "mute", "volume_mute": "mute",
    "play": "play_pause", "pause": "play_pause", "playpause": "play_pause",
    "next": "next_track", "nexttrack": "next_track",
    "prev": "prev_track", "previous": "prev_track", "prevtrack": "prev_track",
    "previous_track": "prev_track",
    "brightup": "bright_up", "brightness_up": "bright_up", "brightnessup": "bright_up",
    "brightdown": "bright_down", "brightness_down": "bright_down",
    "brightnessdown": "bright_down",
    "ff": "fast_forward", "rew": "rewind",
}


# --- shortcuts ------------------------------------------------------------------

@dataclass(frozen=True)
class Shortcut:
    """A normalised chord: canonical modifier names in canonical order, plus one key.

    `mods` is ordered by `MODIFIERS` and deduplicated, so two specs that mean the same thing
    compare equal — which is what lets the web UI recognise an already-bound chord.
    """
    key: str
    mods: tuple[str, ...] = ()

    def __str__(self) -> str:
        return "+".join((*self.mods, self.key))

    @property
    def spec(self) -> str:
        """The canonical spec string, and exactly what gets passed to the helper."""
        return str(self)


def _components(spec: str) -> list[str]:
    """Split a chord spec on '+', keeping a literal plus addressable.

    '+' is both the separator and a key legend. The rule (shared with lmkey.swift): a
    doubled trailing '+' is a literal plus ('cmd++'), a bare '+' is a literal plus, and any
    other empty component is a typo worth reporting rather than guessing at.
    """
    if not spec:
        raise ValueError("empty shortcut")
    if spec == "+":
        return ["+"]
    if spec.endswith("++"):
        mods = [p for p in spec[:-1].split("+") if p]
        if not mods:
            raise ValueError(f"shortcut {spec!r} has no key")
        return [*mods, "+"]
    if spec.endswith("+"):
        raise ValueError(
            f"shortcut {spec!r} has no key after the last '+' "
            f"(for a literal plus write {spec + '+'!r} or {spec + 'plus'!r})")
    parts = spec.split("+")
    if any(not p for p in parts):
        raise ValueError(
            f"shortcut {spec!r} has an empty component "
            "(write a literal plus last, as in 'cmd++', or use 'plus')")
    return parts


def parse_shortcut(spec: str | Shortcut) -> Shortcut:
    """Normalise a chord spec, e.g. 'cmd+shift+4' or 'CMD+⇧+4' -> Shortcut('4', ('shift','cmd')).

    Accepts any order, any case, and the aliases in `MODIFIER_ALIASES`/`KEY_ALIASES`. A single
    key with no modifier ('f13', 'escape') is a perfectly good shortcut. Raises `ValueError`
    naming the offending component on anything unrecognised — config errors should be legible.
    """
    if isinstance(spec, Shortcut):
        return spec
    if not isinstance(spec, str):
        raise ValueError(f"shortcut must be a string, got {type(spec).__name__}")

    comps = _components(spec.strip().lower())
    *raw_mods, raw_key = comps

    mods: set[str] = set()
    for raw in raw_mods:
        mod = MODIFIER_ALIASES.get(raw)
        if mod is None:
            if _canonical_key(raw) is not None:
                raise ValueError(
                    f"{raw!r} in {spec!r} is a key, not a modifier — a shortcut has exactly "
                    "one key, written last")
            raise ValueError(
                f"unknown modifier {raw!r} in {spec!r} "
                f"(one of: {', '.join(MODIFIERS)})")
        mods.add(mod)

    key = _canonical_key(raw_key)
    if key is None:
        if raw_key in MODIFIER_ALIASES:
            raise ValueError(
                f"{raw_key!r} in {spec!r} is a modifier — a shortcut needs a key too")
        raise ValueError(f"unknown key {raw_key!r} in {spec!r}")

    return Shortcut(key=key, mods=tuple(m for m in MODIFIERS if m in mods))


def _canonical_key(raw: str) -> str | None:
    if raw in KEY_NAMES:
        return raw
    alias = KEY_ALIASES.get(raw)
    return alias if alias in KEY_NAMES else None


def canonical_media_action(action: str) -> str | None:
    """The canonical media token for `action`, or None if it isn't a media action.

    Non-media members of the schema's `action` enum (desk_up, lock, profile_next, ...) return
    None — they're implemented elsewhere in the daemon, not by synthesising a keypress.
    """
    if not isinstance(action, str):
        return None
    return MEDIA_ALIASES.get(action.strip().lower())


def is_media_action(action: str) -> bool:
    """Whether `action` is something `send_media` can do."""
    return canonical_media_action(action) is not None


# --- locating the helper --------------------------------------------------------

HELPER_NAME = "lmkey"
HELPER_ENV = "LIBREMICRO_LMKEY"

#: host/swift/lmkey, resolved from this file: libremicro/ -> daemon/ -> host/.
DEFAULT_HELPER = Path(__file__).resolve().parents[2] / "swift" / HELPER_NAME

BUILD_HINT = (f"build it with: cd host/swift && swiftc -O -o {HELPER_NAME} {HELPER_NAME}.swift")

ACCESS_HINT = (
    "grant Accessibility in System Settings > Privacy & Security > Accessibility to the app "
    "that launched this daemon (Terminal, iTerm, your launchd job) — macOS attributes the "
    "permission to that responsible process, not to the helper. Synthesised keys are silently "
    "discarded until you do, then restart the daemon.")


def helper_path() -> Path | None:
    """Where the built helper is, or None if it isn't built.

    Checked every call rather than cached, so building it while the daemon runs just starts
    working. `LIBREMICRO_LMKEY` overrides, which is how the tests point at a fake or at
    nothing at all.
    """
    override = os.environ.get(HELPER_ENV)
    if override is not None:
        p = Path(override).expanduser()
        return p if _executable(p) else None
    if _executable(DEFAULT_HELPER):
        return DEFAULT_HELPER
    found = shutil.which(HELPER_NAME)
    return Path(found) if found else None


def _searched_location() -> str:
    """Where we looked, for an error message that matches reality when the env var is set."""
    override = os.environ.get(HELPER_ENV)
    return f"{HELPER_ENV}={override}" if override else str(DEFAULT_HELPER)


def _executable(p: Path) -> bool:
    try:
        return p.is_file() and os.access(p, os.X_OK)
    except OSError:
        return False


# --- state ----------------------------------------------------------------------

_ACCESS_RECHECK_S = 30.0     # after a denial, let one send through this often to re-test
_CHECK_TIMEOUT_S = 3.0       # bound on the synchronous `lmkey check` in capabilities()
_SEND_TIMEOUT_S = 15.0       # a send this slow is wedged; kill it rather than block the queue
_QUEUE_MAX = 64

#: Exit code lmkey uses for "not trusted for Accessibility".
_EXIT_NO_ACCESSIBILITY = 3

_lock = threading.Lock()
_warned: set[str] = set()
_access: bool | None = None          # None = not yet known
_access_at = 0.0
_queue: "queue.Queue[list[str]]" = queue.Queue(maxsize=_QUEUE_MAX)
_worker: threading.Thread | None = None


def _warn(tag: str, message: str) -> None:
    """Print `message` once per `tag` for the life of the process.

    Per-keypress spam would bury everything else in the log, and everything that goes wrong
    here (not built, not trusted) is a persistent condition: saying it twice adds nothing.
    """
    with _lock:
        if tag in _warned:
            return
        _warned.add(tag)
    print(f"keys: {message}", file=sys.stderr, flush=True)


def reset_warnings() -> None:
    """Forget what's been warned about and any cached Accessibility state."""
    global _access, _access_at
    with _lock:
        _warned.clear()
        _access = None
        _access_at = 0.0


def _record_access(state: bool | None) -> None:
    global _access, _access_at
    with _lock:
        _access = state
        _access_at = time.monotonic()
    if state is False:
        _warn("access", f"not trusted for Accessibility — {ACCESS_HINT}")


def _run_check(path: Path) -> bool | None:
    """`lmkey check` synchronously. True/False = trusted or not, None = couldn't tell."""
    try:
        proc = subprocess.run([str(path), "check", "--json"], capture_output=True,
                              text=True, timeout=_CHECK_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode == 0:
        return True
    if proc.returncode == _EXIT_NO_ACCESSIBILITY:
        return False
    if '"accessibility":true' in proc.stdout:
        return True
    if '"accessibility":false' in proc.stdout:
        return False
    return None


# --- sending -------------------------------------------------------------------
#
# Sends go through one worker thread, for two reasons that are easy to miss until you watch
# the events on a tap:
#
#   Ordering. A keyboard is a serial device. Spawning a helper per send and not waiting lets
#   them overlap, and overlapping is not merely out of order — it is wrong. A chord holds
#   real modifier keys down for its duration, so text typed concurrently arrives as a series
#   of accidental shortcuts. One worker that waits for each helper to exit makes a send
#   atomic with respect to the next.
#
#   Diagnostics. Off the hot path we can afford to read the helper's exit code and stderr,
#   which is how the Accessibility denial gets reported accurately (exit 3) instead of guessed
#   at. The caller still never waits: it drops the request in the queue and returns.

def _pump() -> None:
    """Sender loop. A daemon thread, so it needs no shutdown path — the process exiting is it,
    and `drain()` is there for anyone who wants pending keystrokes finished first."""
    while True:
        args = _queue.get()
        try:
            _run_send(args)
        except Exception as exc:            # never let the worker die on one bad send
            _warn("worker", f"send failed unexpectedly: {exc!r}")
        finally:
            _queue.task_done()


def _run_send(args: list[str]) -> None:
    path = helper_path()
    if path is None:
        return                              # vanished between enqueue and now
    try:
        proc = subprocess.run([str(path), *args], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=_SEND_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _warn("timeout", f"{HELPER_NAME} did not finish within {_SEND_TIMEOUT_S:g}s; killed")
        return
    except OSError as exc:
        _warn("spawn", f"could not run {path}: {exc}. {BUILD_HINT}")
        return

    if proc.returncode == 0:
        # A send that came back clean is also the cheapest possible Accessibility check.
        _record_access(True)
        return
    if proc.returncode == _EXIT_NO_ACCESSIBILITY:
        _record_access(False)
        return
    # Anything else is a bug or a spec the helper rejected; show it, once.
    detail = (proc.stderr or "").strip().splitlines()
    _warn(f"exit{proc.returncode}",
          f"{HELPER_NAME} {' '.join(args[:2])} exited {proc.returncode}: "
          f"{detail[0] if detail else '(no message)'}")


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_pump, name="lmkey-sender", daemon=True)
        _worker.start()


def _spawn(args: list[str]) -> bool:
    """Queue a helper invocation and return immediately.

    The bool means *dispatched*, not *landed* — whether the keystroke arrived is only known
    after the helper exits, and waiting for that is exactly what a dispatch path must not do.
    False means we know it can't work: nothing built, or Accessibility known to be denied.
    """
    path = helper_path()
    if path is None:
        _warn("missing",
              f"{HELPER_NAME} helper not found at {_searched_location()} — keyboard shortcuts, "
              f"text and media keys are disabled. {BUILD_HINT}")
        return False

    with _lock:
        # A denial is sticky for a while: re-testing on every keypress would burn a process
        # per press to learn what we already know. Letting one through after the window means
        # granting the permission recovers on its own, without a restart.
        denied = _access is False and time.monotonic() - _access_at < _ACCESS_RECHECK_S
    if denied:
        return False

    _ensure_worker()
    try:
        _queue.put_nowait(list(args))
    except queue.Full:
        _warn("full", f"{HELPER_NAME} can't keep up; dropping synthesised keys")
        return False
    return True


def drain(timeout: float = 5.0) -> bool:
    """Wait for queued sends to finish. For shutdown and for tests that want determinism.

    Returns False if the queue didn't empty in time. Never call this from the dispatch path.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _queue.unfinished_tasks == 0:
            return True
        time.sleep(0.01)
    return _queue.unfinished_tasks == 0


def send_shortcut(spec: str | Shortcut) -> bool:
    """Press a chord, e.g. 'cmd+shift+4'. Returns False if the helper is unavailable.

    Raises `ValueError` for an unparseable spec — that's a config bug the user needs to see,
    not something to swallow.
    """
    return _spawn(["chord", parse_shortcut(spec).spec])


def send_text(s: str) -> bool:
    """Type `s` at the cursor. Any Unicode works — no keycode needed, layout-independent.

    Newlines become real Return presses and tabs real Tab presses; a Unicode newline in a key
    event is ignored by most text views, so without that, multi-line text arrives run together.
    Empty text is a no-op returning False, since nothing was dispatched.
    """
    if not isinstance(s, str):
        raise ValueError(f"text must be a string, got {type(s).__name__}")
    if not s:
        return False
    # `--` so text starting with a dash isn't read as an option.
    return _spawn(["text", "--", s])


def send_media(action: str) -> bool:
    """Press a media/volume/brightness key: the media half of the schema's `action` enum.

    These are NX system-defined events, not keycodes, which is the whole reason the Swift
    helper exists. Raises `ValueError` for a token that isn't a media action — including the
    schema's non-media ones (desk_up, lock, ...), which belong to other parts of the daemon.
    """
    # A ':fine' qualifier asks for quarter-step volume: the helper posts the same NX event
    # with shift+option held, which is the documented macOS gesture for finer increments.
    # Validate the action name without it, then pass it through.
    base, sep, qualifier = action.partition(":")
    token = canonical_media_action(base)
    if token is None or (sep and qualifier != "fine"):
        raise ValueError(
            f"unknown media action {action!r} "
            f"(one of: {', '.join(SCHEMA_MEDIA_ACTIONS)}; "
            f"a volume token may carry ':fine' for quarter steps)")
    return _spawn(["media", f"{token}:fine" if qualifier == "fine" else token])


# --- capability reporting ------------------------------------------------------

def capabilities(refresh: bool = False) -> dict:
    """What this machine can actually do, for the web UI's setup panel.

    Unlike `send_*` this may wait — bounded by `_CHECK_TIMEOUT_S` — because it's answering a
    request from a person, not a keypress. `accessibility` is None when the helper isn't built
    or didn't answer, which is a different thing from a definite "denied".
    """
    path = helper_path()
    if path is None:
        return {
            "helper": None,
            "expected_at": str(DEFAULT_HELPER),
            "built": False,
            "accessibility": None,
            "hint": BUILD_HINT,
        }

    with _lock:
        cached, age = _access, time.monotonic() - _access_at
    if refresh or cached is None or age >= _ACCESS_RECHECK_S:
        state = _run_check(path)
        _record_access(state)
    else:
        state = cached

    caps = {
        "helper": str(path),
        "expected_at": str(DEFAULT_HELPER),
        "built": True,
        "accessibility": state,
        "media_actions": list(SCHEMA_MEDIA_ACTIONS),
    }
    if state is False:
        caps["hint"] = ACCESS_HINT
    return caps


def available() -> bool:
    """Whether a send has any chance of landing: helper built and not known to be untrusted."""
    if helper_path() is None:
        return False
    with _lock:
        return _access is not False
