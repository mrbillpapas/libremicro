"""Notification watchers: a key that launches an app also reflects that app's state.

Use case 3 in VISION.md — the Slack key pulses when there are unread messages, so the pad is
an ambient notifier and not just a launcher. A watcher is declared per key in the config
(`key.watch`, see the `watch` definition in host/config/schema.json), and this module turns
those declarations into background polls that drive `Renderer.pulse`.

**Pluggable by design.** The schema deliberately leaves `watch.type` open — "the daemon
defines the available kinds" — and `additionalProperties: true` lets a kind take whatever
extra fields it needs (`unread_badge` takes `app`). So kinds live in a registry: subclass
`Watcher`, set `kind`, decorate with `@register`, and nothing in the core changes. Anything
the registry doesn't know is logged once and skipped, never fatal — an exported config from
someone else's machine will name watchers this build has never heard of, and that must not
stop the lights working.

**Off the input and render paths, entirely.** Polling means subprocesses and file reads, both
of which can block for hundreds of milliseconds; the latency principle says nothing on the
input or render path may do that. So one scheduler thread owns due-times, and each poll runs
in its own short-lived thread. A watcher that hangs therefore parks one thread and stops
updating its own key — it cannot delay another watcher, the render loop, or shutdown. A
watcher that raises is caught and recorded as unknown.

**Unknown is not zero.** These two are never conflated:

  a count of 0    we know there is nothing to see — stop pulsing
  unknown         we could not find out — stop pulsing *and* record why, so the UI can say
                  so and the user can fix it (usually a permission or a wrong app name)

The pad going dark on unknown is deliberate: a pulsing key claims "you have messages", and
making that claim when we don't know is worse than showing nothing. The reason always
survives in `state()`, which is what a UI panel reads to explain a key that isn't pulsing.

**How the two macOS watchers actually get their number.** Both read the Dock badge — the
number the user can already see on the app's Dock icon — via the accessibility attribute
`AXStatusLabel` on the Dock's tiles, queried with `osascript`. That needs Accessibility (and
Automation for System Events) granted to whatever launched the daemon; when it isn't, the
read fails loudly as unknown rather than quietly as zero. `lsappinfo info -only StatusLabel`
would have avoided the permission entirely and is what several older tools use, but it
reports nothing on current macOS — measured against apps that visibly had badges — so it is
not used here. `slack_unread` additionally falls back to Slack's own persisted unread state
when the Dock cannot be read; see `slack_state_reading` for what that costs.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .layout import KEY_N

#: Default poll interval when `watch.interval_s` is absent (matches the schema's default).
DEFAULT_INTERVAL_S = 15.0
#: The schema's `minimum` for interval_s. Clamped rather than rejected.
MIN_INTERVAL_S = 1.0
#: Pulse period handed to the renderer. Slow enough to read as "attention", not an alarm.
PULSE_PERIOD_S = 1.4
#: Fallback flash colour if a `watch` somehow has none (the schema requires one).
DEFAULT_FLASH = "ffffff"

#: How far apart the first poll of each watcher is placed, so a reload doesn't fire every
#: watcher's subprocess in the same instant.
STAGGER_S = 0.25
#: Longest the scheduler sleeps between wakeups. Also how quickly a config change that this
#: module wasn't told about (a profile switch, say) is noticed.
MAX_SLEEP_S = 2.0
#: A poll still running this many intervals late is reported as stuck.
STUCK_INTERVALS = 3

_warned: set[str] = set()
_warn_lock = threading.Lock()


def _warn_once(tag: str, message: str) -> None:
    """Print `message` once per `tag`. Everything reported here is a persistent condition
    (an unknown kind, a missing permission); repeating it every 15s would bury the log."""
    with _warn_lock:
        if tag in _warned:
            return
        _warned.add(tag)
    print(f"watchers: {message}", file=sys.stderr, flush=True)


def reset_warnings() -> None:
    with _warn_lock:
        _warned.clear()


# --- what a poll returns ----------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """The outcome of one poll.

    `value is None` means unknown — the one thing that must never be mistaken for zero.
    `detail` is user-facing: it's what the UI shows next to the key, so it should read as an
    explanation ("Slack is not running", "needs Accessibility"), not as a stack trace.
    """
    value: int | None
    detail: str = ""
    source: str = ""

    @classmethod
    def of(cls, value: int, detail: str = "", source: str = "") -> "Reading":
        return cls(max(0, int(value)), detail, source)

    @classmethod
    def unknown(cls, detail: str, source: str = "") -> "Reading":
        return cls(None, detail, source)

    @property
    def is_unknown(self) -> bool:
        return self.value is None

    @property
    def active(self) -> bool:
        """Whether this reading should have the key pulsing."""
        return self.value is not None and self.value > 0


# --- the registry -----------------------------------------------------------


class Watcher:
    """One watched thing. Subclass, set `kind`, implement `poll`.

    `poll` is called on a background thread, never on the input or render path, so it may
    block — but it should bound itself anyway (a `subprocess` timeout, say), because a poll
    that never returns simply stops reporting. Raising is fine and is recorded as unknown;
    returning a `Reading` is better, because you get to write the message the user sees.
    """

    #: Config `watch.type` this class handles. Must be set by subclasses.
    kind: str = ""

    def __init__(self, spec: dict | None = None):
        self.spec: dict = dict(spec or {})

    def poll(self) -> Reading:
        raise NotImplementedError

    def describe(self) -> str:
        """Short human label for logs and the UI."""
        return self.kind

    def close(self) -> None:
        """Release anything long-lived. Called when the watcher is dropped on a reload."""


#: kind -> factory taking the config `watch` dict and returning a Watcher.
_REGISTRY: dict[str, Callable[[dict], Watcher]] = {}


def register(cls: type[Watcher]) -> type[Watcher]:
    """Class decorator: make `cls` available as `watch.type == cls.kind`."""
    if not getattr(cls, "kind", ""):
        raise ValueError(f"{cls.__name__} needs a non-empty `kind`")
    _REGISTRY[cls.kind] = cls
    return cls


def register_kind(kind: str, factory: Callable[[dict], Watcher]) -> None:
    """Register a factory under `kind`. For plugins and tests that don't want a class."""
    if not kind:
        raise ValueError("kind must be non-empty")
    _REGISTRY[kind] = factory


def unregister(kind: str) -> None:
    _REGISTRY.pop(kind, None)


def kinds() -> list[str]:
    """Watcher kinds this build understands. The UI offers these; the schema can't."""
    return sorted(_REGISTRY)


def is_supported(kind: str) -> bool:
    return kind in _REGISTRY


def create(spec: dict) -> Watcher:
    """Build the watcher a `watch` object asks for.

    Raises `KeyError` for an unknown kind and `ValueError` for a spec the kind rejects (a
    missing `app`, say). Both are config problems the caller reports per key rather than
    letting them reach the daemon.
    """
    if not isinstance(spec, dict):
        raise ValueError("watch must be an object")
    kind = str(spec.get("type") or "").strip()
    if not kind:
        raise ValueError("watch has no 'type'")
    factory = _REGISTRY.get(kind)
    if factory is None:
        raise KeyError(kind)
    watcher = factory(spec)
    if not isinstance(watcher, Watcher):
        raise ValueError(f"factory for {kind!r} did not return a Watcher")
    return watcher


# --- reading a Dock badge ---------------------------------------------------
#
# The Dock keeps each app's badge as the accessibility attribute AXStatusLabel on that app's
# tile, which is the only place the count is readable without the app's cooperation: badges
# are set through NSDockTile and are not persisted anywhere on disk. We ask for it through
# System Events because that needs no compiled helper and no third-party module — the daemon
# installs with pyserial and jsonschema and nothing else.
#
# The script reports three things rather than just a number, because the difference between
# them is the difference between zero and unknown: how many tiles carry that name, whether a
# process with that name is running, and the labels found.

_DOCK_SCRIPT = r"""
on run argv
	set appName to item 1 of argv
	set sep to "|"
	set tileCount to 0
	set labels to ""
	tell application "System Events"
		if not (exists process "Dock") then return "err" & sep & "the Dock is not running"
		tell process "Dock"
			repeat with theList in lists
				repeat with e in UI elements of theList
					set nm to ""
					try
						set nm to (name of e) as text
					end try
					if nm is appName then
						set tileCount to tileCount + 1
						set lbl to missing value
						try
							set lbl to value of attribute "AXStatusLabel" of e
						end try
						if lbl is not missing value then
							set labels to labels & (lbl as text) & sep
						end if
					end if
				end repeat
			end repeat
		end tell
		set isRunning to (exists process appName)
	end tell
	return "ok" & sep & tileCount & sep & isRunning & sep & labels
end run
"""

_OSASCRIPT_TIMEOUT_S = 8.0

_ACCESSIBILITY_MARKERS = ("-25211", "-1719", "assistive access", "accessibility access")
_AUTOMATION_MARKERS = ("-1743", "not authorized to send apple events",
                       "not authorised to send apple events", "-600")

ACCESSIBILITY_HINT = (
    "grant Accessibility in System Settings > Privacy & Security > Accessibility to the app "
    "that launched this daemon (Terminal, iTerm, your launchd job) — macOS attributes the "
    "permission to that responsible process")
AUTOMATION_HINT = (
    "allow control of System Events in System Settings > Privacy & Security > Automation "
    "for the app that launched this daemon")


@dataclass(frozen=True)
class DockBadge:
    """What the Dock told us about one app."""
    ok: bool
    tiles: int = 0
    running: bool = False
    labels: tuple[str, ...] = ()
    error: str = ""
    #: "accessibility" / "automation" when the failure was a missing permission, else "".
    permission: str = ""


def _run_osascript(args: list[str], script: str, timeout: float):
    return subprocess.run(["osascript", "-", *args], input=script,
                          capture_output=True, text=True, timeout=timeout)


def classify_osascript_error(stderr: str) -> tuple[str, str]:
    """(permission, message) for an osascript failure.

    A denied permission is the single most likely reason this watcher doesn't work, and it is
    indistinguishable from "no badge" unless we look, so it gets named explicitly.
    """
    text = (stderr or "").strip()
    low = text.lower()
    if any(m in low for m in _ACCESSIBILITY_MARKERS):
        return "accessibility", f"needs Accessibility permission — {ACCESSIBILITY_HINT}"
    if any(m in low for m in _AUTOMATION_MARKERS):
        return "automation", f"needs Automation permission — {AUTOMATION_HINT}"
    first = text.splitlines()[0] if text else "osascript failed with no message"
    return "", first


def parse_dock_output(stdout: str) -> DockBadge:
    """Parse the script's `ok|tiles|running|label|label|` line."""
    line = (stdout or "").strip()
    if not line:
        return DockBadge(False, error="the Dock query returned nothing")
    parts = line.split("|")
    if parts[0] == "err":
        return DockBadge(False, error=parts[1] if len(parts) > 1 else "the Dock query failed")
    if parts[0] != "ok" or len(parts) < 3:
        return DockBadge(False, error=f"unexpected Dock query output: {line!r}")
    try:
        tiles = int(parts[1])
    except ValueError:
        return DockBadge(False, error=f"unexpected tile count in {line!r}")
    running = parts[2].strip().lower() == "true"
    labels = tuple(p for p in parts[3:] if p.strip())
    return DockBadge(True, tiles=tiles, running=running, labels=labels)


def read_dock_badge(app: str, timeout: float = _OSASCRIPT_TIMEOUT_S, run=None) -> DockBadge:
    """Ask the Dock for `app`'s tiles and badge labels."""
    run = run or _run_osascript
    try:
        proc = run([app], _DOCK_SCRIPT, timeout)
    except subprocess.TimeoutExpired:
        return DockBadge(False, error=f"the Dock query did not answer within {timeout:g}s")
    except FileNotFoundError:
        return DockBadge(False, error="osascript not found — this watcher is macOS-only")
    except OSError as exc:
        return DockBadge(False, error=f"could not run osascript: {exc}")

    if proc.returncode != 0:
        permission, message = classify_osascript_error(proc.stderr)
        return DockBadge(False, error=message, permission=permission)
    return parse_dock_output(proc.stdout)


_BULLET_CHARS = "•●∙·⦁"


def parse_badge_label(label: str) -> int:
    """A Dock badge label as a count.

    Badges are strings, not numbers, and apps use that: Slack shows "•" for unread channels
    with no mention, several apps cap at "99+", and some badge a word. So: digits win, a
    bullet is one, and any other non-empty label is also one — a badge the user can see must
    never come back as zero, and the raw text is kept alongside for the UI.
    """
    text = (label or "").strip()
    if not text:
        return 0
    digits = re.search(r"\d[\d,  ]*", text)
    if digits:
        cleaned = re.sub(r"[^\d]", "", digits.group())
        if cleaned:
            return int(cleaned)
    return 1


_installed_cache: dict[str, bool] = {}


def app_installed(app: str, run=None) -> bool:
    """Whether macOS can resolve `app` to an application, without launching it.

    This is what separates "you quit Slack" (a real zero) from "your config says 'Zoom' but
    the app is called 'zoom.us'" (a config error that must not look like zero unread).
    Positive answers are cached for the life of the process; negative ones are re-checked,
    so installing the app later starts working on its own.
    """
    if _installed_cache.get(app):
        return True
    runner = run or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True,
                                                timeout=5.0))
    try:
        proc = runner(["/usr/bin/open", "-Ra", app])
        ok = proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        # Couldn't tell. Claim installed: the alternative is calling a real app missing.
        return True
    if ok:
        _installed_cache[app] = True
    return ok


def badge_reading(app: str, read=None, installed=None) -> Reading:
    """Turn a Dock query into a Reading, keeping zero and unknown apart."""
    badge = (read or read_dock_badge)(app)
    if not badge.ok:
        return Reading.unknown(badge.error, source="dock")

    if badge.tiles == 0:
        if badge.running:
            return Reading.unknown(
                f"{app} is running but has no Dock tile — a badge cannot be read",
                source="dock")
        if not (installed or app_installed)(app):
            return Reading.unknown(
                f"no application named {app!r} — check the watch's 'app' field",
                source="dock")
        return Reading.of(0, detail=f"{app} is not running", source="dock")

    if not badge.labels:
        return Reading.of(0, detail=f"no badge on {app}", source="dock")

    counts = [parse_badge_label(label) for label in badge.labels]
    best = max(counts)
    shown = ", ".join(repr(label) for label in badge.labels)
    return Reading.of(best, detail=f"Dock badge {shown}", source="dock")


# --- the two concrete watchers ----------------------------------------------


@register
class UnreadBadgeWatcher(Watcher):
    """`{"type": "unread_badge", "app": "WhatsApp", "flash": "25d366"}`

    Reliability, honestly: this is exactly the number on the Dock icon, so it agrees with
    what the user sees by definition — but it inherits every limitation of that. An app whose
    badge the user has switched off badges nothing and reports 0, and an app that badges
    something other than unread count (a download tally, a running-task count) reports that
    instead. It cannot work at all without Accessibility, and says so when it can't.
    """

    kind = "unread_badge"

    def __init__(self, spec: dict | None = None, read=None, installed=None):
        super().__init__(spec)
        self.app = str(self.spec.get("app") or "").strip()
        if not self.app:
            raise ValueError(
                'unread_badge needs an "app" — the application whose Dock badge to read')
        self._read = read
        self._installed = installed

    def poll(self) -> Reading:
        return badge_reading(self.app, read=self._read, installed=self._installed)

    def describe(self) -> str:
        return f"{self.kind}({self.app})"


#: Slack's Electron state, persisted by the app itself. Not an API and not documented; it is
#: read only as a fallback, and only ever to answer "is there unread", never to write.
SLACK_STATE_PATH = Path(
    "~/Library/Application Support/Slack/storage/root-state.json").expanduser()


def slack_state_counts(doc: dict) -> tuple[int, int]:
    """(mention count, number of workspaces with plain unread) from Slack's root state.

    Shape, per workspace: `webapp.teams.<TEAM>.unreads = {unreadHighlights, unreads,
    showBullet}`. `unreadHighlights` is the mention/DM count Slack badges numerically;
    `unreads` non-zero with `showBullet` is the "•" badge — unread channels, no mention.
    """
    teams = ((doc.get("webapp") or {}).get("teams") or {}) if isinstance(doc, dict) else {}
    highlights = 0
    bullets = 0
    if not isinstance(teams, dict):
        return 0, 0
    for team in teams.values():
        unreads = (team or {}).get("unreads") if isinstance(team, dict) else None
        if not isinstance(unreads, dict):
            continue
        try:
            highlights += max(0, int(unreads.get("unreadHighlights") or 0))
        except (TypeError, ValueError):
            pass
        try:
            plain = int(unreads.get("unreads") or 0)
        except (TypeError, ValueError):
            plain = 0
        if plain > 0 and unreads.get("showBullet", True):
            bullets += 1
    return highlights, bullets


def slack_state_reading(path: Path | None = None, now: float | None = None) -> Reading:
    """Slack's unread state from its own on-disk state file. No token, no permission.

    Deliberately the fallback and not the primary source: the file is written by Slack when
    it feels like it, so it can lag the truth by minutes, and a stale non-zero would leave a
    key pulsing at nothing. The age is reported so the UI can say how much to trust it.
    """
    target = Path(path) if path is not None else SLACK_STATE_PATH
    try:
        raw = target.read_text()
        age = max(0.0, (now if now is not None else time.time()) - os.path.getmtime(target))
    except OSError as exc:
        return Reading.unknown(f"cannot read Slack's state file ({exc.strerror or exc})",
                               source="slack-state")
    try:
        doc = json.loads(raw)
    except ValueError:
        return Reading.unknown(f"Slack's state file is not readable JSON ({target.name})",
                               source="slack-state")

    highlights, bullets = slack_state_counts(doc)
    value = highlights if highlights else (1 if bullets else 0)
    detail = (f"Slack's own state file: {highlights} mention(s), {bullets} workspace(s) with "
              f"unread; written {age:.0f}s ago")
    return Reading.of(value, detail=detail, source="slack-state")


@register
class SlackUnreadWatcher(UnreadBadgeWatcher):
    """`{"type": "slack_unread", "flash": "e01e5a"}` — no token, works with Slack just open.

    The Dock badge is the primary source because it is what the user sees: a number for
    mentions and DMs, "•" for unread channels without a mention (counted as one). If the Dock
    can't be read — no Accessibility yet, Slack not tiled — it falls back to Slack's own
    persisted state, which needs no permission at all but may be stale. Which source answered
    is always reported.
    """

    kind = "slack_unread"

    def __init__(self, spec: dict | None = None, read=None, installed=None,
                 state_path: Path | None = None):
        spec = dict(spec or {})
        spec.setdefault("app", "Slack")
        super().__init__(spec, read=read, installed=installed)
        self.state_path = state_path

    def poll(self) -> Reading:
        primary = super().poll()
        if not primary.is_unknown:
            return primary
        fallback = slack_state_reading(self.state_path)
        if fallback.is_unknown:
            # The Dock error is the actionable one (usually a permission), so it wins.
            return replace(primary, detail=f"{primary.detail}; {fallback.detail}")
        return replace(fallback,
                       detail=f"{fallback.detail} (Dock unreadable: {primary.detail})")


# --- per-key bookkeeping ----------------------------------------------------


@dataclass
class Entry:
    """One `key.watch` declaration, live."""
    index: int
    kind: str
    spec: dict
    flash: str
    interval: float
    watcher: Watcher | None = None
    #: False for a kind this build doesn't have, or a spec its kind rejected. Never polled.
    supported: bool = True
    key: str = ""                       # identity across reloads
    due: float = 0.0
    value: int | None = None
    detail: str = ""
    source: str = ""
    error: str | None = None
    last_poll: float | None = None      # wall clock, for the UI
    polls: int = 0
    pulsing: bool = False
    inflight: bool = False
    started_at: float = 0.0

    def snapshot(self, wall_now: float) -> dict:
        return {
            "index": self.index,
            "kind": self.kind,
            "app": self.spec.get("app"),
            "flash": self.flash,
            "interval_s": self.interval,
            "supported": self.supported,
            "value": self.value,
            "unknown": self.value is None,
            "pulsing": self.pulsing,
            "error": self.error,
            "detail": self.detail,
            "source": self.source,
            "polls": self.polls,
            "polling": self.inflight,
            "last_poll": self.last_poll,
            "age_s": (None if self.last_poll is None
                      else max(0.0, wall_now - self.last_poll)),
        }


class Watchers:
    """Owns every live watcher: scheduling, state, and the renderer pulses.

    The daemon creates one, calls `start()`/`stop()`, and reads `state()`. It does not have
    to tell this object about a profile switch: `tick` notices that the set of `watch`
    declarations changed and rebuilds, which keeps the wiring in daemon.py to four lines and
    means nothing can drift out of sync with the config.

    `clock` is monotonic time (scheduling) and `wall` is epoch time (reporting); both are
    injected so tests can drive the whole thing without sleeping. `threaded=False` runs polls
    inline, which is the same code path minus the thread.
    """

    def __init__(self, daemon, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time, threaded: bool = True):
        self.d = daemon
        self.clock = clock
        self.wall = wall
        self.threaded = threaded
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._entries: list[Entry] = []
        self._signature: str | None = None

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="lm-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            # A hung poll runs on its own daemon thread, so this only waits for the
            # scheduler — shutdown can't be held up by a watcher.
            thread.join(timeout=2.0)
        with self._lock:
            for entry in self._entries:
                self._set_pulse(entry, False)
                if entry.watcher is not None:
                    try:
                        entry.watcher.close()
                    except Exception:
                        pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:            # the scheduler must not be killable
                _warn_once(f"loop:{type(exc).__name__}",
                           f"scheduler error ({type(exc).__name__}: {exc}); continuing")
            self._stop.wait(self._sleep_for())

    def _sleep_for(self) -> float:
        now = self.clock()
        with self._lock:
            due = [e.due for e in self._entries if e.supported and not e.inflight]
        if not due:
            return MAX_SLEEP_S
        return max(0.05, min(MAX_SLEEP_S, min(due) - now))

    # --- config ------------------------------------------------------------

    @property
    def cfg(self):
        return self.d.cfg

    def config_changed(self) -> None:
        """Called by the daemon after it swaps config, so the change lands immediately
        instead of at the next wakeup. `tick` would catch it anyway."""
        with self._lock:
            self._sync(self.clock(), force=True)

    def _declarations(self) -> list[tuple[int, dict]]:
        """`(key index, watch spec)` for the active profile.

        Mode key overrides are ignored on purpose: a mode is a transient encoder rebinding,
        and tearing watchers down and back up as the user flicks through modes would lose
        every count and re-run every subprocess.
        """
        try:
            profile = self.cfg.profile()
        except Exception:
            return []
        found: list[tuple[int, dict]] = []
        for spec in profile.get("keys") or []:
            if not isinstance(spec, dict):
                continue
            watch = spec.get("watch")
            if not isinstance(watch, dict):
                continue
            try:
                index = int(spec.get("index", -1))
            except (TypeError, ValueError):
                continue
            if 0 <= index < KEY_N:
                found.append((index, watch))
        return found

    def _sync(self, now: float, force: bool = False) -> bool:
        """Rebuild entries if the config's watch declarations changed. Returns whether it did."""
        declarations = self._declarations()
        try:
            signature = json.dumps([self.cfg.active_profile_name, declarations],
                                   sort_keys=True, default=str)
        except (TypeError, ValueError):
            signature = repr(declarations)
        if not force and signature == self._signature:
            return False
        self._signature = signature
        self._rebuild(declarations, now)
        return True

    def _rebuild(self, declarations: list[tuple[int, dict]], now: float) -> None:
        previous = {e.key: e for e in self._entries}
        entries: list[Entry] = []
        for position, (index, watch) in enumerate(declarations):
            key = _identity(index, watch)
            existing = previous.pop(key, None)
            if existing is not None:
                # Unchanged declaration: keep its watcher, its value, and its place in the
                # schedule, so saving an unrelated config edit doesn't blank the pad.
                entries.append(existing)
                continue
            entries.append(self._make_entry(index, watch, key,
                                            now + position * STAGGER_S))

        for stale in previous.values():
            self._set_pulse(stale, False)
            if stale.watcher is not None:
                try:
                    stale.watcher.close()
                except Exception:
                    pass
        self._entries = entries

    def _make_entry(self, index: int, watch: dict, key: str, due: float) -> Entry:
        kind = str(watch.get("type") or "").strip() or "(none)"
        try:
            interval = max(MIN_INTERVAL_S, float(watch.get("interval_s")
                                                 or DEFAULT_INTERVAL_S))
        except (TypeError, ValueError):
            interval = DEFAULT_INTERVAL_S
        flash = str(watch.get("flash") or DEFAULT_FLASH)
        entry = Entry(index=index, kind=kind, spec=dict(watch), flash=flash,
                      interval=interval, key=key, due=due)
        try:
            entry.watcher = create(watch)
        except KeyError:
            # Someone else's exported config naming a kind we don't have. Skip it, say so
            # once, and keep the entry so the UI can explain the dark key.
            entry.supported = False
            entry.error = (f"unknown watcher kind {kind!r} — this build has: "
                           f"{', '.join(kinds()) or 'none'}")
            _warn_once(f"kind:{kind}", f"key {index}: {entry.error}")
        except Exception as exc:
            entry.supported = False
            entry.error = f"{kind}: {exc}"
            _warn_once(f"spec:{kind}:{index}", f"key {index}: {entry.error}")
        return entry

    # --- polling -----------------------------------------------------------

    def tick(self, now: float | None = None, inline: bool | None = None) -> None:
        """Do whatever is due. Called from the scheduler thread, or by tests with a clock."""
        now = self.clock() if now is None else now
        with self._lock:
            self._sync(now)
            due = [e for e in self._entries
                   if e.supported and e.watcher is not None and e.due <= now]
            for entry in due:
                entry.due = now + entry.interval
            starting = [e for e in due if not self._note_inflight(e, now)]
        for entry in starting:
            self._dispatch(entry, inline)

    def _note_inflight(self, entry: Entry, now: float) -> bool:
        """Whether `entry` already has a poll running (and complain if it's stuck)."""
        if not entry.inflight:
            return False
        overdue = now - entry.started_at
        if overdue > STUCK_INTERVALS * entry.interval:
            entry.error = (f"a poll has been running for {overdue:.0f}s — this watcher is "
                           f"stuck and its key won't update")
            _warn_once(f"stuck:{entry.kind}:{entry.index}",
                       f"key {entry.index}: {entry.error}")
        return True

    def _dispatch(self, entry: Entry, inline: bool | None) -> None:
        with self._lock:
            entry.inflight = True
            entry.started_at = self.clock()
        run_inline = (not self.threaded) if inline is None else inline
        if run_inline:
            self._run(entry)
            return
        # One thread per poll, so a watcher that blocks forever costs one parked thread and
        # nothing else. It can't delay another watcher, the render loop, or shutdown.
        threading.Thread(target=self._run, args=(entry,), daemon=True,
                         name=f"lm-watch-{entry.kind}-{entry.index}").start()

    def _run(self, entry: Entry) -> None:
        watcher = entry.watcher
        try:
            reading = watcher.poll() if watcher is not None else Reading.unknown("no watcher")
            reading = _coerce(reading)
        except Exception as exc:
            # A broken watcher reports unknown. It never takes the daemon, or any other
            # watcher, with it.
            reading = Reading.unknown(f"{type(exc).__name__}: {exc}")
        self._record(entry, reading)

    def _record(self, entry: Entry, reading: Reading) -> None:
        with self._lock:
            entry.inflight = False
            entry.last_poll = self.wall()
            entry.polls += 1
            entry.detail = reading.detail
            entry.source = reading.source
            entry.value = reading.value
            entry.error = reading.detail if reading.is_unknown else None
            self._set_pulse(entry, reading.active)
        if reading.is_unknown:
            _warn_once(f"unknown:{entry.kind}:{entry.index}:{reading.detail[:60]}",
                       f"key {entry.index} ({entry.kind}): {reading.detail}")

    def _set_pulse(self, entry: Entry, want: bool) -> None:
        """Start or stop this key's pulse, but only on a change.

        `pulse` is the renderer's own layer and already composites above the base colour and
        the effect, so there is nothing to save or restore here — stopping it puts the key
        back to whatever the profile says it should be.
        """
        if want == entry.pulsing:
            return
        renderer = getattr(self.d, "renderer", None)
        if renderer is None:
            return
        try:
            if want:
                renderer.pulse(entry.index, entry.flash, PULSE_PERIOD_S)
            else:
                renderer.pulse(entry.index, None)
        except Exception as exc:
            _warn_once(f"pulse:{entry.index}", f"could not pulse key {entry.index}: {exc}")
            return
        entry.pulsing = want

    # --- reporting ---------------------------------------------------------

    def state(self) -> list[dict]:
        """Per-watcher state, for the UI panel that explains why a key is or isn't pulsing.

        Read-only: every dict is a fresh copy, so a caller (or a JSON encoder) can't reach
        into the live entries.
        """
        wall_now = self.wall()
        with self._lock:
            return [entry.snapshot(wall_now) for entry in self._entries]

    def status(self) -> dict:
        """`state()` plus what this build can do at all."""
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "kinds": kinds(),
            "watchers": self.state(),
        }

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def _coerce(reading) -> Reading:
    """Accept a bare count from a watcher that couldn't be bothered with a Reading.

    Anything else — including None, the shape a half-written watcher returns by accident — is
    unknown rather than zero, because zero is a claim we have no business making on its
    behalf.
    """
    if isinstance(reading, Reading):
        return reading
    if isinstance(reading, bool):
        return Reading.of(1 if reading else 0)
    if isinstance(reading, int):
        return Reading.of(reading)
    return Reading.unknown(
        f"poll returned {type(reading).__name__}, expected a Reading or an int")


def _identity(index: int, watch: dict) -> str:
    """Stable identity for a declaration, so a reload can keep an unchanged watcher alive."""
    try:
        return f"{index}:" + json.dumps(watch, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return f"{index}:{watch!r}"
