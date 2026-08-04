"""Notification watchers: the framework, and the parsing the two macOS watchers rely on.

Two halves, for two different reasons.

The **framework** is tested with a fake watcher kind and a fake clock, so scheduling, pulse
transitions, unknown-versus-zero, and the isolation guarantees (a raising watcher, a hanging
watcher, an unknown kind in config) are all deterministic and need neither a network nor a
running Slack. Only the hanging-watcher test uses real threads, because thread isolation is
the thing under test there.

The **real watchers** are tested against captured output rather than a live app: the exact
strings the Dock query produced on a real machine (an app with a badge, an app without one, a
tiled app that isn't running, an app with no tile at all), the error text macOS emits when
Accessibility is denied, and the real shape of Slack's persisted unread state. That's the
only way these assertions mean the same thing tomorrow, and it's why the samples are quoted
verbatim with a note saying where each came from.

`LIBREMICRO_LIVE_WATCHERS=1` adds one test that really does query the Dock, for confirming a
machine's permissions by hand.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libremicro import watchers                                        # noqa: E402
from libremicro.config import Config                                   # noqa: E402
from libremicro.watchers import (                                      # noqa: E402
    DEFAULT_INTERVAL_S, DockBadge, Reading, SlackUnreadWatcher, UnreadBadgeWatcher,
    Watcher, Watchers, badge_reading, classify_osascript_error, parse_badge_label,
    parse_dock_output, slack_state_counts, slack_state_reading,
)


# --- test doubles -----------------------------------------------------------


class _StubRenderer:
    """Records pulses the way the real renderer applies them: `colour=None` clears."""

    def __init__(self):
        self.pulses: dict[int, tuple[str, float]] = {}
        self.calls: list[tuple[int, str | None, float]] = []

    def pulse(self, index, colour, period=1.4):
        self.calls.append((index, colour, period))
        if colour is None:
            self.pulses.pop(index, None)
        else:
            self.pulses[index] = (colour, period)


class _StubDaemon:
    def __init__(self, doc):
        self.cfg = Config(doc)
        self.renderer = _StubRenderer()


class _Clock:
    def __init__(self, t=1000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += float(dt)
        return self.t


class _Fake(Watcher):
    """An injectable watcher kind. `answers[id]` may be a Reading, an int, an exception to
    raise, or a callable."""

    kind = "fake"
    answers: dict = {}
    calls: list = []

    def __init__(self, spec=None):
        super().__init__(spec)
        self.id = str(self.spec.get("id") or "a")
        self.closed = 0

    def poll(self):
        _Fake.calls.append(self.id)
        answer = _Fake.answers.get(self.id, Reading.of(0))
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return answer()
        return answer

    def close(self):
        self.closed += 1


def doc(*specs, profile="default", extra_profiles=None):
    """A config with one `watch` per (index, spec) pair."""
    profiles = {profile: {"keys": [{"index": i, "color": "112233", "watch": w}
                                   for i, w in specs]}}
    profiles.update(extra_profiles or {})
    return {"version": 2, "active_profile": profile, "profiles": profiles}


def fake(ident="a", interval=None, flash="e01e5a", **rest):
    spec = {"type": "fake", "flash": flash, "id": ident, **rest}
    if interval is not None:
        spec["interval_s"] = interval
    return spec


def wait_for(predicate, timeout=3.0):
    """Wait for a background poll to land. Only used by the threaded tests."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


class WatcherTestCase(unittest.TestCase):
    """Base: registers the fake kind and captures the module's one-time warnings."""

    def setUp(self):
        watchers.register(_Fake)
        _Fake.answers = {}
        _Fake.calls = []
        watchers.reset_warnings()
        self.err = io.StringIO()
        self.clock = _Clock()
        self.wall = _Clock(1_700_000_000.0)

    def tearDown(self):
        watchers.unregister("fake")
        watchers.reset_warnings()

    def build(self, document, threaded=False):
        self.d = _StubDaemon(document)
        self.w = Watchers(self.d, clock=self.clock, wall=self.wall, threaded=threaded)
        return self.w

    def tick(self, at=None, **kw):
        """Tick with stderr captured, since watchers warn once per condition."""
        if at is not None:
            self.clock.t = float(at)
        with contextlib.redirect_stderr(self.err):
            self.w.tick(**kw)

    def log(self):
        return self.err.getvalue()

    def entry(self, index):
        for snap in self.w.state():
            if snap["index"] == index:
                return snap
        raise AssertionError(f"no watcher on key {index}")


# --- the registry -----------------------------------------------------------


class TestRegistry(WatcherTestCase):
    def test_the_two_real_kinds_are_registered(self):
        self.assertIn("unread_badge", watchers.kinds())
        self.assertIn("slack_unread", watchers.kinds())

    def test_registering_adds_a_kind_and_unregistering_removes_it(self):
        self.assertTrue(watchers.is_supported("fake"))
        watchers.unregister("fake")
        self.assertFalse(watchers.is_supported("fake"))

    def test_create_builds_the_registered_kind(self):
        made = watchers.create({"type": "fake", "flash": "ffffff", "id": "z"})
        self.assertIsInstance(made, _Fake)
        self.assertEqual(made.id, "z")

    def test_unknown_kind_raises_keyerror(self):
        with self.assertRaises(KeyError):
            watchers.create({"type": "phase_of_the_moon", "flash": "ffffff"})

    def test_missing_or_bad_type_raises_valueerror(self):
        for spec in ({"flash": "ffffff"}, {"type": "", "flash": "ffffff"}, "nope"):
            with self.assertRaises(ValueError):
                watchers.create(spec)

    def test_a_factory_can_be_registered_without_a_class(self):
        watchers.register_kind("lambda_kind", lambda spec: _Fake(spec))
        try:
            self.assertIsInstance(watchers.create({"type": "lambda_kind"}), _Fake)
        finally:
            watchers.unregister("lambda_kind")

    def test_a_class_without_a_kind_is_rejected(self):
        class Nameless(Watcher):
            pass

        with self.assertRaises(ValueError):
            watchers.register(Nameless)

    def test_registering_a_kind_does_not_touch_the_core(self):
        # The point of the registry: adding a kind is a registration, not an edit. Nothing
        # in the manager knows any kind name.
        source = Path(watchers.__file__).read_text()
        manager = source.split("class Watchers:", 1)[1]
        for name in ("unread_badge", "slack_unread", "fake"):
            self.assertNotIn(name, manager)


# --- building from config ---------------------------------------------------


class TestBuildingFromConfig(WatcherTestCase):
    def test_one_entry_per_watch_declaration(self):
        self.build(doc((0, fake("a")), (2, fake("b"))))
        self.tick()
        self.assertEqual([s["index"] for s in self.w.state()], [0, 2])

    def test_keys_without_a_watch_are_ignored(self):
        document = doc((0, fake("a")))
        document["profiles"]["default"]["keys"].append({"index": 5, "color": "ffffff"})
        self.build(document)
        self.tick()
        self.assertEqual([s["index"] for s in self.w.state()], [0])

    def test_malformed_watch_values_are_ignored(self):
        document = doc((0, fake("a")))
        document["profiles"]["default"]["keys"].append({"index": 6, "watch": "slack"})
        self.build(document)
        self.tick()
        self.assertEqual(len(self.w.state()), 1)

    def test_out_of_range_index_is_ignored(self):
        self.build(doc((0, fake("a")), (99, fake("b"))))
        self.tick()
        self.assertEqual([s["index"] for s in self.w.state()], [0])

    def test_interval_defaults_and_is_clamped(self):
        self.build(doc((0, fake("a")), (1, fake("b", interval=60)),
                       (2, fake("c", interval=0.1))))
        self.tick()
        self.assertEqual(self.entry(0)["interval_s"], DEFAULT_INTERVAL_S)
        self.assertEqual(self.entry(1)["interval_s"], 60)
        self.assertEqual(self.entry(2)["interval_s"], watchers.MIN_INTERVAL_S)

    def test_unknown_kind_is_skipped_never_polled_and_logged_once(self):
        self.build(doc((0, {"type": "phase_of_the_moon", "flash": "ff0000"}),
                       (1, fake("b"))))
        for step in range(4):
            self.tick(at=1000.0 + step * 20)

        unknown = self.entry(0)
        self.assertFalse(unknown["supported"])
        self.assertIsNone(unknown["value"])
        self.assertTrue(unknown["unknown"])
        self.assertIn("unknown watcher kind", unknown["error"])
        self.assertEqual(unknown["polls"], 0)
        self.assertFalse(unknown["pulsing"])
        # ...and the known one alongside it still runs.
        self.assertGreater(self.entry(1)["polls"], 0)
        self.assertEqual(self.log().count("phase_of_the_moon"), 1,
                         "an unknown kind should be reported once, not every poll")

    def test_a_kind_that_rejects_its_spec_is_skipped_not_fatal(self):
        # unread_badge with no `app` is a config error, not a crash.
        self.build(doc((0, {"type": "unread_badge", "flash": "ffffff"}), (1, fake("b"))))
        self.tick()
        self.assertFalse(self.entry(0)["supported"])
        self.assertIn("app", self.entry(0)["error"])
        self.assertEqual(len(self.w.state()), 2)

    def test_flash_colour_and_app_are_reported(self):
        self.build(doc((0, {"type": "unread_badge", "app": "WhatsApp", "flash": "25d366"})))
        self.tick()
        self.assertEqual(self.entry(0)["flash"], "25d366")
        self.assertEqual(self.entry(0)["app"], "WhatsApp")

    def test_a_broken_profile_does_not_raise(self):
        self.build(doc((0, fake("a"))))
        self.d.cfg.doc["active_profile"] = "ghost"
        self.tick(at=1100.0)
        self.assertEqual(self.w.state(), [])


# --- scheduling -------------------------------------------------------------


class TestScheduling(WatcherTestCase):
    def test_first_polls_are_staggered(self):
        self.build(doc((0, fake("a")), (1, fake("b")), (2, fake("c"))))
        self.tick(at=1000.0)
        self.assertEqual(_Fake.calls, ["a"], "all three must not fire in the same instant")
        self.tick(at=1000.0 + watchers.STAGGER_S)
        self.assertEqual(_Fake.calls, ["a", "b"])
        self.tick(at=1000.0 + 2 * watchers.STAGGER_S)
        self.assertEqual(_Fake.calls, ["a", "b", "c"])

    def test_nothing_polls_again_before_its_interval(self):
        self.build(doc((0, fake("a", interval=10)),))
        self.tick(at=1000.0)
        for at in (1001.0, 1005.0, 1009.9):
            self.tick(at=at)
        self.assertEqual(_Fake.calls, ["a"])
        self.tick(at=1010.0)
        self.assertEqual(_Fake.calls, ["a", "a"])

    def test_each_watcher_keeps_its_own_interval(self):
        self.build(doc((0, fake("slow", interval=100)), (1, fake("quick", interval=2))))
        self.tick(at=1000.0)
        self.tick(at=1000.25)
        for step in range(1, 6):
            self.tick(at=1000.25 + step * 2)
        self.assertEqual(_Fake.calls.count("slow"), 1)
        self.assertEqual(_Fake.calls.count("quick"), 6)

    def test_sleep_never_busy_loops_and_never_oversleeps(self):
        self.build(doc((0, fake("a", interval=5)),))
        self.tick(at=1000.0)
        self.assertLessEqual(self.w._sleep_for(), watchers.MAX_SLEEP_S)
        self.assertGreater(self.w._sleep_for(), 0.0)
        self.build(doc())
        self.tick()
        self.assertEqual(self.w._sleep_for(), watchers.MAX_SLEEP_S)

    def test_last_poll_and_age_are_reported(self):
        self.build(doc((0, fake("a")),))
        self.tick(at=1000.0)
        self.assertEqual(self.entry(0)["last_poll"], 1_700_000_000.0)
        self.wall.advance(30)
        self.assertEqual(self.entry(0)["age_s"], 30.0)

    def test_never_polled_reports_no_time(self):
        self.build(doc((0, fake("a")), (1, fake("b"))))
        self.tick(at=1000.0)
        self.assertIsNone(self.entry(1)["last_poll"])
        self.assertIsNone(self.entry(1)["age_s"])


# --- pulse behaviour --------------------------------------------------------


class TestPulseBehaviour(WatcherTestCase):
    def setUp(self):
        super().setUp()
        self.build(doc((0, fake("a", interval=10)),))

    def test_nonzero_starts_a_pulse_in_the_flash_colour(self):
        _Fake.answers = {"a": Reading.of(3)}
        self.tick(at=1000.0)
        self.assertEqual(self.d.renderer.pulses, {0: ("e01e5a", watchers.PULSE_PERIOD_S)})
        self.assertTrue(self.entry(0)["pulsing"])
        self.assertEqual(self.entry(0)["value"], 3)

    def test_zero_stops_the_pulse(self):
        _Fake.answers = {"a": Reading.of(3)}
        self.tick(at=1000.0)
        _Fake.answers = {"a": Reading.of(0)}
        self.tick(at=1010.0)
        self.assertEqual(self.d.renderer.pulses, {})
        self.assertEqual(self.d.renderer.calls[-1], (0, None, watchers.PULSE_PERIOD_S))
        self.assertFalse(self.entry(0)["pulsing"])
        self.assertEqual(self.entry(0)["value"], 0)

    def test_zero_from_the_start_never_pulses(self):
        _Fake.answers = {"a": Reading.of(0)}
        self.tick(at=1000.0)
        self.assertEqual(self.d.renderer.calls, [])

    def test_the_renderer_is_only_touched_on_a_change(self):
        _Fake.answers = {"a": Reading.of(3)}
        self.tick(at=1000.0)
        _Fake.answers = {"a": Reading.of(9)}     # still unread, just more of it
        self.tick(at=1010.0)
        self.tick(at=1020.0)
        self.assertEqual(len(self.d.renderer.calls), 1,
                         "a steady non-zero count should not re-issue the pulse")
        self.assertEqual(self.entry(0)["value"], 9)

    def test_unknown_clears_the_pulse_rather_than_leaving_it_lying(self):
        _Fake.answers = {"a": Reading.of(3)}
        self.tick(at=1000.0)
        _Fake.answers = {"a": Reading.unknown("Slack went away")}
        self.tick(at=1010.0)
        self.assertEqual(self.d.renderer.pulses, {})
        self.assertIsNone(self.entry(0)["value"])
        self.assertEqual(self.entry(0)["error"], "Slack went away")

    def test_a_bare_int_is_accepted_as_a_count(self):
        _Fake.answers = {"a": 4}
        self.tick(at=1000.0)
        self.assertEqual(self.entry(0)["value"], 4)
        self.assertTrue(self.entry(0)["pulsing"])

    def test_a_watcher_returning_nothing_is_unknown_not_zero(self):
        _Fake.answers = {"a": lambda: None}
        self.tick(at=1000.0)
        self.assertIsNone(self.entry(0)["value"])
        self.assertTrue(self.entry(0)["unknown"])
        self.assertIn("expected a Reading", self.entry(0)["error"])

    def test_a_renderer_that_explodes_does_not_break_the_watcher(self):
        def boom(*_a, **_k):
            raise RuntimeError("no serial link")

        self.d.renderer.pulse = boom
        _Fake.answers = {"a": Reading.of(2)}
        self.tick(at=1000.0)
        self.assertEqual(self.entry(0)["value"], 2)
        self.assertFalse(self.entry(0)["pulsing"])
        self.assertIn("could not pulse", self.log())


# --- unknown is not zero ----------------------------------------------------


class TestUnknownVersusZero(WatcherTestCase):
    def test_the_two_states_are_distinguishable_in_the_report(self):
        self.build(doc((0, fake("zero")), (1, fake("dunno"))))
        _Fake.answers = {"zero": Reading.of(0, detail="Slack is not running"),
                         "dunno": Reading.unknown("needs Accessibility permission")}
        self.tick(at=1000.0)
        self.tick(at=1000.0 + watchers.STAGGER_S)

        zero, unknown = self.entry(0), self.entry(1)
        self.assertEqual(zero["value"], 0)
        self.assertFalse(zero["unknown"])
        self.assertIsNone(zero["error"], "a real zero is not an error")
        self.assertEqual(zero["detail"], "Slack is not running")

        self.assertIsNone(unknown["value"])
        self.assertTrue(unknown["unknown"])
        self.assertIn("Accessibility", unknown["error"])

    def test_readings_report_themselves(self):
        self.assertTrue(Reading.unknown("x").is_unknown)
        self.assertFalse(Reading.of(0).is_unknown)
        self.assertFalse(Reading.of(0).active)
        self.assertTrue(Reading.of(1).active)
        self.assertFalse(Reading.unknown("x").active)
        self.assertEqual(Reading.of(-5).value, 0)

    def test_an_unknown_is_reported_once_per_reason(self):
        self.build(doc((0, fake("a", interval=5)),))
        _Fake.answers = {"a": Reading.unknown("needs Accessibility permission")}
        for step in range(4):
            self.tick(at=1000.0 + step * 5)
        self.assertEqual(self.log().count("needs Accessibility"), 1)


# --- isolation --------------------------------------------------------------


class TestIsolation(WatcherTestCase):
    def test_a_raising_watcher_records_the_error_and_spares_the_others(self):
        self.build(doc((0, fake("bad")), (1, fake("good")), (2, fake("also_good"))))
        _Fake.answers = {"bad": RuntimeError("osascript exploded"),
                         "good": Reading.of(2), "also_good": Reading.of(0)}
        for step in range(3):
            self.tick(at=1000.0 + step * watchers.STAGGER_S)

        self.assertIn("RuntimeError", self.entry(0)["error"])
        self.assertIsNone(self.entry(0)["value"])
        self.assertEqual(self.entry(1)["value"], 2)
        self.assertTrue(self.entry(1)["pulsing"])
        self.assertEqual(self.entry(2)["value"], 0)

    def test_a_raising_watcher_is_polled_again_next_time(self):
        self.build(doc((0, fake("bad", interval=5)),))
        _Fake.answers = {"bad": RuntimeError("transient")}
        self.tick(at=1000.0)
        _Fake.answers = {"bad": Reading.of(1)}
        self.tick(at=1005.0)
        self.assertEqual(self.entry(0)["value"], 1, "one failure must not retire a watcher")

    def test_a_hanging_watcher_does_not_stall_the_others(self):
        # The one test that uses real threads: thread isolation is the claim being checked.
        release = threading.Event()
        self.build(doc((0, fake("hangs", interval=5)), (1, fake("fine", interval=5))),
                   threaded=True)
        _Fake.answers = {"hangs": lambda: (release.wait(10), Reading.of(1))[1],
                         "fine": Reading.of(7)}
        try:
            self.tick(at=1000.0)
            self.tick(at=1000.0 + watchers.STAGGER_S)
            self.assertTrue(wait_for(lambda: self.entry(1)["value"] == 7),
                            "the healthy watcher must report while the other is stuck")
            self.assertTrue(self.entry(1)["pulsing"])
            self.assertTrue(self.entry(0)["polling"])
            self.assertIsNone(self.entry(0)["value"])

            # And it is not re-dispatched while it's still in there.
            self.tick(at=1030.0)
            self.assertEqual(_Fake.calls.count("hangs"), 1)
            self.assertIn("stuck", self.entry(0)["error"])
        finally:
            release.set()
        self.assertTrue(wait_for(lambda: self.entry(0)["value"] == 1))
        self.assertFalse(self.entry(0)["polling"])

    def test_stop_does_not_wait_for_a_hanging_watcher(self):
        release = threading.Event()
        self.build(doc((0, fake("hangs", interval=1)),), threaded=True)
        _Fake.answers = {"hangs": lambda: (release.wait(10), Reading.of(1))[1]}
        try:
            self.tick(at=1000.0)
            self.assertTrue(wait_for(lambda: self.entry(0)["polling"]))
            started = time.monotonic()
            with contextlib.redirect_stderr(self.err):
                self.w.stop()
            self.assertLess(time.monotonic() - started, 2.0,
                            "shutdown must not wait on a stuck poll")
        finally:
            release.set()


# --- config changes ---------------------------------------------------------


class TestConfigChanges(WatcherTestCase):
    def test_a_profile_switch_is_noticed_without_being_told(self):
        # The dispatcher switches profiles without going through apply_config, so the
        # scheduler has to spot it by itself.
        self.build(doc((0, fake("a")),
                       extra_profiles={"other": {"keys": [
                           {"index": 4, "watch": fake("other_key")}]}}))
        self.tick(at=1000.0)
        self.assertEqual([s["index"] for s in self.w.state()], [0])

        self.d.cfg.doc["active_profile"] = "other"
        self.tick(at=1001.0)
        self.assertEqual([s["index"] for s in self.w.state()], [4])

    def test_a_dropped_watcher_stops_pulsing_its_key(self):
        self.build(doc((0, fake("a")),))
        _Fake.answers = {"a": Reading.of(5)}
        self.tick(at=1000.0)
        self.assertEqual(set(self.d.renderer.pulses), {0})

        self.d.cfg.doc["profiles"]["default"]["keys"] = []
        self.tick(at=1001.0)
        self.assertEqual(self.d.renderer.pulses, {},
                         "a key that stopped being watched must not keep pulsing")
        self.assertEqual(self.w.state(), [])

    def test_an_unchanged_declaration_keeps_its_value_across_a_reload(self):
        self.build(doc((0, fake("a", interval=30)), (1, fake("b", interval=30))))
        _Fake.answers = {"a": Reading.of(2), "b": Reading.of(0)}
        self.tick(at=1000.0)
        self.tick(at=1000.25)
        self.assertEqual(self.entry(0)["value"], 2)

        with contextlib.redirect_stderr(self.err):
            self.w.config_changed()
        self.assertEqual(self.entry(0)["value"], 2,
                         "an unrelated config edit should not blank the pad")
        self.assertTrue(self.entry(0)["pulsing"])

    def test_an_edited_declaration_is_rebuilt(self):
        self.build(doc((0, fake("a", interval=30)),))
        _Fake.answers = {"a": Reading.of(2), "b": Reading.of(0)}
        self.tick(at=1000.0)
        self.d.cfg.doc["profiles"]["default"]["keys"][0]["watch"] = fake("b", interval=30)
        self.tick(at=1001.0)
        self.assertEqual(_Fake.calls, ["a", "b"], "a changed spec starts a fresh watcher")
        self.assertEqual(self.entry(0)["value"], 0)
        self.assertEqual(self.d.renderer.pulses, {}, "and drops the old one's pulse")

    def test_rebuilding_closes_the_watcher_it_drops(self):
        self.build(doc((0, fake("a")),))
        self.tick(at=1000.0)
        dropped = self.w._entries[0].watcher
        self.d.cfg.doc["profiles"]["default"]["keys"] = []
        self.tick(at=1001.0)
        self.assertEqual(dropped.closed, 1)


# --- reporting --------------------------------------------------------------


class TestReporting(WatcherTestCase):
    def test_state_is_a_copy(self):
        self.build(doc((0, fake("a")),))
        _Fake.answers = {"a": Reading.of(4)}
        self.tick(at=1000.0)
        snapshot = self.w.state()
        snapshot[0]["value"] = 999
        snapshot.clear()
        self.assertEqual(self.entry(0)["value"], 4)

    def test_state_is_json_serialisable(self):
        self.build(doc((0, fake("a")), (1, {"type": "nope", "flash": "ffffff"})))
        _Fake.answers = {"a": Reading.of(4, detail="Dock badge '4'", source="dock")}
        self.tick(at=1000.0)
        json.dumps(self.w.state())          # the UI reads this over HTTP

    def test_status_reports_the_available_kinds(self):
        self.build(doc((0, fake("a")),))
        self.tick(at=1000.0)
        status = self.w.status()
        self.assertFalse(status["running"])
        self.assertIn("unread_badge", status["kinds"])
        self.assertEqual(len(status["watchers"]), 1)

    def test_stop_clears_every_pulse(self):
        self.build(doc((0, fake("a")), (1, fake("b"))))
        _Fake.answers = {"a": Reading.of(1), "b": Reading.of(1)}
        self.tick(at=1000.0)
        self.tick(at=1000.25)
        self.assertEqual(set(self.d.renderer.pulses), {0, 1})
        with contextlib.redirect_stderr(self.err):
            self.w.stop()
        self.assertEqual(self.d.renderer.pulses, {})

    def test_len_counts_entries(self):
        self.build(doc((0, fake("a")), (1, fake("b"))))
        self.tick(at=1000.0)
        self.assertEqual(len(self.w), 2)


class TestBackgroundThread(WatcherTestCase):
    """start()/stop() with the real clock. One poll, no sleeps beyond waiting for it."""

    def test_the_scheduler_thread_polls_and_stops(self):
        self.build(doc((0, fake("a", interval=1)),), threaded=True)
        self.w.clock = time.monotonic
        self.w.wall = time.time
        _Fake.answers = {"a": Reading.of(3)}
        with contextlib.redirect_stderr(self.err):
            self.w.start()
            try:
                self.assertTrue(wait_for(lambda: self.entry(0)["value"] == 3))
                self.assertEqual(self.d.renderer.pulses.get(0),
                                 ("e01e5a", watchers.PULSE_PERIOD_S))
            finally:
                self.w.stop()
        self.assertFalse(self.w.status()["running"])
        self.assertEqual(self.d.renderer.pulses, {})


# --- the Dock query: captured samples ---------------------------------------
#
# Every string in this section was produced by the real thing on a real machine, so these
# tests are about parsing what macOS actually says rather than what it might say.

#: Messages, badged "2" and running (`osascript badge.applescript Messages`).
SAMPLE_BADGED = "ok|1|true|2|\n"
#: Slack, running and tiled with no badge at all.
SAMPLE_NO_BADGE = "ok|1|true|\n"
#: WhatsApp: kept in the Dock, not running.
SAMPLE_TILED_NOT_RUNNING = "ok|1|false|\n"
#: Mail: no tile, not running.
SAMPLE_NO_TILE = "ok|0|false|\n"
#: Two tiles with the same name — a second copy of an app in another location. Observed with
#: two "Google Chrome" entries in one Dock.
SAMPLE_TWO_TILES = "ok|2|true|3|1|\n"

#: osascript's stderr when Accessibility has not been granted to the responsible process.
STDERR_NO_ACCESSIBILITY = (
    "execution error: System Events got an error: osascript is not allowed assistive "
    "access. (-25211)\n")
#: ...and when Automation control of System Events has been refused.
STDERR_NO_AUTOMATION = (
    "execution error: Not authorized to send Apple events to System Events. (-1743)\n")


class TestDockOutputParsing(unittest.TestCase):
    def test_badged_app(self):
        badge = parse_dock_output(SAMPLE_BADGED)
        self.assertTrue(badge.ok)
        self.assertEqual((badge.tiles, badge.running, badge.labels), (1, True, ("2",)))

    def test_running_app_with_no_badge(self):
        badge = parse_dock_output(SAMPLE_NO_BADGE)
        self.assertTrue(badge.ok)
        self.assertEqual(badge.labels, ())
        self.assertEqual(badge.tiles, 1)

    def test_tiled_but_not_running(self):
        badge = parse_dock_output(SAMPLE_TILED_NOT_RUNNING)
        self.assertTrue(badge.ok)
        self.assertFalse(badge.running)

    def test_no_tile_at_all(self):
        badge = parse_dock_output(SAMPLE_NO_TILE)
        self.assertTrue(badge.ok)
        self.assertEqual((badge.tiles, badge.labels), (0, ()))

    def test_two_tiles_with_the_same_name(self):
        badge = parse_dock_output(SAMPLE_TWO_TILES)
        self.assertEqual(badge.labels, ("3", "1"))

    def test_script_reported_error(self):
        badge = parse_dock_output("err|the Dock is not running")
        self.assertFalse(badge.ok)
        self.assertEqual(badge.error, "the Dock is not running")

    def test_garbage_is_not_ok_and_never_a_count(self):
        for text in ("", "   ", "what", "ok|x|true|", "ok|1"):
            badge = parse_dock_output(text)
            self.assertFalse(badge.ok, text)
            self.assertTrue(badge.error, text)


class TestBadgeLabelParsing(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(parse_badge_label("2"), 2)
        self.assertEqual(parse_badge_label("12"), 12)
        self.assertEqual(parse_badge_label(" 7 "), 7)
        self.assertEqual(parse_badge_label("1,234"), 1234)

    def test_no_badge_is_zero(self):
        self.assertEqual(parse_badge_label(""), 0)
        self.assertEqual(parse_badge_label("   "), 0)
        self.assertEqual(parse_badge_label(None), 0)

    def test_slacks_bullet_counts_as_one(self):
        # Slack badges "•" for unread channels with no mention in them.
        for bullet in ("•", "●", "·"):
            self.assertEqual(parse_badge_label(bullet), 1, bullet)

    def test_capped_counts_keep_their_number(self):
        self.assertEqual(parse_badge_label("99+"), 99)
        self.assertEqual(parse_badge_label("9+"), 9)

    def test_a_badge_we_cannot_parse_is_still_a_badge(self):
        # Never zero: the user can see it, so the key should pulse.
        self.assertEqual(parse_badge_label("New"), 1)
        self.assertEqual(parse_badge_label("!"), 1)


class TestErrorClassification(unittest.TestCase):
    def test_accessibility_denial_is_named(self):
        permission, message = classify_osascript_error(STDERR_NO_ACCESSIBILITY)
        self.assertEqual(permission, "accessibility")
        self.assertIn("Accessibility", message)
        self.assertIn("System Settings", message)

    def test_automation_denial_is_named(self):
        permission, message = classify_osascript_error(STDERR_NO_AUTOMATION)
        self.assertEqual(permission, "automation")
        self.assertIn("Automation", message)

    def test_anything_else_is_passed_through_as_one_line(self):
        permission, message = classify_osascript_error(
            "execution error: System Events got an error: Can't get list 1. (-1728)\nline 2")
        self.assertEqual(permission, "")
        self.assertNotIn("\n", message)
        self.assertIn("-1728", message)

    def test_silence_still_produces_a_message(self):
        permission, message = classify_osascript_error("")
        self.assertEqual(permission, "")
        self.assertTrue(message)


def _proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["osascript"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class TestReadDockBadge(unittest.TestCase):
    def test_success_is_parsed(self):
        badge = watchers.read_dock_badge(
            "Messages", run=lambda args, script, timeout: _proc(stdout=SAMPLE_BADGED))
        self.assertEqual(badge.labels, ("2",))

    def test_the_app_name_is_passed_as_an_argument_not_interpolated(self):
        seen = {}

        def run(args, script, timeout):
            seen["args"] = args
            seen["script"] = script
            return _proc(stdout=SAMPLE_NO_BADGE)

        watchers.read_dock_badge('Weird " App', run=run)
        self.assertEqual(seen["args"], ['Weird " App'])
        self.assertNotIn("Weird", seen["script"])

    def test_permission_failure_is_reported_not_swallowed(self):
        badge = watchers.read_dock_badge(
            "Slack", run=lambda *a: _proc(returncode=1, stderr=STDERR_NO_ACCESSIBILITY))
        self.assertFalse(badge.ok)
        self.assertEqual(badge.permission, "accessibility")

    def test_timeout_is_reported(self):
        def hang(*_a):
            raise subprocess.TimeoutExpired(cmd="osascript", timeout=8.0)

        badge = watchers.read_dock_badge("Slack", run=hang)
        self.assertFalse(badge.ok)
        self.assertIn("did not answer", badge.error)

    def test_a_machine_without_osascript_says_so(self):
        def missing(*_a):
            raise FileNotFoundError(2, "No such file or directory")

        badge = watchers.read_dock_badge("Slack", run=missing)
        self.assertFalse(badge.ok)
        self.assertIn("macOS", badge.error)


class TestBadgeReading(unittest.TestCase):
    """Where zero and unknown are decided."""

    @staticmethod
    def reading(sample=None, badge=None, installed=True):
        return badge_reading(
            "Slack",
            read=lambda app: badge if badge is not None else parse_dock_output(sample),
            installed=lambda app: installed)

    def test_a_badge_becomes_a_count(self):
        r = self.reading(SAMPLE_BADGED)
        self.assertEqual(r.value, 2)
        self.assertEqual(r.source, "dock")
        self.assertIn("'2'", r.detail)

    def test_a_tile_with_no_badge_is_a_real_zero(self):
        r = self.reading(SAMPLE_NO_BADGE)
        self.assertEqual(r.value, 0)
        self.assertFalse(r.is_unknown)

    def test_a_tile_for_a_quit_app_is_zero_and_says_so(self):
        r = self.reading(SAMPLE_TILED_NOT_RUNNING)
        self.assertEqual(r.value, 0)
        self.assertIn("not running", r.detail)

    def test_two_tiles_take_the_larger_count(self):
        self.assertEqual(self.reading(SAMPLE_TWO_TILES).value, 3)

    def test_quit_but_installed_app_is_zero_with_a_reason(self):
        r = self.reading(SAMPLE_NO_TILE, installed=True)
        self.assertEqual(r.value, 0)
        self.assertIn("not running", r.detail)

    def test_an_app_name_that_resolves_to_nothing_is_unknown(self):
        # The silent-zero trap: a typo'd or renamed app must not read as "no unread".
        r = self.reading(SAMPLE_NO_TILE, installed=False)
        self.assertTrue(r.is_unknown)
        self.assertIn("app", r.detail)

    def test_running_with_no_tile_is_unknown(self):
        r = self.reading("ok|0|true|")
        self.assertTrue(r.is_unknown)
        self.assertIn("Dock tile", r.detail)

    def test_a_permission_failure_is_unknown_and_says_what_to_do(self):
        r = self.reading(badge=DockBadge(False, error="needs Accessibility permission — go on",
                                         permission="accessibility"))
        self.assertTrue(r.is_unknown)
        self.assertIn("Accessibility", r.detail)


class TestUnreadBadgeWatcher(unittest.TestCase):
    def test_it_needs_an_app(self):
        with self.assertRaises(ValueError):
            UnreadBadgeWatcher({"type": "unread_badge", "flash": "ffffff"})
        with self.assertRaises(ValueError):
            UnreadBadgeWatcher({"app": "   "})

    def test_it_polls_the_named_app(self):
        seen = []
        w = UnreadBadgeWatcher({"app": "WhatsApp"},
                               read=lambda app: (seen.append(app),
                                                 parse_dock_output(SAMPLE_BADGED))[1],
                               installed=lambda app: True)
        self.assertEqual(w.poll().value, 2)
        self.assertEqual(seen, ["WhatsApp"])
        self.assertIn("WhatsApp", w.describe())

    def test_the_example_config_builds(self):
        # host/config/example.json's own declarations must be constructible.
        made = watchers.create({"type": "unread_badge", "app": "WhatsApp",
                                "flash": "25d366"})
        self.assertIsInstance(made, UnreadBadgeWatcher)


# --- Slack ------------------------------------------------------------------
#
# Captured from a real ~/Library/Application Support/Slack/storage/root-state.json (team id
# and user id replaced). `unreadHighlights` is the count Slack badges numerically;
# `unreads` with `showBullet` is the "•" badge — unread channels, no mention.

SLACK_STATE_SAMPLE = {
    "appTeams": {},
    "webapp": {
        "teams": {
            "T00000000": {
                "theme": {"titlebarBackground": "#2C3849"},
                "notificationPrefs": {"muteSounds": False},
                "unreads": {"showBullet": True, "unreadHighlights": 12, "unreads": 1},
                "userId": "U00000000",
            }
        }
    },
    "_persist": {"version": -1, "rehydrated": True},
}


def slack_doc(**unreads):
    return {"webapp": {"teams": {"T1": {"unreads": unreads}}}}


class TestSlackStateParsing(unittest.TestCase):
    def test_the_captured_sample(self):
        self.assertEqual(slack_state_counts(SLACK_STATE_SAMPLE), (12, 1))

    def test_mentions_only(self):
        self.assertEqual(slack_state_counts(slack_doc(unreadHighlights=3, unreads=0)), (3, 0))

    def test_bullet_only(self):
        self.assertEqual(slack_state_counts(
            slack_doc(unreadHighlights=0, unreads=1, showBullet=True)), (0, 1))

    def test_bullet_suppressed_by_the_users_preference(self):
        self.assertEqual(slack_state_counts(
            slack_doc(unreadHighlights=0, unreads=1, showBullet=False)), (0, 0))

    def test_nothing_unread(self):
        self.assertEqual(slack_state_counts(slack_doc(unreadHighlights=0, unreads=0)), (0, 0))

    def test_several_workspaces_add_up(self):
        doc_ = {"webapp": {"teams": {
            "T1": {"unreads": {"unreadHighlights": 2, "unreads": 1}},
            "T2": {"unreads": {"unreadHighlights": 5, "unreads": 0}},
            "T3": {"unreads": {"unreadHighlights": 0, "unreads": 1}},
        }}}
        self.assertEqual(slack_state_counts(doc_), (7, 2))

    def test_shapes_we_did_not_expect_do_not_raise(self):
        for bad in ({}, {"webapp": None}, {"webapp": {"teams": []}},
                    {"webapp": {"teams": {"T1": None}}},
                    {"webapp": {"teams": {"T1": {"unreads": "lots"}}}},
                    slack_doc(unreadHighlights="many", unreads=None), "not a doc", None):
            self.assertEqual(slack_state_counts(bad if isinstance(bad, dict) else {}), (0, 0),
                             bad)


class TestSlackStateReading(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "root-state.json"

    def write(self, text):
        self.path.write_text(text)
        return self.path

    def test_a_real_looking_file_gives_a_count_and_its_age(self):
        self.write(json.dumps(SLACK_STATE_SAMPLE))
        r = slack_state_reading(self.path, now=os.path.getmtime(self.path) + 90)
        self.assertEqual(r.value, 12)
        self.assertEqual(r.source, "slack-state")
        self.assertIn("90s ago", r.detail)

    def test_a_bullet_becomes_one(self):
        self.write(json.dumps(slack_doc(unreadHighlights=0, unreads=1)))
        self.assertEqual(slack_state_reading(self.path).value, 1)

    def test_all_clear_is_zero(self):
        self.write(json.dumps(slack_doc(unreadHighlights=0, unreads=0)))
        r = slack_state_reading(self.path)
        self.assertEqual(r.value, 0)
        self.assertFalse(r.is_unknown)

    def test_a_missing_file_is_unknown_not_zero(self):
        r = slack_state_reading(Path(self.dir.name) / "absent.json")
        self.assertTrue(r.is_unknown)
        self.assertIn("cannot read", r.detail)

    def test_truncated_json_is_unknown_not_zero(self):
        self.write('{"webapp": {"teams":')
        r = slack_state_reading(self.path)
        self.assertTrue(r.is_unknown)
        self.assertIn("JSON", r.detail)


class TestSlackUnreadWatcher(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.state = Path(self.dir.name) / "root-state.json"
        self.state.write_text(json.dumps(SLACK_STATE_SAMPLE))

    def watcher(self, sample=None, badge=None):
        return SlackUnreadWatcher(
            {"type": "slack_unread", "flash": "e01e5a"},
            read=lambda app: badge if badge is not None else parse_dock_output(sample),
            installed=lambda app: True, state_path=self.state)

    def test_it_defaults_to_the_slack_app(self):
        self.assertEqual(self.watcher(SAMPLE_NO_BADGE).app, "Slack")

    def test_the_dock_badge_wins_when_it_can_be_read(self):
        r = self.watcher(SAMPLE_BADGED).poll()
        self.assertEqual(r.value, 2)
        self.assertEqual(r.source, "dock")

    def test_a_readable_dock_with_no_badge_is_zero_even_though_the_state_file_disagrees(self):
        # The state file says 12; the Dock says none. What the user sees wins.
        r = self.watcher(SAMPLE_NO_BADGE).poll()
        self.assertEqual(r.value, 0)
        self.assertEqual(r.source, "dock")

    def test_it_falls_back_to_slacks_own_state_when_the_dock_is_unreadable(self):
        r = self.watcher(badge=DockBadge(False, error="needs Accessibility permission",
                                        permission="accessibility")).poll()
        self.assertEqual(r.value, 12)
        self.assertEqual(r.source, "slack-state")
        self.assertIn("Dock unreadable", r.detail)

    def test_with_both_sources_broken_it_is_unknown_and_names_the_dock_problem(self):
        self.state.unlink()
        r = self.watcher(badge=DockBadge(False, error="needs Accessibility permission",
                                        permission="accessibility")).poll()
        self.assertTrue(r.is_unknown)
        self.assertIn("Accessibility", r.detail)

    def test_it_needs_no_token_or_network(self):
        # Nothing in this watcher reaches for a token or a socket; the config in
        # host/config/example.json declares only a type and a colour.
        made = watchers.create({"type": "slack_unread", "flash": "e01e5a"})
        self.assertIsInstance(made, SlackUnreadWatcher)
        self.assertEqual(made.spec.get("app"), "Slack")


@unittest.skipUnless(os.environ.get("LIBREMICRO_LIVE_WATCHERS"),
                     "set LIBREMICRO_LIVE_WATCHERS=1 to query the real Dock")
class TestLive(unittest.TestCase):
    """Opt-in: confirms this machine's permissions really are granted."""

    def test_the_dock_answers_for_the_finder(self):
        badge = watchers.read_dock_badge("Finder")
        self.assertTrue(badge.ok, badge.error)
        self.assertGreaterEqual(badge.tiles, 1)

    def test_a_reading_for_slack_is_not_a_silent_zero(self):
        reading = UnreadBadgeWatcher({"app": "Slack"}).poll()
        print(f"\nlive Slack reading: value={reading.value} "
              f"detail={reading.detail!r} source={reading.source}")
        self.assertTrue(reading.detail, "every live reading should explain itself")


if __name__ == "__main__":
    unittest.main(verbosity=2)
