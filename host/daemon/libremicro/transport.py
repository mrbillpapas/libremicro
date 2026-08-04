"""Serial link to the CM2.

The firmware protocol is newline-delimited ASCII over USB-Serial-JTAG (docs/PROTOCOL.md).
Two things about it shape this module:

Bandwidth. At 115200 baud the link carries ~11.5 KB/s. A naive full frame is 21 per-LED
commands of ~10 bytes, so 30 fps costs ~6.3 KB/s — over half the link, before acks. So
`send_frame` diffs against the last frame and only writes pixels that actually changed,
which for most effects is a small fraction. A batched frame command would remove the
problem entirely; see the `kf`/`uf` proposal in docs/PROTOCOL.md.

Direction sharing. Command acks (`ok`/`err`) and input events (`key`/`enc`/`touch`/`rear`)
arrive on the same stream, so the reader classifies by line prefix and never blocks the
render loop waiting for an ack.
"""
from __future__ import annotations

import glob
import queue
import threading
import time
from typing import Callable, Iterable

from .color import to_hex
from .frame import Frame
from .layout import KEY_N, STATUS_N, UNDERGLOW_N, Layout

try:
    import serial  # type: ignore
except ImportError:  # pragma: no cover - surfaced at startup with a clear message
    serial = None

EVENT_PREFIXES = ("key", "enc", "touch", "rear", "batt")


def find_port(explicit: str | None = None) -> str | None:
    """Resolve a configured port. 'auto' or None picks the first usbmodem device."""
    if explicit and explicit != "auto":
        return explicit
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    return ports[0] if ports else None


class Link:
    """A connection to the device. Safe to use with no device attached.

    When the port is missing or disappears, the link goes to `connected = False` and
    silently drops writes rather than raising — the daemon and web UI stay usable for
    editing, and `ensure_connected` picks the device back up when it returns.
    """

    def __init__(self, port: str | None = "auto", baud: int = 115200,
                 layout: Layout | None = None,
                 on_event: Callable[[str, list[str]], None] | None = None):
        if serial is None:
            raise RuntimeError("pyserial missing: pip install pyserial")
        self.configured_port = port
        self.baud = baud
        self.layout = layout or Layout()
        self.on_event = on_event
        self.port: str | None = None
        self._ser = None
        # Reentrant: a failed write calls _drop(), which takes this lock too.
        self._lock = threading.RLock()
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._last: Frame | None = None
        self._last_brightness: int | None = None
        self.events: "queue.Queue[tuple[str, list[str]]]" = queue.Queue()
        self._retry_after = 0.0
        # Whether this firmware has ever sent an input event. Lets the UI distinguish
        # "v1 firmware, LED-out only" from "v2, but you haven't pressed anything yet".
        self.saw_input_event = False

    # --- connection ---------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._ser is not None and getattr(self._ser, "is_open", False)

    def ensure_connected(self) -> bool:
        """Connect if not already. Rate-limited so a missing device isn't a busy loop."""
        if self.connected:
            return True
        now = time.monotonic()
        if now < self._retry_after:
            return False
        self._retry_after = now + 2.0

        port = find_port(self.configured_port)
        if not port:
            return False
        try:
            # write_timeout keeps a wedged device from blocking the render loop forever.
            self._ser = serial.Serial(port, self.baud, timeout=0.2, write_timeout=0.5)
        except Exception:
            self._ser = None
            return False

        self.port = port
        time.sleep(0.2)
        try:
            self._ser.reset_input_buffer()
        except Exception:
            pass
        # Force a full resend: we have no idea what's on the strips after a reconnect.
        self._last = None
        self._last_brightness = None
        self._start_reader()
        return True

    def close(self) -> None:
        self._stop.set()
        if self._reader:
            self._reader.join(timeout=1.0)
            self._reader = None
        with self._lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None

    def _start_reader(self) -> None:
        if self._reader and self._reader.is_alive():
            return
        self._stop.clear()
        self._reader = threading.Thread(target=self._read_loop, name="lm-serial-read",
                                        daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        buf = b""
        while not self._stop.is_set():
            ser = self._ser
            if ser is None:
                time.sleep(0.2)
                continue
            try:
                chunk = ser.read(256)
            except Exception:
                self._drop()
                continue
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                self._handle_line(raw.decode("utf-8", "replace").strip())

    def _handle_line(self, line: str) -> None:
        if not line:
            return
        head, *rest = line.split()
        if head in EVENT_PREFIXES:
            if head != "batt":
                self.saw_input_event = True
            self.events.put((head, rest))
            if self.on_event:
                try:
                    self.on_event(head, rest)
                except Exception:
                    # A misbehaving handler must not kill the reader thread.
                    pass

    def _drop(self) -> None:
        with self._lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:
                    pass
            self._ser = None
        self.port = None
        self._last = None

    # --- writing ------------------------------------------------------------

    def send(self, *lines: str) -> bool:
        """Write command lines. Returns False if the device isn't there."""
        if not lines:
            return self.connected
        if not self.ensure_connected():
            return False
        payload = "".join(l.rstrip() + "\n" for l in lines).encode()
        failed = False
        with self._lock:
            ser = self._ser
            if ser is None:
                return False
            try:
                # No flush() here on purpose. pyserial's flush is tcdrain(), which blocks
                # until the device has drained every byte — at 30 fps that serialises the
                # render loop against USB completion, and if the device stops draining it
                # blocks forever with no timeout. write_timeout bounds the write instead.
                ser.write(payload)
            except Exception:
                failed = True
        if failed:
            # Outside the lock: _drop() takes it too.
            self._drop()
            return False
        return True

    def set_brightness(self, value: int) -> None:
        value = max(0, min(255, int(value)))
        if value != self._last_brightness:
            if self.send(f"bright {value}"):
                self._last_brightness = value

    def clear(self) -> None:
        if self.send("clear"):
            self._last = Frame.blank()

    def send_frame(self, frame: Frame, force: bool = False) -> bool:
        """Push a frame, writing only the pixels that changed since the last one."""
        if not self.ensure_connected():
            return False

        prev = None if force else self._last
        lines = list(self._frame_lines(frame, prev))
        if not lines:
            return True
        if self.send(*lines):
            self._last = frame.copy()
            return True
        return False

    def _frame_lines(self, frame: Frame, prev: Frame | None) -> Iterable[str]:
        """Command lines needed to turn `prev` into `frame`.

        Uniform zones collapse to a single `all` write, which is both fewer bytes and
        fewer firmware refreshes than 13 individual pixels.
        """
        l2s = self.layout.logical_to_strip
        keys_uniform = len(set(frame.keys)) == 1
        under_uniform = len(set(frame.under)) == 1

        if keys_uniform and (prev is None or prev.keys != frame.keys):
            yield f"k all {to_hex(frame.keys[0])}"
        elif not keys_uniform:
            for logical in range(min(KEY_N, len(frame.keys))):
                c = frame.keys[logical]
                if prev is not None and prev.keys[logical] == c:
                    continue
                yield f"k {l2s[logical]} {to_hex(c)}"

        s2r = self.layout.strip_to_ring
        if under_uniform and (prev is None or prev.under != frame.under):
            yield f"u all {to_hex(frame.under[0])}"
        elif not under_uniform:
            for strip_i in range(min(UNDERGLOW_N, len(frame.under))):
                c = frame.under[s2r[strip_i]]
                if prev is not None and prev.under[s2r[strip_i]] == c:
                    continue
                yield f"u {strip_i} {to_hex(c)}"

        for i in range(min(STATUS_N, len(frame.status))):
            duty = max(0, min(255, int(frame.status[i])))
            if prev is not None and prev.status[i] == duty:
                continue
            yield f"t {i} {duty}"

    # --- one-off helpers ----------------------------------------------------

    def identify(self, target: str, index: int) -> bool:
        """Light exactly one LED, everything else off — for the layout identify sweep."""
        zone = "u" if target == "underglow" else "k"
        limit = UNDERGLOW_N if zone == "u" else KEY_N
        index = max(0, min(limit - 1, int(index)))
        self._last = None  # identify writes raw strip indices, bypassing the frame diff
        return self.send("clear", f"{zone} {index} ffffff")
