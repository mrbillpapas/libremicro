"""The on-screen cheat sheet: build a picture of the live bindings, hand it to `lmhud`.

Thirteen unlabelled keycaps is the pad's one real ergonomic problem, and it gets worse the more
you bind. This module answers "what does this key do again?" without opening the web UI.

**Two jobs, kept apart.** Turning config into labels is pure Python and fully testable
(`build`); putting pixels on screen needs AppKit and lives in `host/swift/lmhud.swift`. The
seam between them is one JSON document, so the label logic can be tested with no window server,
no helper binary and no device — which is the whole reason it's a seam and not one function.

**Visibility is process liveness.** The sheet is up exactly as long as the helper process is
alive: showing spawns it, hiding sends SIGTERM. There is no "is it still showing?" query to get
out of sync with reality, and a daemon that dies takes its panel with it rather than leaving one
pinned over the user's screen. The cost is that a mode change re-renders by respawning, which is
about 50 ms and invisible in practice.

That also means closing the panel with its own × needs no plumbing back to here: the process
exits, `visible` goes false on its own, and the next toggle opens a fresh one. The helper owns
its dragged position across those respawns, so this module never sees a coordinate.

Like `keys.py`, nothing here raises into the dispatch path: an unbuilt helper or a headless
session produces one warning and a False, not a traceback per keypress.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import threading
from pathlib import Path

from . import events
from .layout import KEY_ROWS

HELPER_NAME = "lmhud"
HELPER_ENV = "LIBREMICRO_LMHUD"

#: host/swift/lmhud, resolved from this file: libremicro/ -> daemon/ -> host/.
DEFAULT_HELPER = Path(__file__).resolve().parents[2] / "swift" / HELPER_NAME

BUILD_HINT = f"build it with: cd host/swift && swiftc -O -o {HELPER_NAME} {HELPER_NAME}.swift"

GRID = 4
#: Which grid slot the non-key controls occupy. The pad is a 4x4 grid holding 13 keycaps plus
#: the encoder (top-left), the joystick (top-right) and the touch pad (bottom-left) — that is
#: 13 + 3 = 16, so the grid is exactly full and these three are the slots `KEY_ROWS` skips.
#: Derived here rather than hard-coded per row so a different `key_rows` still lands somewhere
#: sensible: a row short of GRID gets its gap filled from this table, in order.
CONTROL_SLOTS = {
    (0, 0): events.ENCODER,
    (0, GRID - 1): events.JOYSTICK,
    (len(KEY_ROWS) - 1, 0): events.TOUCH,
}

#: Trigger kinds worth showing on a key, in the order a label should prefer them. `press` is
#: what a key does when you press it, so it wins; the rest only surface when nothing else does.
KEY_KINDS = (events.PRESS, events.HOLD, events.DOUBLE, events.RELEASE)

_warned: set[str] = set()
_lock = threading.Lock()


def _warn(key: str, message: str) -> None:
    with _lock:
        if key in _warned:
            return
        _warned.add(key)
    print(f"libremicro: {message}", flush=True)


def helper_path() -> Path | None:
    """Where the built helper is, or None if it isn't built. Re-checked every call, so building
    it while the daemon runs just starts working."""
    override = os.environ.get(HELPER_ENV)
    if override is not None:
        p = Path(override).expanduser()
        return p if _executable(p) else None
    if _executable(DEFAULT_HELPER):
        return DEFAULT_HELPER
    found = shutil.which(HELPER_NAME)
    return Path(found) if found else None


def _executable(p: Path) -> bool:
    try:
        return p.is_file() and os.access(p, os.X_OK)
    except OSError:
        return False


# --- turning bindings into labels -------------------------------------------------

def label_for(binding: dict | None) -> tuple[str, str]:
    """A binding as `(label, detail)`. `("", "")` means nothing is bound.

    The label answers "what does this do" in as few characters as a 52 pt tile can hold, so it
    prefers the concrete noun — the app, the profile, the mode — over the mechanism. The detail
    line carries the mechanism, which is what you want when two keys have the same label.
    """
    if not isinstance(binding, dict):
        return ("", "")
    if "launch" in binding:
        return (str(binding["launch"]), "launch")
    if "shortcut" in binding:
        return (_pretty_chord(str(binding["shortcut"])), "shortcut")
    if "text" in binding:
        one = " ".join(str(binding["text"]).split())
        return (one[:18] + ("…" if len(one) > 18 else ""), "type")
    if "shell" in binding:
        return (_command_head(str(binding["shell"])), "shell")
    if "script" in binding:
        return (Path(str(binding["script"])).name, "script")
    if "applescript" in binding:
        return ("AppleScript", "run")
    if "mode" in binding:
        return (str(binding["mode"]), "mode")
    if "profile" in binding:
        return (str(binding["profile"]), "profile")
    if "action" in binding:
        token = str(binding["action"])
        return (token.replace("_", " "), "built-in")
    return ("", "")


def _command_head(command: str) -> str:
    """The recognisable part of a shell command: the program, or `open`'s target.

    shlex, not `split()`: `open -na 'Google Chrome'` is the single most likely shell binding on
    this pad, and naive splitting labels that key `'Google`.
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts:
        return "shell"
    if parts[0] == "open" and len(parts) > 1:
        target = [p for p in parts[1:] if not p.startswith("-")]
        if target:
            return Path(target[0]).name[:18]
    return Path(parts[0]).name[:18]


def _pretty_chord(spec: str) -> str:
    """`cmd+shift+4` as `⌘⇧4`, because the glyphs are what's printed on the user's keyboard."""
    glyphs = {"cmd": "⌘", "ctrl": "⌃", "opt": "⌥", "alt": "⌥", "shift": "⇧", "fn": "fn"}
    parts = spec.split("+")
    return "".join(glyphs.get(p.lower(), p.upper() if len(p) == 1 else p.title())
                   for p in parts)


def _key_label(key_spec: dict | None, declared: str | None) -> tuple[str, str]:
    """A key's label. An explicit `label` in the config always wins — it's the user's own name
    for the key, and second-guessing it with a derived one would be worse."""
    binding = None
    for kind in KEY_KINDS:
        binding = (key_spec or {}).get(kind)
        if binding:
            break
    derived, detail = label_for(binding)
    if declared:
        # Keep the detail only when it says something the declared label doesn't.
        return (str(declared), detail if derived.lower() != str(declared).lower() else "")
    return (derived, detail)


def build(cfg, dispatcher) -> dict:
    """The sheet, as the JSON document `lmhud` reads. Pure: no helper, no device, no window."""
    profile = cfg.profile()
    declared: dict[int, str] = {}
    for k in profile.get("keys") or []:
        if isinstance(k, dict) and isinstance(k.get("index"), int) and k.get("label"):
            declared[k["index"]] = str(k["label"])

    rows: list[list[dict]] = []
    logical = 0
    for r, count in enumerate(KEY_ROWS):
        row: list[dict] = []
        placed = 0
        for c in range(GRID):
            control = CONTROL_SLOTS.get((r, c))
            # A control slot is only honoured while this row has a gap to spare; otherwise a
            # config with wider rows would silently lose a keycap to the encoder.
            if control is not None and count < GRID:
                row.append(_control_cell(control, dispatcher))
            elif placed < count:
                row.append(_key_cell(logical, declared.get(logical), dispatcher))
                logical += 1
                placed += 1
            else:
                row.append({"kind": "empty"})
        rows.append(row)

    return {
        "title": cfg.active_profile_name,
        "mode": dispatcher.mode,
        "rows": rows,
    }


def _key_cell(index: int, declared: str | None, dispatcher) -> dict:
    spec = {}
    for kind in KEY_KINDS:
        b = dispatcher.resolve(events.KEY, index, kind)
        if b:
            spec[kind] = b
    label, detail = _key_label(spec, declared)
    cell = {"kind": "key"}
    if label:
        cell["label"] = label
        if detail:
            cell["detail"] = detail
    return cell


def _control_cell(control: str, dispatcher) -> dict:
    """The encoder, joystick and touch pad each collapse to one tile, so each gets the label of
    its primary gesture and a detail naming the secondary one."""
    if control == events.ENCODER:
        turn, _ = label_for(dispatcher.resolve(events.ENCODER, 0, events.CW))
        press, _ = label_for(dispatcher.resolve(events.ENCODER, 0, events.PRESS))
        cell = {"kind": "encoder", "label": turn or "dial"}
        if press:
            cell["detail"] = f"press: {press}"
        return cell
    if control == events.JOYSTICK:
        bound = []
        for i, name in enumerate(events.JOY_DIRS):
            label, _ = label_for(dispatcher.resolve(events.JOYSTICK, i, events.PRESS))
            if label:
                bound.append((name, label))
        if not bound:
            return {"kind": "joystick"}
        if len(bound) == 1:
            return {"kind": "joystick", "label": bound[0][1]}
        return {"kind": "joystick", "label": "stick", "detail": f"{len(bound)} dirs"}
    label, detail = label_for(dispatcher.resolve(events.TOUCH, 0, events.PRESS))
    cell = {"kind": "touch"}
    if label:
        cell["label"] = label
        if detail:
            cell["detail"] = detail
    return cell


# --- showing it -------------------------------------------------------------------

class CheatSheet:
    """Owns the one helper process. Not thread-safe against itself by accident: every public
    method takes the lock, because a key bound to `cheat_sheet` can be double-tapped faster
    than a process spawns."""

    def __init__(self, daemon, corner: str = "bottom-left", timeout_s: float = 0.0):
        self.d = daemon
        self.corner = corner
        self.timeout_s = timeout_s
        self._proc: subprocess.Popen | None = None
        self._lock = threading.RLock()

    @property
    def visible(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def toggle(self) -> bool:
        with self._lock:
            return self.hide() if self.visible else self.show()

    def show(self) -> bool:
        with self._lock:
            self.hide()
            path = helper_path()
            if path is None:
                _warn("missing", f"{HELPER_NAME} helper not found at "
                                 f"{os.environ.get(HELPER_ENV) or DEFAULT_HELPER} — the cheat "
                                 f"sheet is disabled. {BUILD_HINT}")
                return False
            try:
                sheet = build(self.d.cfg, self.d.dispatcher)
            except Exception as exc:      # a config we can't describe must not kill a keypress
                _warn("build", f"could not build the cheat sheet: {exc}")
                return False

            args = [str(path), "show", "--corner", self.corner]
            if self.timeout_s > 0:
                args += ["--timeout", str(self.timeout_s)]
            try:
                proc = subprocess.Popen(args, stdin=subprocess.PIPE,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                assert proc.stdin is not None
                proc.stdin.write(json.dumps(sheet).encode())
                proc.stdin.close()
            except OSError as exc:
                _warn("spawn", f"could not run {path}: {exc}. {BUILD_HINT}")
                return False
            self._proc = proc
            return True

    def hide(self) -> bool:
        with self._lock:
            proc, self._proc = self._proc, None
            if proc is None or proc.poll() is not None:
                return False
            try:
                proc.terminate()
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
            except OSError:
                pass
            return True

    def refresh(self) -> None:
        """Re-render if the sheet is up. Called when the mode or profile changes — a cheat sheet
        showing the previous mode's bindings is worse than no cheat sheet."""
        with self._lock:
            if self.visible:
                self.show()

    def state(self) -> dict:
        path = helper_path()
        return {
            "visible": self.visible,
            "helper": str(path) if path else None,
            "expected_at": str(DEFAULT_HELPER),
            "built": path is not None,
            "corner": self.corner,
            **({} if path else {"hint": BUILD_HINT}),
        }
