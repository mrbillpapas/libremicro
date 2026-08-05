"""The daemon: wires the serial link, the renderer, the input dispatcher, the notification
watchers, and the web UI together.

Binding dispatch is fully implemented (see dispatch.py). The one gap is upstream: firmware v2
emits input events but is not yet flashed, so in practice events currently arrive through
`inject_event` from `POST /api/simulate` rather than from the pad. Both paths are identical
from the dispatcher down, which is what makes the simulated one worth having.
"""
from __future__ import annotations

import itertools
import signal
import sys
import threading
import time
from collections import deque

from .agent_surface import AgentSurface
from .cheatsheet import CheatSheet
from .config import Config, ConfigError
from .dispatch import Dispatcher
from .renderer import Renderer
from .server import serve
from .transport import Link
from .watchers import Watchers


class Daemon:
    def __init__(self, config: Config, serve_ui: bool | None = None):
        self.cfg = config
        self.battery: dict | None = None      # populated in Phase 8 from the MAX77972
        self._stopping = threading.Event()
        # A short history of what the pad sent, so the web UI can show which index arrived
        # when you press a physical key — that's how the matrix-to-physical mapping gets
        # confirmed, the same way the identify sweep confirmed the LED wiring.
        self.recent_events: deque[dict] = deque(maxlen=200)
        self._event_seq = itertools.count(1)

        self.link = Link(port=self.cfg.port, baud=self.cfg.baud,
                         layout=self.cfg.layout, on_event=self.handle_event)
        self.dispatcher = Dispatcher(self)
        self.agent = AgentSurface(self)
        self.agent.extend_actions(self.dispatcher.actions)
        self.renderer = Renderer(self.link, self.cfg, on_tick=self._tick)
        # Notification watchers poll on their own threads and drive Renderer.pulse; nothing
        # here touches the input or render path. `self.watchers.state()` is the read-only
        # view of why a key is or isn't pulsing.
        self.watchers = Watchers(self)
        # The cheat sheet is pure host-side: it needs no device and no firmware, so it works
        # while the link is parked or the pad is running someone else's firmware entirely.
        hud = self.cfg.device.get("cheat_sheet") or {}
        self.cheat_sheet = CheatSheet(self,
                                     corner=str(hud.get("corner", "bottom-left")),
                                     timeout_s=float(hud.get("timeout_s", 0) or 0))

        webui = self.cfg.webui
        self._serve_ui = webui.get("enabled", True) if serve_ui is None else serve_ui
        self._ui_host = webui.get("host", "127.0.0.1")
        self._ui_port = int(webui.get("port", 8777))
        self._httpd = None

    def _tick(self, now: float) -> None:
        """Frame tick, fanned out — Renderer.on_tick takes a single callable."""
        self.dispatcher.tick(now)
        self.agent.tick(now)

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        connected = self.link.ensure_connected()
        print(f"device: {'connected on ' + str(self.link.port) if connected else 'not found (will retry)'}", flush=True)
        self.renderer.start()
        self.watchers.start()

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
        self.watchers.stop()
        self.agent.close()
        self.cheat_sheet.hide()      # never leave a panel pinned over the user's screen
        self.renderer.stop()
        if self._httpd is not None:
            self._httpd.shutdown()
        self.link.clear()
        self.link.close()

    # --- handing the device to another program ------------------------------

    def release_device(self) -> bool:
        """Blank the pad, drop the serial port, and stop reconnecting.

        This is the whole of what "revert to stock" needs from us. Work Louder's Input app
        finds a flashable board by listing *serial* ports and matching VID 0x303A with
        manufacturer "Espressif" (`WLDeviceDiscovery.findWLBootloaderDevices`), which the
        ESP32-S3's USB-Serial-JTAG console already reports while our firmware is running — so
        the app sees the pad as a bootloader device with no priming from us. It then flashes
        through esptool-js, whose `UsbJtagSerialReset` drives the chip into download mode over
        DTR/RTS in hardware, regardless of what firmware is running.

        So the only thing standing in the app's way is this daemon: it holds
        /dev/cu.usbmodem* open and re-opens it every two seconds. Hence release, not "prime".
        """
        if self.link.parked:
            return True
        # Blank first, while we still have the port. Otherwise the last frame stays latched
        # on the strips for the whole flash — the firmware has no host-disconnect timeout.
        self.link.clear()
        port = self.link.port
        self.link.park()
        print(f"device: released {port or '(none)'} — the pad is now free for another program "
              f"(Work Louder Input, esptool). POST /api/reclaim to take it back.", flush=True)
        return True

    def reclaim_device(self) -> bool:
        """Undo release_device. False means nothing answered on the port."""
        ok = self.link.unpark()
        print(f"device: {'reclaimed on ' + str(self.link.port) if ok else 'reclaim found no device (will retry)'}",
              flush=True)
        return ok

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
        self.watchers.config_changed()
        self.agent.config_changed()

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
        self._record_event(kind, args, source="device")
        self.dispatcher.feed(kind, args)

    def _record_event(self, kind: str, args: list[str], source: str) -> None:
        self.recent_events.append({
            "seq": next(self._event_seq),
            "at": time.time(),
            "source": source,
            "event": kind,
            "args": list(args),
            "line": " ".join([kind, *args]),
        })

    def inject_event(self, kind: str, args: list[str]) -> None:
        """Feed a synthetic event as though it came from the device.

        The firmware doesn't emit input events yet, so without this there'd be no way to
        exercise or demo any binding. The web UI uses it to make clicking a key on screen
        actually launch the app, which also makes the whole dispatch path testable.
        """
        print(f"libremicro: injected event: {kind} {' '.join(args)}", flush=True)
        self._record_event(kind, args, source="injected")
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
