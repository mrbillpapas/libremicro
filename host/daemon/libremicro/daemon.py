"""The daemon: wires the serial link, the renderer, and the web UI together.

Input-event handling is stubbed deliberately. The firmware does not emit input events yet
(that's Phase 2 in docs/ROADMAP.md — it needs three provisional pins re-verified first), so
the plumbing is here and correct but the only thing an event currently does is register
activity and flash the key. Binding dispatch — launch, keyboard shortcut, script, mode —
lands in Phase 3, on top of this.
"""
from __future__ import annotations

import signal
import sys
import threading

from .config import Config, ConfigError
from .dispatch import Dispatcher
from .renderer import Renderer
from .server import serve
from .transport import Link


class Daemon:
    def __init__(self, config: Config, serve_ui: bool | None = None):
        self.cfg = config
        self.battery: dict | None = None      # populated in Phase 8 from the MAX77972
        self._stopping = threading.Event()

        self.link = Link(port=self.cfg.port, baud=self.cfg.baud,
                         layout=self.cfg.layout, on_event=self.handle_event)
        self.dispatcher = Dispatcher(self)
        self.renderer = Renderer(self.link, self.cfg, on_tick=self.dispatcher.tick)

        webui = self.cfg.webui
        self._serve_ui = webui.get("enabled", True) if serve_ui is None else serve_ui
        self._ui_host = webui.get("host", "127.0.0.1")
        self._ui_port = int(webui.get("port", 8777))
        self._httpd = None

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        connected = self.link.ensure_connected()
        print(f"device: {'connected on ' + str(self.link.port) if connected else 'not found (will retry)'}", flush=True)
        self.renderer.start()

        if self._serve_ui:
            try:
                self._httpd = serve(self, self._ui_host, self._ui_port)
                print(f"web UI: http://{self._ui_host}:{self._ui_port}", flush=True)
            except OSError as exc:
                print(f"web UI: failed to bind {self._ui_host}:{self._ui_port} — {exc}",
                      file=sys.stderr, flush=True)

        if not self.cfg.layout.verified:
            print("layout: strip-index mapping is UNVERIFIED — run the identify sweep in "
                  "the web UI (see docs/HARDWARE.md)", flush=True)
        print(f"profile: {self.cfg.active_profile_name}", flush=True)

    def run_forever(self) -> None:
        self.start()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: self._stopping.set())
        try:
            while not self._stopping.wait(0.5):
                pass
        finally:
            self.stop()

    def stop(self) -> None:
        print("\nshutting down", flush=True)
        self.renderer.stop()
        if self._httpd is not None:
            self._httpd.shutdown()
        self.link.clear()
        self.link.close()

    # --- config -------------------------------------------------------------

    def apply_config(self, cfg: Config, persist: bool = False) -> None:
        """Swap in a new config. Keeps the existing file path when the new doc has none."""
        if persist:
            # A config edited in the web UI has no path of its own; inherit the running
            # one's, and let save_path keep edits off the shipped example.
            cfg.path = cfg.path or self.cfg.path
            saved = cfg.save()
            print(f"config saved: {saved}", flush=True)
        self.cfg = cfg
        self.link.configured_port = cfg.port
        self.link.layout = cfg.layout
        self.renderer.set_config(cfg)
        self.dispatcher.config_changed()

    def reload_config(self) -> bool:
        try:
            cfg = Config.load(self.cfg.path)
        except ConfigError as exc:
            print(f"reload failed: {exc}", file=sys.stderr, flush=True)
            return False
        self.apply_config(cfg)
        print("config reloaded", flush=True)
        return True

    # --- events -------------------------------------------------------------

    def handle_event(self, kind: str, args: list[str]) -> None:
        """Called from the serial reader thread for each firmware event line.

        Kept thin on purpose: the reader thread must get back to draining the port, so all
        the work — recognising hold/double, resolving bindings, running actions — happens in
        the dispatcher, and actions themselves are spawned rather than awaited.
        """
        self.dispatcher.feed(kind, args)

    def inject_event(self, kind: str, args: list[str]) -> None:
        """Feed a synthetic event as though it came from the device.

        The firmware doesn't emit input events yet, so without this there'd be no way to
        exercise or demo any binding. The web UI uses it to make clicking a key on screen
        actually launch the app, which also makes the whole dispatch path testable.
        """
        print(f"libremicro: injected event: {kind} {' '.join(args)}", flush=True)
        self.dispatcher.feed(kind, args)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="libremicro",
                                 description="LibreMicro host daemon for the Creator Micro 2")
    ap.add_argument("-c", "--config", help="path to config JSON (default: search standard paths)")
    ap.add_argument("--no-ui", action="store_true", help="don't serve the web UI")
    ap.add_argument("--validate", action="store_true",
                    help="load and validate the config, then exit")
    args = ap.parse_args(argv)

    try:
        cfg = Config.load(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr, flush=True)
        return 2

    print(f"config: {cfg.path}", flush=True)
    if args.validate:
        print("config is valid", flush=True)
        return 0

    try:
        daemon = Daemon(cfg, serve_ui=not args.no_ui)
    except RuntimeError as exc:
        print(f"startup error: {exc}", file=sys.stderr, flush=True)
        return 2
    daemon.run_forever()
    return 0
