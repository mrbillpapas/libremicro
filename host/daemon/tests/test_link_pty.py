"""End-to-end transport tests over a pseudo-terminal.

test_core.py exercises `Link._frame_lines` as a pure function. This file drives the real
`Link` — actual pyserial, actual reader thread, actual byte framing — against a PTY
standing in for the device, and asserts on the exact bytes that would reach the firmware.

That covers the parts a pure-function test can't: that writes are newline-framed and
flushed, that the frame diff is maintained across successive sends, that a disconnect is
survived, and that inbound event lines are parsed and dispatched off the read thread.

Run: python -m unittest discover -s host/daemon/tests
"""
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libremicro.color import parse_hex  # noqa: E402
from libremicro.frame import Frame  # noqa: E402
from libremicro.layout import Layout  # noqa: E402
from libremicro.transport import Link  # noqa: E402


class PtyDevice:
    """A PTY pair posing as the CM2: `path` is what Link opens, `read_lines` is what the
    firmware would have received."""

    def __init__(self):
        self.master, self.slave = os.openpty()
        self.path = os.ttyname(self.slave)
        os.set_blocking(self.master, False)
        self._buf = b""

    def read_lines(self, settle: float = 0.25) -> list[str]:
        deadline = time.monotonic() + settle
        while time.monotonic() < deadline:
            try:
                chunk = os.read(self.master, 65536)
            except (BlockingIOError, OSError):
                chunk = b""
            if chunk:
                self._buf += chunk
                deadline = time.monotonic() + 0.05   # keep draining while data flows
            else:
                time.sleep(0.01)
        out, _, self._buf = self._buf.rpartition(b"\n")
        return [l for l in out.decode("utf-8", "replace").split("\n") if l]

    def write_line(self, line: str) -> None:
        os.write(self.master, (line + "\n").encode())

    def close(self) -> None:
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


class TestLinkOverPty(unittest.TestCase):
    def setUp(self):
        self.dev = PtyDevice()
        self.events: list[tuple[str, list[str]]] = []
        self.link = Link(port=self.dev.path, layout=Layout({}),
                         on_event=lambda k, a: self.events.append((k, a)))
        self.assertTrue(self.link.ensure_connected(), "Link failed to open the PTY")
        self.dev.read_lines(0.1)   # discard anything from connection setup

    def tearDown(self):
        self.link.close()
        self.dev.close()

    # --- writing ------------------------------------------------------------

    def test_connects_and_reports_port(self):
        self.assertTrue(self.link.connected)
        self.assertEqual(self.link.port, self.dev.path)

    def test_uniform_frame_reaches_device_as_two_commands(self):
        f = Frame([parse_hex("ff0000")] * 13, [parse_hex("0000ff")] * 8)
        self.assertTrue(self.link.send_frame(f))
        lines = self.dev.read_lines()
        self.assertIn("k all ff0000", lines)
        self.assertIn("u all 0000ff", lines)

    def test_successive_frames_send_only_the_delta(self):
        base = Frame([parse_hex("101010")] * 13, [parse_hex("202020")] * 8)
        self.link.send_frame(base)
        self.dev.read_lines()

        nxt = base.copy()
        nxt.keys[7] = parse_hex("00ff88")
        self.link.send_frame(nxt)
        # Logical 7 -> its strip index under the confirmed serpentine wiring.
        strip = self.link.layout.logical_to_strip[7]
        self.assertEqual(self.dev.read_lines(), [f"k {strip} 00ff88"])

    def test_unchanged_frame_writes_nothing(self):
        f = Frame([parse_hex("123456")] * 13)
        self.link.send_frame(f)
        self.dev.read_lines()
        self.link.send_frame(f.copy())
        self.assertEqual(self.dev.read_lines(), [])

    def test_force_resends_the_whole_frame(self):
        f = Frame([parse_hex("123456")] * 13, [parse_hex("654321")] * 8)
        self.link.send_frame(f)
        self.dev.read_lines()
        self.link.send_frame(f.copy(), force=True)
        lines = self.dev.read_lines()
        self.assertIn("k all 123456", lines)
        self.assertIn("u all 654321", lines)

    def test_brightness_is_deduplicated(self):
        self.link.set_brightness(200)
        self.assertEqual(self.dev.read_lines(), ["bright 200"])
        self.link.set_brightness(200)
        self.assertEqual(self.dev.read_lines(), [])
        self.link.set_brightness(64)
        self.assertEqual(self.dev.read_lines(), ["bright 64"])

    def test_brightness_is_clamped(self):
        self.link.set_brightness(999)
        self.assertEqual(self.dev.read_lines(), ["bright 255"])
        self.link.set_brightness(-5)
        self.assertEqual(self.dev.read_lines(), ["bright 0"])

    def test_identify_clears_then_lights_one_pixel(self):
        self.assertTrue(self.link.identify("underglow", 3))
        self.assertEqual(self.dev.read_lines(), ["clear", "u 3 ffffff"])

    def test_identify_clamps_out_of_range_index(self):
        self.link.identify("keys", 99)
        self.assertEqual(self.dev.read_lines(), ["clear", "k 12 ffffff"])

    def test_clear_resets_the_diff_baseline(self):
        f = Frame([parse_hex("ff0000")] * 13)
        self.link.send_frame(f)
        self.dev.read_lines()
        self.link.clear()
        self.assertEqual(self.dev.read_lines(), ["clear"])
        # After a clear the device is black, so the same frame must be sent again.
        self.link.send_frame(f.copy())
        self.assertIn("k all ff0000", self.dev.read_lines())

    def test_status_led_duties_are_written(self):
        self.link.send_frame(Frame(status=[0, 128, 255]))
        lines = self.dev.read_lines()
        self.assertIn("t 1 128", lines)
        self.assertIn("t 2 255", lines)

    def test_every_line_is_newline_framed(self):
        # 13 distinct key colours can't collapse to `k all`. The first frame also
        # establishes the underglow and the three status LEDs, since there's no previous
        # frame to diff against: 13 + `u all` + 3 status = 17 lines.
        self.link.send_frame(Frame([parse_hex(f"{i:02x}0000") for i in range(13)]))
        raw = self.dev.read_lines()
        self.assertEqual(len([l for l in raw if l.startswith("k ")]), 13)
        self.assertEqual(len(raw), 17)
        self.assertTrue(all(line and not line.endswith("\r") for line in raw))

    # --- reading ------------------------------------------------------------

    def test_input_events_are_parsed_and_dispatched(self):
        for line in ("key 3 down", "key 3 up", "enc cw", "enc press", "touch", "rear"):
            self.dev.write_line(line)
        deadline = time.monotonic() + 2.0
        while len(self.events) < 6 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.events, [
            ("key", ["3", "down"]), ("key", ["3", "up"]),
            ("enc", ["cw"]), ("enc", ["press"]),
            ("touch", []), ("rear", []),
        ])

    def test_command_acks_are_not_mistaken_for_events(self):
        for line in ("ok", "err bad command", "ok k 3"):
            self.dev.write_line(line)
        time.sleep(0.3)
        self.assertEqual(self.events, [])

    def test_events_are_also_queued(self):
        self.dev.write_line("key 11 down")
        deadline = time.monotonic() + 2.0
        while self.link.events.empty() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.link.events.get_nowait(), ("key", ["11", "down"]))

    def test_split_line_is_reassembled(self):
        os.write(self.dev.master, b"key 5 do")
        time.sleep(0.15)
        self.assertEqual(self.events, [])
        os.write(self.dev.master, b"wn\n")
        deadline = time.monotonic() + 2.0
        while not self.events and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.events, [("key", ["5", "down"])])

    def test_a_raising_event_handler_does_not_kill_the_reader(self):
        boom = Link(port=self.dev.path, layout=Layout({}),
                    on_event=lambda k, a: (_ for _ in ()).throw(RuntimeError("boom")))
        try:
            self.assertTrue(boom.ensure_connected())
            self.dev.write_line("key 1 down")
            time.sleep(0.3)
            self.dev.write_line("key 2 down")
            deadline = time.monotonic() + 2.0
            seen = []
            while len(seen) < 2 and time.monotonic() < deadline:
                try:
                    seen.append(boom.events.get_nowait())
                except Exception:
                    time.sleep(0.02)
            self.assertEqual(len(seen), 2, "reader thread died on a handler exception")
        finally:
            boom.close()

    # --- failure handling ---------------------------------------------------

    def test_disconnect_is_survived_without_raising(self):
        self.link.send_frame(Frame([parse_hex("ff0000")] * 13))
        self.dev.read_lines()
        self.dev.close()
        # Writes after the device vanishes must return False, never raise.
        for _ in range(3):
            self.link.send_frame(Frame([parse_hex("00ff00")] * 13))
        self.assertFalse(self.link.connected)

    def test_missing_port_is_not_an_error(self):
        link = Link(port="/dev/cu.definitely-not-here")
        try:
            self.assertFalse(link.ensure_connected())
            self.assertFalse(link.connected)
            self.assertFalse(link.send_frame(Frame.blank()))
            self.assertIsNone(link.port)
        finally:
            link.close()


class TestRendererOverPty(unittest.TestCase):
    """The render loop against a virtual device, which is as close to the real thing as
    this can get without flashing firmware."""

    def setUp(self):
        self.dev = PtyDevice()

    def tearDown(self):
        self.dev.close()

    def test_render_loop_streams_an_animated_effect(self):
        from libremicro.config import Config
        from libremicro.renderer import Renderer

        cfg = Config({
            "version": 2,
            "device": {"port": self.dev.path, "brightness": 200, "fps": 30},
            "profiles": {"default": {
                "keys": [{"index": i, "color": "101010"} for i in range(13)],
                "lighting": {"effect": {"name": "chase", "palette": "aurora",
                                        "speed": 1.0, "target": "all"}},
            }},
        })
        link = Link(port=cfg.port, layout=cfg.layout)
        self.assertTrue(link.ensure_connected())
        renderer = Renderer(link, cfg)
        renderer.start()
        try:
            time.sleep(1.0)
        finally:
            renderer.stop()
            link.close()

        lines = self.dev.read_lines(0.3)
        self.assertIn("bright 200", lines)
        pixel_writes = [l for l in lines if l.startswith(("k ", "u "))]
        self.assertGreater(len(pixel_writes), 20,
                           "render loop produced almost no pixel writes")
        # Every emitted colour must be a well-formed 6-digit hex triplet.
        for line in pixel_writes:
            parts = line.split()
            self.assertEqual(len(parts), 3, line)
            self.assertRegex(parts[2], r"^[0-9a-f]{6}$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
