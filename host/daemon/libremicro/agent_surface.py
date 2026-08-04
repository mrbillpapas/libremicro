"""The agentic-coding control surface: a live Claude Code session, on the pad.

Use case 4 in VISION.md, Phase 7 in docs/ROADMAP.md. Design notes and setup instructions
live in docs/AGENT-SURFACE.md; this docstring covers only what a reader of the code needs.

**Where status comes from, and why.** There is no API for asking a running Claude Code
session how it is doing. What there *is* is the hook system: `settings.json` can run a
command on ~30 named events, each handed a JSON object on stdin carrying `session_id`,
`cwd`, `transcript_path`, `permission_mode`, `effort.level` and `hook_event_name`. That is a
push feed of exactly the transitions we want, it is under the user's control, and it needs no
polling. So the status source is a fire-and-forget hook that POSTs the payload it was given
to the daemon, and `ingest()` is the other end of that pipe. `EVENT_STATUS` is the whole
mapping, in one table, deliberately.

Two consequences shape everything else.

*We are told about transitions, never about steady state.* Nothing fires while a turn is
merely in progress, and nothing at all fires if the session is killed mid-turn. A surface
that trusted the last thing it heard would sit there showing "working" for the rest of the
day. So every live status expires — `stale_after_s` since the last report of any kind puts
the session back to `unknown`, and `unknown` is a real, visible, deliberately unconfident
state, not an error.

*We can observe far more than we can control.* Effort level arrives on every hook payload,
so the dial can show the truth; but nothing outside the process can change a live session's
effort, model, or focused session. Where an action can only be faked, it is not implemented —
`effort.apply: "none"` exists for exactly that reason, and the one mechanism that does work
(typing `/effort <level>` into the focused terminal) is named for what it is.

Nothing here blocks the input path. Anything that could — running whisper, waiting on a
focus hook — goes to one background worker thread, the same rule `actions.py` follows.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue

from .actions import HOOK_DIR, Result

# --- status model -----------------------------------------------------------

#: No usable information. The honest default: shown before any hook has ever reported, and
#: shown again once a report goes stale. Never inferred from anything.
UNKNOWN = "unknown"
#: Session is alive and it is your turn.
IDLE = "idle"
#: A turn is in progress — thinking, or running tools.
WORKING = "working"
#: The session has stopped and wants something from you: a permission decision, or input.
WAITING = "waiting"
#: The turn ended in an API-level failure (rate limit, overload, auth, billing).
ERROR = "error"
#: A turn just finished successfully. Decays to IDLE after `done_hold_s`.
DONE = "done"

STATUSES: tuple[str, ...] = (UNKNOWN, IDLE, WORKING, WAITING, ERROR, DONE)

#: Statuses that mean "a turn is in flight". These are the ones that must expire: if the
#: session dies here, no further hook fires, and stale is the only way we find out.
LIVE_STATUSES: frozenset[str] = frozenset({WORKING, WAITING})

#: Returned by `map_event` for SessionEnd. Not a status — `ingest` turns it into a removal.
ENDED = "ended"


@dataclass(frozen=True)
class Led:
    """How a status looks on a key.

    Colour says *what*, behaviour and rate say *how urgently*. That split is the whole point:
    a solid key is information you can ignore, a pulse is a request. Rate then ranks the
    requests — `waiting` pulses roughly three times faster than `working`, so "it needs me"
    is distinguishable from "it's busy" from across a room and without reading the colour.
    """
    color: str
    behaviour: str          # "solid" | "pulse"
    period: float = 0.0     # seconds per pulse cycle; ignored when solid


#: The mapping the whole feature is judged on. Overridable per-colour via `agent.colors`.
LED_MAP: dict[str, Led] = {
    # Dim, colourless, and still — reads as "no signal", which is what it means. Deliberately
    # not off: an unlit key is indistinguishable from a daemon that died.
    UNKNOWN: Led("141414", "solid"),
    # Cool and dim. Present, unremarkable, ignorable.
    IDLE:    Led("0d2a44", "solid"),
    # Amber, slow breath. Busy is not urgent, so it must not compete for attention.
    WORKING: Led("ff8c00", "pulse", 1.8),
    # The one that has to grab you. Fast, and magenta because no other state uses that hue,
    # so it is unambiguous even to a red/green-colourblind user.
    WAITING: Led("ff2fd0", "pulse", 0.45),
    # Red, mid-rate: worth your attention, but nothing is waiting on your keypress.
    ERROR:   Led("ff2200", "pulse", 1.0),
    # Solid green. Nothing is required of you; the information is "it finished".
    DONE:    Led("00e676", "solid"),
}

#: Effort ladder, low to high. These are the levels the documented hook payload's
#: `effort.level` field can carry. Override with `agent.effort.levels`.
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

EFFORT_ON = "3fa9ff"        # a rung at or below the selection
EFFORT_PENDING = "ffd400"   # selected but not yet applied to the session

DICTATE_RECORDING = "ff1744"
DICTATE_BUSY = "ffab00"
DICTATE_OK = "00e676"

FAIL_FLASH = "ff2200"

#: Built-in action tokens this module implements. `extend_actions` routes them.
AGENT_ACTIONS: tuple[str, ...] = (
    "agent_approve", "agent_deny",
    "agent_session_next", "agent_session_prev", "agent_session_focus",
    "agent_effort_up", "agent_effort_down", "agent_effort_apply",
    "agent_dictate_start", "agent_dictate_stop", "agent_dictate",
)

#: Hook the user drops in ~/.config/libremicro/hooks/ to bring a session's terminal forward.
#: Focusing an arbitrary terminal pane is not portable — tmux, iTerm2, Terminal.app and
#: Ghostty each need different incantations — so it follows the same escape hatch actions.py
#: uses for the desk-height actions rather than pretending we can do it everywhere.
FOCUS_HOOK = "agent_focus"

_SUSTAIN_S = 0.9        # `flash` duration used to hold a solid colour
_REARM_S = 0.35         # how often to re-arm it (must be < _SUSTAIN_S - flash fade)
_PROBE_CACHE_S = 5.0


# --- event -> status --------------------------------------------------------

def map_event(payload: dict) -> tuple[str | None, str]:
    """`(status, detail)` for one hook payload. `status is None` means "leave it alone".

    Leaving it alone is the important half. A hook event we don't recognise — and Claude Code
    grows new ones — still proves the session is alive, so it refreshes staleness, but it must
    not be allowed to invent a state. Same for the notification types that carry no bearing on
    what the session is doing.
    """
    event = str(payload.get("event") or payload.get("hook_event_name") or "")
    tool = str(payload.get("tool_name") or "")

    if event == "Notification":
        kind = str(payload.get("notification_type") or "")
        message = str(payload.get("message") or "")
        if kind == "permission_prompt":
            return WAITING, message or (f"permission: {tool}" if tool else "permission")
        if kind == "agent_needs_input":
            return WAITING, message or "needs input"
        if kind == "idle_prompt":
            return IDLE, message
        if kind == "agent_completed":
            return DONE, message
        return None, message            # auth_success, elicitation_*: not state

    if event == "SessionEnd":
        return ENDED, str(payload.get("reason") or "")

    if event == "StopFailure":
        # The matcher for this event is the error class, and it arrives in the payload as
        # `error_type` in the versions that carry it. Either way we know it's an error.
        return ERROR, str(payload.get("error_type") or payload.get("reason") or "api error")

    if event == "Stop":
        return DONE, _clip(payload.get("last_assistant_message") or "")

    if event == "SessionStart":
        return IDLE, str(payload.get("source") or "")

    if event in ("PreToolUse", "PostToolUse", "PostToolBatch"):
        return WORKING, tool
    if event == "PostToolUseFailure":
        # A tool that failed is routine — a grep that matched nothing, a test that went red.
        # Calling that ERROR would cry wolf on the state that means "the session is broken".
        return WORKING, f"{tool} failed" if tool else "tool failed"
    if event == "PermissionRequest":
        return WAITING, tool or "permission"
    if event == "UserPromptSubmit":
        return WORKING, "prompt"
    if event in ("SubagentStart", "SubagentStop"):
        return WORKING, str(payload.get("agent_type") or "subagent")
    if event in ("PreCompact", "PostCompact"):
        return WORKING, "compacting"
    if event == "TeammateIdle":
        return IDLE, "teammate idle"

    return None, ""


def _clip(text: str, limit: int = 80) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _effort_of(payload: dict) -> str:
    """`effort` arrives as `{"level": "high"}`; accept a bare string too."""
    raw = payload.get("effort")
    if isinstance(raw, dict):
        return str(raw.get("level") or "")
    return str(raw or "")


# --- sessions ---------------------------------------------------------------

@dataclass
class Session:
    id: str
    label: str = ""
    cwd: str = ""
    transcript_path: str = ""
    status: str = UNKNOWN
    detail: str = ""
    event: str = ""
    effort: str = ""
    permission_mode: str = ""
    terminal: dict = field(default_factory=dict)
    first_seen: float = 0.0
    last_seen: float = 0.0
    status_at: float = 0.0

    def status_now(self, now: float, stale_after: float, done_hold: float) -> str:
        """The status to act on, after expiry and decay.

        Staleness is measured from `last_seen`, not `status_at`: a busy session keeps
        reporting tool calls, so silence — not an old label — is what proves it's gone.
        """
        if self.status in LIVE_STATUSES and now - self.last_seen >= stale_after:
            return UNKNOWN
        if self.status == DONE and now - self.status_at >= done_hold:
            return IDLE
        return self.status

    def as_dict(self, now: float, stale_after: float, done_hold: float) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "cwd": self.cwd,
            "status": self.status_now(now, stale_after, done_hold),
            "reported_status": self.status,
            "detail": self.detail,
            "event": self.event,
            "effort": self.effort,
            "permission_mode": self.permission_mode,
            "terminal": dict(self.terminal),
            "age_s": round(max(0.0, now - self.last_seen), 1),
            "stale": self.status_now(now, stale_after, done_hold) == UNKNOWN
                     and self.status != UNKNOWN,
        }


# --- dictation --------------------------------------------------------------

WHISPER_BINS = ("whisper-cli", "whisper-cpp", "whisper")
MODEL_DIR = Path(os.path.expanduser("~/.cache/whisper-cpp"))
MODEL_PREFERENCE = ("ggml-small.en.bin", "ggml-base.en.bin", "ggml-medium.en.bin")


def find_whisper(configured: str = "auto") -> str | None:
    if configured and configured != "auto":
        path = os.path.expanduser(configured)
        return path if os.access(path, os.X_OK) else None
    for name in WHISPER_BINS:
        found = shutil.which(name)
        if found:
            return found
    return None


def find_model(configured: str = "auto") -> str | None:
    if configured and configured != "auto":
        path = os.path.expanduser(configured)
        return path if os.path.isfile(path) else None
    for name in MODEL_PREFERENCE:
        candidate = MODEL_DIR / name
        if candidate.is_file():
            return str(candidate)
    try:
        models = sorted(MODEL_DIR.glob("ggml-*.bin"))
    except OSError:
        return None
    return str(models[0]) if models else None


class FfmpegRecorder:
    """Records 16 kHz mono WAV from a CoreAudio input, which is what whisper.cpp wants.

    ffmpeg rather than sox because ffmpeg is what's actually installed on this machine and it
    can talk to avfoundation directly. It is stopped with SIGINT specifically: that makes
    ffmpeg finalise the WAV header on the way out, where SIGKILL leaves a truncated file that
    whisper reads as silence.
    """

    def __init__(self, device: str = ":0", binary: str = "ffmpeg"):
        self.device = device if str(device).startswith(":") else f":{device}"
        self.binary = binary
        self._proc: subprocess.Popen | None = None

    def available(self) -> str | None:
        return shutil.which(self.binary)

    def start(self, path: str) -> None:
        cmd = [self.binary, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "avfoundation", "-i", self.device,
               "-ac", "1", "-ar", "16000", "-y", path]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)

    def stop(self) -> bool:
        proc, self._proc = self._proc, None
        if proc is None:
            return False
        try:
            proc.send_signal(2)                 # SIGINT: flush and write the WAV trailer
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            return False
        except Exception:
            return False
        return True


class WhisperTranscriber:
    def __init__(self, binary: str, model: str, language: str = "en", threads: int = 4):
        self.binary = binary
        self.model = model
        self.language = language
        self.threads = threads

    def __call__(self, wav_path: str) -> str:
        base = wav_path[:-4] if wav_path.endswith(".wav") else wav_path
        cmd = [self.binary, "-m", self.model, "-f", wav_path, "-l", self.language,
               "-t", str(self.threads), "-nt", "-otxt", "-of", base]
        try:
            subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=120)
        except Exception:
            return ""
        try:
            return Path(base + ".txt").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""


class Dictation:
    """Hold to record, release to transcribe and insert.

    Everything expensive happens off the input path. `stop()` returns as soon as the recorder
    has been asked to finish; whisper and the text insertion run on the caller-supplied
    `submit_work` queue.
    """

    OFF, RECORDING, BUSY = "off", "recording", "busy"

    def __init__(self, settings: dict | None = None, *, insert=None, recorder=None,
                 transcriber=None, submit_work=None, clock=time.monotonic):
        self.s = dict(settings or {})
        self._insert = insert
        self._recorder = recorder
        self._transcriber = transcriber
        self._submit = submit_work or (lambda fn: fn())
        self._clock = clock
        self.state = self.OFF
        self.last_text = ""
        self.last_error = ""
        self._started_at = 0.0
        self._path: str | None = None
        self._recorder_live = None
        self._lock = threading.Lock()

    # --- capability ------------------------------------------------------

    def preflight(self) -> dict:
        """What is missing, named precisely enough to fix. Never raises.

        The three parts are checked independently so the reason says which one is absent —
        "dictation unavailable" sends someone hunting, "whisper-cli not found (brew install
        whisper-cpp)" does not.
        """
        if not self.s.get("enabled", True):
            return {"available": False, "reason": "dictation disabled in config",
                    "whisper": "", "model": "", "recorder": ""}
        missing: list[str] = []

        if self._recorder is not None:
            recorder = "injected"
        else:
            found = self._new_recorder().available()
            recorder = found or ""
            if not found:
                missing.append(f"{self.s.get('ffmpeg_bin', 'ffmpeg')} not found "
                               f"(brew install ffmpeg)")

        if self._transcriber is not None:
            whisper, model = "injected", "injected"
        else:
            whisper = find_whisper(self.s.get("whisper_bin", "auto")) or ""
            model = find_model(self.s.get("model", "auto")) or ""
            if not whisper:
                missing.append("whisper-cli not found (brew install whisper-cpp)")
            if not model:
                missing.append(f"no whisper model in {MODEL_DIR} (e.g. ggml-base.en.bin)")

        return {"available": not missing, "reason": "; ".join(missing),
                "whisper": whisper, "model": model, "recorder": recorder}

    def _new_recorder(self):
        return self._recorder or FfmpegRecorder(self.s.get("audio_device", ":0"),
                                                str(self.s.get("ffmpeg_bin", "ffmpeg")))

    # --- control ---------------------------------------------------------

    def start(self) -> Result:
        with self._lock:
            if self.state != self.OFF:
                return Result(False, f"dictation already {self.state}")
            caps = self.preflight()
            if not caps["available"]:
                self.last_error = caps["reason"]
                return Result(False, caps["reason"])
            recorder = self._new_recorder()
            path = os.path.join(tempfile.gettempdir(),
                               f"libremicro-dictate-{int(time.time())}.wav")
            try:
                recorder.start(path)
            except Exception as exc:
                self.last_error = f"recorder failed to start: {exc}"
                return Result(False, self.last_error)
            self._recorder_live = recorder
            self._path = path
            self._started_at = self._clock()
            self.state = self.RECORDING
            self.last_error = ""
            return Result(True, "recording")

    def stop(self) -> Result:
        with self._lock:
            if self.state != self.RECORDING:
                return Result(False, "not recording")
            recorder = self._recorder_live
            path = self._path
            self.state = self.BUSY
        try:
            ok = bool(recorder.stop()) if recorder is not None else False
        except Exception as exc:
            with self._lock:
                self.state = self.OFF
                self.last_error = f"recorder failed to stop: {exc}"
            return Result(False, self.last_error)
        if not ok or not path:
            with self._lock:
                self.state = self.OFF
                self.last_error = "recording produced no audio"
            return Result(False, self.last_error)
        self._submit(lambda: self._finish(path))
        return Result(True, "transcribing")

    def cancel(self) -> Result:
        with self._lock:
            if self.state == self.OFF:
                return Result(False, "not recording")
            recorder = self._recorder_live
            self.state = self.OFF
        try:
            if recorder is not None:
                recorder.stop()
        except Exception:
            pass
        return Result(True, "cancelled")

    def over_limit(self, now: float) -> bool:
        limit = float(self.s.get("max_seconds", 60) or 0)
        return (self.state == self.RECORDING and limit > 0
                and now - self._started_at >= limit)

    # --- worker ----------------------------------------------------------

    def _finish(self, path: str) -> None:
        try:
            transcriber = self._transcriber
            if transcriber is None:
                binary, model = (find_whisper(self.s.get("whisper_bin", "auto")),
                                 find_model(self.s.get("model", "auto")))
                if not binary or not model:
                    self.last_error = "whisper disappeared between start and stop"
                    return
                transcriber = WhisperTranscriber(binary, model,
                                                 str(self.s.get("language", "en")),
                                                 int(self.s.get("threads", 4)))
            try:
                text = (transcriber(path) or "").strip()
            except Exception as exc:
                self.last_error = f"transcription failed: {exc}"
                return
            if not text:
                self.last_error = "transcription was empty"
                return
            self.last_text = text
            self.last_error = ""
            if self._insert is not None:
                try:
                    if not self._insert(text):
                        self.last_error = "could not insert transcript"
                except Exception as exc:
                    self.last_error = f"insert failed: {exc}"
        finally:
            with self._lock:
                self.state = self.OFF
            for leftover in (path, path[:-4] + ".txt" if path.endswith(".wav") else ""):
                if leftover:
                    try:
                        os.unlink(leftover)
                    except OSError:
                        pass


# --- process probe ----------------------------------------------------------

def claude_processes() -> int | None:
    """How many `claude` CLI processes are running, or None if we couldn't tell.

    Diagnostic only. This can distinguish "no session at all" from "a session is running but
    isn't reporting, so your hooks aren't installed" — which is worth saying out loud — and it
    can do nothing else. It cannot see per-session state, cannot tell busy from idle, and
    cannot tell which session is which, so it never feeds the status model.

    `ps` and an exact match on the command name, not `pgrep -f claude`: that pattern also
    matches every helper process of the Claude desktop app, which would report a session that
    doesn't exist.
    """
    try:
        out = subprocess.run(["ps", "-Ao", "comm"], capture_output=True, text=True,
                             timeout=2.0)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    count = 0
    for line in out.stdout.splitlines():
        name = line.strip()
        if name == "claude" or name.endswith("/claude"):
            count += 1
    return count


# --- the surface ------------------------------------------------------------

class AgentSurface:
    """Holds session state, paints it, and runs the agent action tokens.

    Constructed with the daemon so it can reach `cfg` and `renderer`, and with injectable
    seams for everything that touches the outside world, because the interesting behaviour
    here — expiry, LED choice, degradation — has to be testable with no session, no
    microphone, and no clock.
    """

    def __init__(self, daemon, *, clock=time.monotonic, sender=None, dictation=None,
                 probe=claude_processes):
        self.d = daemon
        self._clock = clock
        self._sender = sender                # None = lazily import .keys
        self._probe = probe
        self._lock = threading.RLock()

        self.sessions: dict[str, Session] = {}
        self._pinned: str | None = None      # session id, or None for auto-follow
        self._pending_effort: str | None = None
        self._reports = 0
        self._painted: dict[int, tuple] = {}
        self._last_rearm = 0.0
        self._probe_cache: tuple[float, int | None] = (0.0, None)

        self._work: Queue = Queue(maxsize=32)
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

        self.dictation = dictation or Dictation(
            self.settings.get("dictation"), insert=self._insert_text,
            submit_work=self.submit, clock=clock)

    # --- config ----------------------------------------------------------

    @property
    def settings(self) -> dict:
        """The `agent` object from config, or an empty one.

        Read live rather than cached at construction so a config reload takes effect, and
        defensively because `agent` is not in schema.json yet — a config without it must
        still start a daemon.
        """
        try:
            value = self.d.cfg.doc.get("agent")
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", True))

    def _num(self, key: str, default: float) -> float:
        try:
            return float(self.settings.get(key, default))
        except (TypeError, ValueError):
            return default

    @property
    def stale_after(self) -> float:
        return max(1.0, self._num("stale_after_s", 90.0))

    @property
    def session_ttl(self) -> float:
        return max(self.stale_after, self._num("session_ttl_s", 1800.0))

    @property
    def done_hold(self) -> float:
        return max(0.0, self._num("done_hold_s", 10.0))

    @property
    def effort_levels(self) -> list[str]:
        raw = (self.settings.get("effort") or {}).get("levels")
        if isinstance(raw, list) and raw:
            return [str(x) for x in raw]
        return list(EFFORT_LEVELS)

    def led_for(self, status: str) -> Led:
        base = LED_MAP.get(status, LED_MAP[UNKNOWN])
        override = (self.settings.get("colors") or {}).get(status)
        return Led(str(override), base.behaviour, base.period) if override else base

    def _key(self, name: str) -> int | None:
        value = self.settings.get(name)
        try:
            index = int(value)
        except (TypeError, ValueError):
            return None
        return index if 0 <= index <= 12 else None

    def _key_list(self, name: str) -> list[int]:
        out = []
        for value in self.settings.get(name) or []:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= index <= 12:
                out.append(index)
        return out

    def config_changed(self) -> None:
        """Config was swapped. Drop painted state so keys the new config no longer uses stop
        being driven, and rebuild dictation against the new settings."""
        with self._lock:
            self._painted.clear()
            self._pending_effort = None
            if self.dictation.state == Dictation.OFF:
                self.dictation = Dictation(self.settings.get("dictation"),
                                           insert=self._insert_text,
                                           submit_work=self.submit, clock=self._clock)
            else:
                self.dictation.s = dict(self.settings.get("dictation") or {})

    # --- lifecycle -------------------------------------------------------

    def submit(self, fn) -> bool:
        """Run `fn` on the background worker. Full queue drops the job rather than blocking —
        a wedged whisper must not be able to stall a keypress."""
        self._ensure_worker()
        try:
            self._work.put_nowait(fn)
        except Exception:
            return False
        return True

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop.clear()
            self._worker = threading.Thread(target=self._pump, name="lm-agent", daemon=True)
            self._worker.start()

    def _pump(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._work.get(timeout=0.25)
            except Empty:
                continue
            try:
                job()
            except Exception as exc:
                _warn(f"agent worker: {type(exc).__name__}: {exc}")

    def close(self) -> None:
        self._stop.set()
        try:
            self.dictation.cancel()
        except Exception:
            pass
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.join(timeout=2.0)

    # --- ingest ----------------------------------------------------------

    def ingest(self, payload: dict, now: float | None = None) -> dict:
        """Absorb one hook report. This is the body of `POST /api/agent/status`.

        Never raises and never rejects a payload it merely doesn't understand: an unknown
        event refreshes the session's liveness and leaves the status alone, so a Claude Code
        release that adds a hook event degrades to "no new information", not to a wrong colour
        and not to a 500.
        """
        now = self._clock() if now is None else now
        if not isinstance(payload, dict):
            return {"ok": False, "errors": ["payload must be an object"]}
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return {"ok": False, "errors": ["session_id is required"]}

        status, detail = map_event(payload)
        explicit = payload.get("status")
        if isinstance(explicit, str) and explicit in STATUSES:
            # An escape hatch for non-Claude-Code sources: say the status outright.
            status, detail = explicit, str(payload.get("detail") or detail)

        with self._lock:
            self._reports += 1
            if status == ENDED:
                self.sessions.pop(session_id, None)
                if self._pinned == session_id:
                    self._pinned = None
                self._expire(now)
                return {"ok": True, "session_id": session_id, "status": ENDED,
                        "sessions": len(self.sessions)}

            session = self.sessions.get(session_id)
            if session is None:
                session = Session(id=session_id, first_seen=now, status_at=now)
                self.sessions[session_id] = session

            cwd = str(payload.get("cwd") or "")
            if cwd:
                session.cwd = cwd
            label = str(payload.get("label") or "")
            session.label = label or session.label or (os.path.basename(cwd.rstrip("/"))
                                                       if cwd else session_id[:8])
            if payload.get("transcript_path"):
                session.transcript_path = str(payload["transcript_path"])
            if payload.get("permission_mode"):
                session.permission_mode = str(payload["permission_mode"])
            effort = _effort_of(payload)
            if effort:
                session.effort = effort
                if self._pending_effort == effort:
                    self._pending_effort = None      # the session caught up; stop nagging
            terminal = payload.get("terminal")
            if isinstance(terminal, dict) and terminal:
                session.terminal = {str(k): str(v) for k, v in terminal.items() if v}

            session.event = str(payload.get("event") or payload.get("hook_event_name") or "")
            session.last_seen = now
            if status is not None:
                if status != session.status:
                    session.status_at = now
                session.status = status
                session.detail = detail
            self._expire(now)
            selected = self._selected(now)
            return {"ok": True, "session_id": session_id,
                    "status": session.status_now(now, self.stale_after, self.done_hold),
                    "selected": bool(selected is not None and selected.id == session_id),
                    "sessions": len(self.sessions)}

    def _expire(self, now: float) -> None:
        """Forget sessions we haven't heard from in `session_ttl`. Called under the lock."""
        ttl = self.session_ttl
        for sid in [s.id for s in self.sessions.values() if now - s.last_seen >= ttl]:
            self.sessions.pop(sid, None)
            if self._pinned == sid:
                self._pinned = None

    # --- selection -------------------------------------------------------

    def _ordered(self, now: float) -> list[Session]:
        """Sessions in auto-follow order: anything waiting first, then most recent."""
        prioritise = bool(self.settings.get("prioritise_waiting", True))

        def rank(s: Session) -> tuple:
            waiting = prioritise and s.status_now(now, self.stale_after, self.done_hold) == WAITING
            return (0 if waiting else 1, -s.last_seen)

        return sorted(self.sessions.values(), key=rank)

    def _selected(self, now: float) -> Session | None:
        if self._pinned is not None:
            pinned = self.sessions.get(self._pinned)
            if pinned is not None:
                return pinned
            self._pinned = None
        if not self.settings.get("follow_active", True):
            recent = sorted(self.sessions.values(), key=lambda s: -s.last_seen)
            return recent[0] if recent else None
        ordered = self._ordered(now)
        return ordered[0] if ordered else None

    def selected(self, now: float | None = None) -> Session | None:
        with self._lock:
            return self._selected(self._clock() if now is None else now)

    def status(self, now: float | None = None) -> str:
        """The status the pad is showing. `unknown` when there's nothing to show."""
        now = self._clock() if now is None else now
        with self._lock:
            session = self._selected(now)
            if session is None:
                return UNKNOWN
            return session.status_now(now, self.stale_after, self.done_hold)

    def cycle_session(self, step: int, now: float | None = None) -> Result:
        """Move the selection. The cycle includes an 'auto' slot, so there is always a way
        back to following whatever is most active — a pin you can't undo from the pad is a
        trap."""
        now = self._clock() if now is None else now
        with self._lock:
            ids = [s.id for s in sorted(self.sessions.values(), key=lambda s: -s.last_seen)]
            if not ids:
                return Result(False, "no sessions reporting")
            slots: list[str | None] = [None] + ids
            try:
                position = slots.index(self._pinned)
            except ValueError:
                position = 0
            self._pinned = slots[(position + step) % len(slots)]
            target = self._pinned
            label = (self.sessions[target].label if target in self.sessions else "auto")
        return Result(True, f"session: {label}")

    # --- painting --------------------------------------------------------

    def targets(self, now: float | None = None) -> dict[int, Led]:
        """What each configured key should look like right now.

        Built lowest precedence first so later roles overwrite earlier ones when a user has
        put two roles on one key: effort bar < status < approve/deny/alert < dictation.
        Dictation wins because it is the only one that reflects something the user is
        physically doing at that moment.
        """
        now = self._clock() if now is None else now
        out: dict[int, Led] = {}
        with self._lock:
            session = self._selected(now)
            status = (session.status_now(now, self.stale_after, self.done_hold)
                      if session is not None else UNKNOWN)
            reported_effort = session.effort if session is not None else ""
            pending = self._pending_effort
            others_waiting = any(
                s.status_now(now, self.stale_after, self.done_hold) == WAITING
                for s in self.sessions.values()
                if session is None or s.id != session.id)

        levels = self.effort_levels
        bar = self._key_list("effort_keys")[: len(levels)]
        if bar:
            shown = pending or reported_effort
            top = levels.index(shown) if shown in levels else -1
            pending_at = levels.index(pending) if pending in levels else -1
            for i, index in enumerate(bar):
                if i == pending_at and pending != reported_effort:
                    out[index] = Led(EFFORT_PENDING, "pulse", 0.6)
                elif i <= top:
                    out[index] = Led(EFFORT_ON, "solid")

        status_key = self._key("status_key")
        if status_key is not None:
            out[status_key] = self.led_for(status)

        waiting_led = self.led_for(WAITING)
        if status == WAITING:
            # Light the two keys you are being asked to press. Nothing else on the pad
            # communicates "act here" as directly as the affordance lighting up.
            for name in ("approve_key", "deny_key"):
                index = self._key(name)
                if index is not None:
                    out[index] = waiting_led
        alert_key = self._key("alert_key")
        if alert_key is not None and others_waiting:
            out[alert_key] = waiting_led

        dictate_key = self._key("dictate_key")
        if dictate_key is not None:
            state = self.dictation.state
            if state == Dictation.RECORDING:
                out[dictate_key] = Led(DICTATE_RECORDING, "pulse", 0.7)
            elif state == Dictation.BUSY:
                out[dictate_key] = Led(DICTATE_BUSY, "pulse", 0.35)
        return out

    def tick(self, now: float | None = None) -> None:
        """Called once per render frame. Expires status, decays `done`, repaints, and stops a
        runaway recording. Must never raise into the render loop."""
        now = self._clock() if now is None else now
        try:
            if not self.enabled:
                return
            if self.dictation.over_limit(now):
                _warn("dictation hit max_seconds; stopping")
                self.dictation.stop()
            self._apply(self.targets(now), now)
        except Exception as exc:
            _warn(f"agent tick: {type(exc).__name__}: {exc}")

    def _apply(self, targets: dict[int, Led], now: float) -> None:
        renderer = getattr(self.d, "renderer", None)
        if renderer is None:
            return
        rearm = now - self._last_rearm >= _REARM_S
        for index, led in targets.items():
            signature = (led.color, led.behaviour, led.period)
            changed = self._painted.get(index) != signature
            if led.behaviour == "pulse":
                if changed:
                    renderer.pulse(index, led.color, led.period)
            elif changed or rearm:
                # There is no "hold this colour" call in the renderer, and there shouldn't
                # be — the base layer is config's job. A flash longer than the renderer's
                # fade window holds full colour, so re-arming it is how a status sits solid,
                # and it self-clears within a second if this loop ever stops.
                if changed:
                    renderer.pulse(index, None)
                renderer.flash(index, led.color, seconds=_SUSTAIN_S)
            self._painted[index] = signature
        for index in [i for i in self._painted if i not in targets]:
            renderer.pulse(index, None)
            del self._painted[index]
        if rearm:
            self._last_rearm = now

    # --- actions ---------------------------------------------------------

    def extend_actions(self, actions) -> None:
        """Route the `agent_*` tokens through this surface.

        Installed by replacing the bound `action` method on the `Actions` instance, so the
        agent surface stays a self-contained module and `actions.py` needs no knowledge of
        it. The proper long-term home for these tokens is the built-in table in `actions.py`
        plus the schema's `action` enum; until then this is one line of wiring in the daemon
        and nothing else changes.
        """
        inner = actions.action

        def action(token: str, ctx) -> Result:
            if token in AGENT_ACTIONS:
                return self.run_action(token, ctx)
            return inner(token, ctx)

        actions.action = action

    def run_action(self, token: str, ctx=None) -> Result:
        if not self.enabled:
            return Result(False, "agent surface disabled in config")
        try:
            if token == "agent_approve":
                return self.respond("approve")
            if token == "agent_deny":
                return self.respond("deny")
            if token == "agent_session_next":
                return self.cycle_session(+1)
            if token == "agent_session_prev":
                return self.cycle_session(-1)
            if token == "agent_session_focus":
                return self.focus_selected()
            if token == "agent_effort_up":
                return self.nudge_effort(+1)
            if token == "agent_effort_down":
                return self.nudge_effort(-1)
            if token == "agent_effort_apply":
                return self.apply_effort()
            if token == "agent_dictate_start":
                return self.dictation.start()
            if token == "agent_dictate_stop":
                return self.dictation.stop()
            if token == "agent_dictate":
                return (self.dictation.stop()
                        if self.dictation.state == Dictation.RECORDING
                        else self.dictation.start())
        except Exception as exc:
            return Result(False, f"{token}: {type(exc).__name__}: {exc}")
        return Result(False, f"unknown agent action: {token!r}")

    # --- approve / deny --------------------------------------------------

    def respond(self, which: str) -> Result:
        """Answer a permission prompt by synthesising the keystroke into the focused app.

        There is no way to address a keystroke *to* a session, so this is guarded on the one
        thing we actually know: the session told us it is waiting. Without that guard an
        approve key is an Enter key that fires into whatever happens to be frontmost, which is
        a genuinely bad thing to give someone. `require_waiting: false` removes the guard for
        people who want it; the guard is on by default because the failure mode is silent.
        """
        settings = self.settings.get(which) or {}
        spec = str(settings.get("shortcut") or ("return" if which == "approve" else "escape"))
        text = settings.get("text")
        now = self._clock()
        if self.settings.get("require_waiting", True):
            if self.status(now) != WAITING:
                return Result(False, f"not waiting for approval (status: {self.status(now)})")

        def send() -> bool:
            ok = True
            if text:
                ok = self._insert_text(str(text))
            return bool(self._send_shortcut(spec) and ok)

        if self.settings.get("focus_first", False):
            delay = max(0.0, self._num("focus_delay_ms", 250.0) / 1000.0)

            def job() -> None:
                self.focus_selected()
                time.sleep(delay)
                send()

            return (Result(True, f"{which} queued after focus") if self.submit(job)
                    else Result(False, "agent worker busy"))
        fired = send()
        return Result(bool(fired), "" if fired else f"{which} keystroke did not fire")

    def focus_selected(self) -> Result:
        """Bring the selected session's terminal forward, via the user's focus hook.

        Focusing a specific terminal pane is not portable — tmux, iTerm2, Terminal.app and
        Ghostty each need something different, and only the hook that runs *inside* the
        session can know which one it is. So the session's `terminal` hints are handed to a
        user-supplied executable, exactly as actions.py does for desk height, rather than
        guessing.
        """
        session = self.selected()
        if session is None:
            return Result(False, "no session selected")
        target = HOOK_DIR / FOCUS_HOOK
        if not target.exists():
            return Result(False, f"no focus hook (expected {target}); "
                                 f"it receives LM_AGENT_* in its environment")
        if not os.access(target, os.X_OK):
            return Result(False, f"focus hook is not executable (chmod +x {target})")
        env = dict(os.environ)
        env.update({
            "LM_AGENT_SESSION": session.id,
            "LM_AGENT_LABEL": session.label,
            "LM_AGENT_CWD": session.cwd,
            "LM_AGENT_STATUS": self.status(),
            "LM_AGENT_TRANSCRIPT": session.transcript_path,
        })
        env.update({f"LM_AGENT_TERM_{k.upper()}": v for k, v in session.terminal.items()})
        try:
            subprocess.Popen([str(target)], env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        except (OSError, ValueError) as exc:
            return Result(False, f"focus hook failed to start: {exc}")
        return Result(True, f"focus {session.label}")

    # --- effort ----------------------------------------------------------

    def nudge_effort(self, step: int) -> Result:
        """Move the pending effort selection one rung. Does not apply it.

        Select-then-commit rather than apply-per-detent, for two reasons. A detent is a
        discrete step but a dial gets spun, so applying each one would type five slash
        commands to get from low to max. And the selection needs to be visible before it is
        acted on — the effort bar shows the pending rung pulsing against the level the session
        actually reported, so you can see what you're about to ask for and whether it landed.
        """
        levels = self.effort_levels
        if not levels:
            return Result(False, "no effort levels configured")
        with self._lock:
            session = self._selected(self._clock())
            current = self._pending_effort or (session.effort if session else "")
            index = levels.index(current) if current in levels else 0
            index = max(0, min(len(levels) - 1, index + step))
            self._pending_effort = levels[index]
            chosen = self._pending_effort
        return Result(True, f"effort -> {chosen} (press to apply)")

    def apply_effort(self) -> Result:
        """Commit the pending effort level.

        Claude Code has no external control channel: `/effort` is an in-session slash command
        and nothing outside the process can invoke it. The only honest mechanism left is to
        *type* it, which needs the session focused and its prompt empty. That is a real
        constraint, not a bug, so it is stated here and in the docs rather than papered over —
        and `effort.apply: "none"` turns the knob into a read-only display for anyone who'd
        rather have no action than a fragile one.
        """
        mode = str((self.settings.get("effort") or {}).get("apply", "slash_command"))
        with self._lock:
            pending = self._pending_effort
        if not pending:
            return Result(False, "no pending effort selection")
        if mode == "none":
            return Result(False, "effort.apply is 'none': the dial is display-only")
        if mode != "slash_command":
            return Result(False, f"unknown effort.apply mode: {mode!r}")
        if not self._insert_text(f"/effort {pending}"):
            return Result(False, "could not type the effort command")
        if not self._send_shortcut("return"):
            return Result(False, "typed /effort but could not submit it")
        return Result(True, f"typed /effort {pending}")

    # --- key synthesis ---------------------------------------------------

    def _keys(self):
        if self._sender is not None:
            return self._sender
        try:
            from . import keys
        except ImportError as exc:
            _warn(f"agent surface: keyboard synthesis unavailable: {exc}")
            return None
        caps = getattr(keys, "capabilities", None)
        if callable(caps):
            try:
                state = caps() or {}
            except Exception:
                state = {}
            if state and not state.get("available", True):
                _warn(f"agent surface: keyboard synthesis unavailable: "
                      f"{state.get('reason', 'helper not ready')}")
                return None
        return keys

    def _send_shortcut(self, spec: str) -> bool:
        sender = self._keys()
        if sender is None:
            return False
        try:
            return bool(sender.send_shortcut(spec))
        except Exception as exc:
            _warn(f"agent surface: shortcut {spec!r} failed: {exc}")
            return False

    def _insert_text(self, text: str) -> bool:
        sender = self._keys()
        if sender is None:
            return False
        try:
            return bool(sender.send_text(text))
        except Exception as exc:
            _warn(f"agent surface: text insert failed: {exc}")
            return False

    # --- reporting -------------------------------------------------------

    def snapshot(self, now: float | None = None) -> dict:
        """State for `GET /api/status` and the web UI. Never raises."""
        now = self._clock() if now is None else now
        with self._lock:
            selected = self._selected(now)
            sessions = [s.as_dict(now, self.stale_after, self.done_hold)
                        for s in self._ordered(now)]
            pending = self._pending_effort
            reports = self._reports
        status = (selected.status_now(now, self.stale_after, self.done_hold)
                  if selected is not None else UNKNOWN)
        led = self.led_for(status)
        return {
            "enabled": self.enabled,
            "harness": str(self.settings.get("harness", "claude-code")),
            "status": status,
            "led": {"color": led.color, "behaviour": led.behaviour, "period": led.period},
            "source": "hooks" if reports else "none",
            "reports": reports,
            "hooks_installed": bool(reports),
            "claude_processes": self._probe_cached(now),
            "selected": selected.as_dict(now, self.stale_after, self.done_hold)
                        if selected is not None else None,
            "pinned": self._pinned,
            "sessions": sessions,
            "effort": {
                "levels": self.effort_levels,
                "reported": selected.effort if selected is not None else "",
                "pending": pending,
                "apply": str((self.settings.get("effort") or {}).get("apply",
                                                                    "slash_command")),
            },
            "dictation": {**self.dictation.preflight(),
                          "state": self.dictation.state,
                          "last_error": self.dictation.last_error},
            "expiry": {"stale_after_s": self.stale_after,
                       "session_ttl_s": self.session_ttl,
                       "done_hold_s": self.done_hold},
        }

    def _probe_cached(self, now: float) -> int | None:
        at, value = self._probe_cache
        if value is not None and now - at < _PROBE_CACHE_S:
            return value
        if self._probe is None:
            return None
        try:
            value = self._probe()
        except Exception:
            value = None
        self._probe_cache = (now, value)
        return value


_warned: set[str] = set()


def _warn(message: str) -> None:
    """One line per distinct cause, on stderr. Matches actions.py: a surface that spams a
    traceback per frame is worse than one that says nothing."""
    if message in _warned:
        return
    _warned.add(message)
    print(f"libremicro: {message}", file=sys.stderr, flush=True)


def reset_warnings() -> None:
    _warned.clear()
