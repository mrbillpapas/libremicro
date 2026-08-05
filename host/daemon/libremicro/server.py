"""Local HTTP API and static host for the web UI.

Stdlib only, on purpose: the daemon should install with `pip install pyserial jsonschema`
and nothing else, and the UI is plain files with no build step. This binds to loopback by
default — it exposes the ability to run arbitrary configured commands, so it must not be
reachable from the network without the user explicitly choosing that.
"""
from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import cheatsheet
from . import palettes as palette_mod
from .config import SCHEMA_PATH, Config, ConfigError, validate
from .frame import Frame

WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
MAX_BODY = 4 * 1024 * 1024


class Api:
    """The daemon-facing surface the HTTP layer calls into.

    Split out from the handler so the endpoints stay testable without sockets, and so the
    daemon can hand in whatever it wants to expose.
    """

    def __init__(self, daemon: Any):
        self.daemon = daemon

    # --- reads -------------------------------------------------------------

    def get_config(self) -> dict:
        return self.daemon.cfg.doc

    def get_schema(self) -> dict:
        try:
            return json.loads(SCHEMA_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def get_palettes(self) -> dict:
        return palette_mod.catalog()

    def get_events(self, since: int = 0) -> dict:
        """Recent input events, newest last. `?since=<seq>` returns only newer ones, so the
        UI can poll cheaply instead of needing a streaming connection."""
        events = [e for e in self.daemon.recent_events if e["seq"] > since]
        latest = self.daemon.recent_events[-1]["seq"] if self.daemon.recent_events else 0
        return {"events": events, "latest_seq": latest,
                "input_events_seen": self.daemon.link.saw_input_event}

    def get_frame(self) -> dict:
        """The frame the daemon is putting on the device RIGHT NOW.

        This exists so the web UI can mirror the pad rather than re-implementing every effect
        in JavaScript and hoping the two agree. A second implementation of the same animation
        will always drift — different clock, different rounding, different notion of when a
        cycle started — so the honest way to show what the pad is doing is to ask.
        """
        frame = self.daemon.renderer.compose()
        out = frame.to_hex()
        out["brightness"] = self.daemon.renderer.effective_brightness()
        out["connected"] = self.daemon.link.connected
        return out

    def get_status(self) -> dict:
        d = self.daemon
        return {
            "connected": d.link.connected,
            # Parked is not the same as disconnected: the device is there, we let go of it on
            # purpose. Without this the UI would report a missing pad and invite a reflash.
            "parked": d.link.parked,
            "port": d.link.port,
            "active_profile": d.cfg.active_profile_name,
            "profiles": d.cfg.profile_names,
            "active_mode": d.dispatcher.mode,
            "previewing": d.renderer.previewing,
            "layout_verified": d.cfg.layout.verified,
            "battery": d.battery,
            "input_events": d.link.saw_input_event,
            "keys": self._key_capabilities(),
            # None means the connected firmware didn't answer `ver` — i.e. it's v1, LED-out
            # only. That's the difference between "press a key and nothing happens because
            # you haven't bound it" and "because this firmware can't report presses".
            "firmware": d.link.firmware,
            "watchers": self._watcher_state(),
            "agent": d.agent.snapshot(),
            "cheat_sheet": d.cheat_sheet.state(),
        }

    def agent_status(self, body: dict) -> dict:
        """Ingest one Claude Code hook report. See docs/AGENT-SURFACE.md.

        The hook forwards its stdin verbatim; this reads `hook_event_name` itself rather than
        making the user's shell one-liner do any parsing.
        """
        return self.daemon.agent.ingest(body)

    def _watcher_state(self) -> list | None:
        probe = getattr(self.daemon, "watchers", None)
        if probe is None:
            return None
        try:
            return probe.state()
        except Exception:
            return None

    def _key_capabilities(self) -> dict:
        """Whether keyboard synthesis will actually work, so the UI can warn up front rather
        than letting shortcuts silently do nothing."""
        try:
            from . import keys
            caps = getattr(keys, "capabilities", None)
            if callable(caps):
                return dict(caps() or {})
            return {"available": True, "reason": "no capability reporting"}
        except ImportError as exc:
            return {"available": False, "reason": str(exc)}
        except Exception as exc:
            return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    # --- writes ------------------------------------------------------------

    def put_config(self, body: dict) -> dict:
        self.daemon.renderer.note_activity()
        try:
            cfg = Config(body)
        except ConfigError as exc:
            return {"ok": False, "errors": [str(exc)]}
        errors = [e for e in validate(cfg.doc) if not e.endswith("skipped validation")]
        if errors:
            return {"ok": False, "errors": errors}
        self.daemon.apply_config(cfg, persist=True)
        return {"ok": True, "errors": []}

    def preview_frame(self, body: dict) -> dict:
        self.daemon.renderer.note_activity()
        self.daemon.renderer.preview_frame(Frame.from_hex(body), ttl=float(body.get("ttl", 5.0)))
        return {"ok": True}

    def preview_effect(self, body: dict) -> dict:
        self.daemon.renderer.note_activity()
        spec = body.get("effect") or {}
        try:
            self.daemon.renderer.preview_effect(spec)
        except ValueError as exc:
            return {"ok": False, "errors": [str(exc)]}
        return {"ok": True}

    def preview_stop(self, body: dict) -> dict:
        self.daemon.renderer.note_activity()
        self.daemon.renderer.preview_stop()
        return {"ok": True}

    def identify(self, body: dict) -> dict:
        self.daemon.renderer.note_activity()
        target = body.get("target", "keys")
        index = int(body.get("index", 0))
        # Hold the render loop off the strips entirely — a preview frame would still be a
        # frame and would blank the pixel identify just lit. Default outlasts a 1s step so
        # the LED stays visible; the sweep re-arms it on each call.
        self.daemon.renderer.hold(float(body.get("hold", 4.0)))
        ok = self.daemon.link.identify(target, index)
        return {"ok": ok, "connected": self.daemon.link.connected}

    def simulate(self, body: dict) -> dict:
        """Inject a synthetic input event, as if the pad had sent it.

        This is how bindings are usable and testable before firmware v2 exists. Accepts
        either a raw firmware line (`{"line": "key 3 down"}`) or its parts
        (`{"event": "key", "args": ["3", "down"]}`). A bare key index is expanded to a full
        down/up pair, since that's what "click this key" means.
        """
        line = (body.get("line") or "").strip()
        if line:
            parts = line.split()
            event, args = parts[0], parts[1:]
        elif body.get("event"):
            event = str(body["event"])
            args = [str(a) for a in (body.get("args") or [])]
        elif body.get("key") is not None:
            index = int(body["key"])
            self.daemon.inject_event("key", [str(index), "down"])
            if body.get("hold_s"):
                # Let the recogniser see a genuine hold rather than faking the trigger.
                import time as _t
                _t.sleep(min(2.0, float(body["hold_s"])))
            self.daemon.inject_event("key", [str(index), "up"])
            return {"ok": True, "injected": f"key {index} down/up"}
        else:
            return {"ok": False, "errors": ["give one of: line, event+args, or key"]}

        self.daemon.inject_event(event, args)
        return {"ok": True, "injected": f"{event} {' '.join(args)}".strip()}

    def cheat_sheet(self, body: dict) -> dict:
        """`{"show": "toggle"|"show"|"hide"}`. Also serves the built sheet back, so the web UI
        can show exactly what went on screen — and so it's inspectable when the helper isn't
        built and nothing appears."""
        what = str(body.get("show", "toggle"))
        sheet = self.daemon.cheat_sheet
        fn = {"toggle": sheet.toggle, "show": sheet.show, "hide": sheet.hide}.get(what)
        if fn is None:
            return {"ok": False, "errors": [f"unknown cheat sheet action {what!r}"]}
        ok = fn()
        out = {"ok": True, "acted": ok, **sheet.state()}
        try:
            out["sheet"] = cheatsheet.build(self.daemon.cfg, self.daemon.dispatcher)
        except Exception as exc:
            out["errors"] = [f"could not build the sheet: {exc}"]
        return out

    def release(self, body: dict) -> dict:
        """Hand the pad to another program — the first half of reverting to stock firmware.

        `next_steps` is served rather than hard-coded in the UI so the instructions can't
        drift from what the daemon actually did.
        """
        port = self.daemon.link.port
        self.daemon.release_device()
        return {
            "ok": True,
            "parked": True,
            "port": port,
            "next_steps": [
                "Open Work Louder Input. It polls for flashable boards once a second and "
                "should offer \"Found device in bootloader mode, click here to reflash\".",
                "Let it flash. It writes the stock image at offset 0 without a full erase, so "
                "BLE pairing (nvs) and the vendor keymap (fs) survive.",
                "The pad reboots into stock firmware. LibreMicro will not touch it again "
                "until you POST /api/reclaim or restart the daemon.",
            ],
        }

    def reclaim(self, body: dict) -> dict:
        ok = self.daemon.reclaim_device()
        return {"ok": ok, "parked": self.daemon.link.parked,
                "connected": self.daemon.link.connected, "port": self.daemon.link.port,
                "errors": [] if ok else ["no device answered on the port"]}

    def set_profile(self, body: dict) -> dict:
        target = body.get("profile") or "next"
        result = self.daemon.dispatcher.switch_profile(str(target))
        return {"ok": bool(result), "errors": [] if result else [result.detail],
                "active_profile": self.daemon.cfg.active_profile_name}

    def set_mode(self, body: dict) -> dict:
        name = body.get("mode")
        d = self.daemon.dispatcher
        if name in (None, "", "none"):
            with d._lock:
                d._exit_mode()
            return {"ok": True, "active_mode": None}
        if name not in d.modes():
            return {"ok": False, "errors": [f"no mode named {name!r}"]}
        d.recognizer.reset()
        with d._lock:
            d._mode = str(name)
        self.daemon.renderer.set_mode(str(name))
        return {"ok": True, "active_mode": d.mode}

    def export(self) -> dict:
        return self.daemon.cfg.export_bundle()

    def import_bundle(self, body: dict) -> dict:
        try:
            cfg = Config.import_bundle(body)
        except ConfigError as exc:
            return {"ok": False, "errors": [str(exc)]}
        self.daemon.apply_config(cfg, persist=True)
        return {"ok": True, "errors": []}


class _Handler(BaseHTTPRequestHandler):
    api: Api
    server_version = "LibreMicro"

    # --- routing -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path, _, query = self.path.partition("?")
        if path == "/api/events":
            since = 0
            for part in query.split("&"):
                if part.startswith("since="):
                    try:
                        since = int(part[6:])
                    except ValueError:
                        since = 0
            self._json(200, self.api.get_events(since))
            return
        routes: dict[str, Callable[[], Any]] = {
            "/api/config": self.api.get_config,
            "/api/schema": self.api.get_schema,
            "/api/palettes": self.api.get_palettes,
            "/api/status": self.api.get_status,
            "/api/export": self.api.export,
            "/api/frame": self.api.get_frame,
        }
        if path in routes:
            self._json(200, routes[path]())
            return
        self._static(path)

    def do_PUT(self) -> None:  # noqa: N802
        self._mutate({"/api/config": self.api.put_config})

    def do_POST(self) -> None:  # noqa: N802
        self._mutate({
            "/api/config": self.api.put_config,
            "/api/preview/frame": self.api.preview_frame,
            "/api/preview/effect": self.api.preview_effect,
            "/api/preview/stop": self.api.preview_stop,
            "/api/identify": self.api.identify,
            "/api/import": self.api.import_bundle,
            "/api/simulate": self.api.simulate,
            "/api/profile": self.api.set_profile,
            "/api/mode": self.api.set_mode,
            "/api/agent/status": self.api.agent_status,
            "/api/release": self.api.release,
            "/api/reclaim": self.api.reclaim,
            "/api/cheatsheet": self.api.cheat_sheet,
        })

    def _mutate(self, routes: dict[str, Callable[[dict], Any]]) -> None:
        path = self.path.split("?", 1)[0]
        handler = routes.get(path)
        if handler is None:
            self._json(404, {"ok": False, "errors": [f"no route {path}"]})
            return
        try:
            body = self._read_json()
        except ValueError as exc:
            self._json(400, {"ok": False, "errors": [str(exc)]})
            return
        try:
            result = handler(body)
        except Exception as exc:
            self._json(500, {"ok": False, "errors": [f"{type(exc).__name__}: {exc}"]})
            return
        self._json(200 if result.get("ok", True) else 422, result)

    # --- io ----------------------------------------------------------------

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("request body too large")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc

    def _json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (WEBUI_DIR / rel).resolve()
        # Refuse anything that escapes the webui directory.
        if not str(target).startswith(str(WEBUI_DIR.resolve())) or not target.is_file():
            self._json(404, {"ok": False, "errors": [f"not found: {path}"]})
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Default logging writes a line per request to stderr, which buries daemon output.
        pass


def serve(daemon: Any, host: str = "127.0.0.1", port: int = 8777) -> ThreadingHTTPServer:
    """Start the API server on a background thread and return it."""
    handler = type("Handler", (_Handler,), {"api": Api(daemon)})
    httpd = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=httpd.serve_forever, name="lm-http", daemon=True).start()
    return httpd
