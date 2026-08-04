"""Input recognition and binding dispatch.

The recogniser is driven with an explicit clock, so the awkward timing cases — a double-tap
arriving just inside the window, a hold that must suppress the press it would otherwise
produce — are tested deterministically rather than with sleeps.

Actions are stubbed. What's under test is *which* binding gets chosen and *when*, not
whether macOS opens an app.
"""
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libremicro import events  # noqa: E402
from libremicro.actions import Actions, Context, Result  # noqa: E402
from libremicro.config import Config  # noqa: E402
from libremicro.dispatch import Dispatcher  # noqa: E402
from libremicro.events import DOUBLE, HOLD, KEY, PRESS, RELEASE, Recognizer, Trigger  # noqa: E402


class TestRecognizer(unittest.TestCase):
    """Timing semantics, with a fake clock."""

    def setUp(self):
        self.fired: list[Trigger] = []
        self.bound: set[tuple[str, int, str]] = set()
        self.r = Recognizer(self.fired.append,
                            lambda c, i, k: (c, i, k) in self.bound,
                            hold_ms=400, double_ms=250)

    def bind(self, *kinds, index=0):
        for k in kinds:
            self.bound.add((KEY, index, k))

    def kinds(self):
        return [t.kind for t in self.fired]

    # --- press --------------------------------------------------------------

    def test_press_fires_immediately_when_no_double_bound(self):
        self.bind(PRESS)
        self.r.down(KEY, 0, 0.0)
        self.r.up(KEY, 0, 0.05)
        self.assertEqual(self.kinds(), [PRESS])

    def test_unbound_key_fires_nothing(self):
        self.r.down(KEY, 0, 0.0)
        self.r.up(KEY, 0, 0.05)
        self.r.tick(1.0)
        self.assertEqual(self.fired, [])

    def test_press_is_deferred_when_double_is_bound(self):
        self.bind(PRESS, DOUBLE)
        self.r.down(KEY, 0, 0.0)
        self.r.up(KEY, 0, 0.05)
        self.assertEqual(self.kinds(), [], "press must wait out the double window")
        self.r.tick(0.20)
        self.assertEqual(self.kinds(), [], "still inside the window")
        self.r.tick(0.40)
        self.assertEqual(self.kinds(), [PRESS])

    def test_double_tap_fires_double_and_not_press(self):
        self.bind(PRESS, DOUBLE)
        self.r.down(KEY, 0, 0.0)
        self.r.up(KEY, 0, 0.05)
        self.r.down(KEY, 0, 0.15)          # second tap inside 250ms
        self.assertEqual(self.kinds(), [DOUBLE])
        self.r.up(KEY, 0, 0.20)
        self.r.tick(2.0)
        self.assertEqual(self.kinds(), [DOUBLE], "press must not follow a double")

    def test_second_tap_outside_the_window_is_two_presses(self):
        self.bind(PRESS, DOUBLE)
        self.r.down(KEY, 0, 0.0)
        self.r.up(KEY, 0, 0.05)
        self.r.tick(0.40)
        self.r.down(KEY, 0, 0.50)
        self.r.up(KEY, 0, 0.55)
        self.r.tick(0.90)
        self.assertEqual(self.kinds(), [PRESS, PRESS])

    # --- hold ---------------------------------------------------------------

    def test_hold_fires_while_still_down(self):
        self.bind(PRESS, HOLD)
        self.r.down(KEY, 0, 0.0)
        self.r.tick(0.30)
        self.assertEqual(self.kinds(), [])
        self.r.tick(0.45)
        self.assertEqual(self.kinds(), [HOLD])

    def test_hold_suppresses_press_on_release(self):
        self.bind(PRESS, HOLD)
        self.r.down(KEY, 0, 0.0)
        self.r.tick(0.45)
        self.r.up(KEY, 0, 0.60)
        self.r.tick(1.5)
        self.assertEqual(self.kinds(), [HOLD], "a long press must not also fire press")

    def test_hold_fires_once_not_repeatedly(self):
        self.bind(HOLD)
        self.r.down(KEY, 0, 0.0)
        for t in (0.45, 0.6, 0.9, 2.0):
            self.r.tick(t)
        self.assertEqual(self.kinds(), [HOLD])

    def test_short_press_does_not_fire_hold(self):
        self.bind(PRESS, HOLD)
        self.r.down(KEY, 0, 0.0)
        self.r.up(KEY, 0, 0.10)
        self.r.tick(1.0)
        self.assertEqual(self.kinds(), [PRESS])

    def test_hold_ignored_when_not_bound(self):
        self.bind(PRESS)
        self.r.down(KEY, 0, 0.0)
        self.r.tick(1.0)
        self.assertEqual(self.kinds(), [])
        self.r.up(KEY, 0, 1.1)
        self.assertEqual(self.kinds(), [PRESS])

    # --- release ------------------------------------------------------------

    def test_release_fires_independently(self):
        self.bind(PRESS, RELEASE)
        self.r.down(KEY, 0, 0.0)
        self.r.up(KEY, 0, 0.05)
        self.assertEqual(self.kinds(), [RELEASE, PRESS])

    def test_release_fires_even_after_hold(self):
        self.bind(HOLD, RELEASE)
        self.r.down(KEY, 0, 0.0)
        self.r.tick(0.45)
        self.r.up(KEY, 0, 0.5)
        self.assertEqual(self.kinds(), [HOLD, RELEASE])

    # --- misc ---------------------------------------------------------------

    def test_keys_are_tracked_independently(self):
        self.bind(PRESS, index=0)
        self.bind(PRESS, index=1)
        self.r.down(KEY, 0, 0.0)
        self.r.down(KEY, 1, 0.01)
        self.r.up(KEY, 1, 0.02)
        self.assertEqual([(t.index, t.kind) for t in self.fired], [(1, PRESS)])
        self.r.up(KEY, 0, 0.03)
        self.assertEqual([(t.index, t.kind) for t in self.fired], [(1, PRESS), (0, PRESS)])

    def test_reset_drops_in_flight_state(self):
        self.bind(PRESS, HOLD)
        self.r.down(KEY, 0, 0.0)
        self.assertTrue(self.r.is_down(KEY, 0))
        self.r.reset()
        self.assertFalse(self.r.is_down(KEY, 0))
        self.r.tick(2.0)
        self.assertEqual(self.fired, [])

    def test_up_without_down_is_harmless(self):
        self.bind(PRESS, RELEASE)
        self.r.up(KEY, 0, 0.0)
        self.assertEqual(self.kinds(), [RELEASE])

    def test_encoder_rotation_fires_immediately(self):
        self.bound.add((events.ENCODER, 0, events.CW))
        self.r.rotate(events.CW, 0.0)
        self.r.rotate(events.CCW, 0.01)      # not bound
        self.assertEqual([(t.control, t.kind) for t in self.fired],
                         [(events.ENCODER, events.CW)])

    def test_tap_is_a_down_up_pair(self):
        self.bound.add((events.TOUCH, 0, PRESS))
        self.r.tap(events.TOUCH, 0, 0.0)
        self.assertEqual([(t.control, t.kind) for t in self.fired],
                         [(events.TOUCH, PRESS)])


class TestParseDeviceLine(unittest.TestCase):
    def test_key_events(self):
        self.assertEqual(events.parse_device_line("key", ["3", "down"]), ("down", KEY, 3))
        self.assertEqual(events.parse_device_line("key", ["12", "up"]), ("up", KEY, 12))

    def test_encoder_events(self):
        self.assertEqual(events.parse_device_line("enc", ["cw"]), ("rotate", "cw"))
        self.assertEqual(events.parse_device_line("enc", ["ccw"]), ("rotate", "ccw"))
        self.assertEqual(events.parse_device_line("enc", ["press"]), ("down", events.ENCODER, 0))
        self.assertEqual(events.parse_device_line("enc", ["release"]), ("up", events.ENCODER, 0))

    def test_bare_touch_and_rear_are_taps(self):
        self.assertEqual(events.parse_device_line("touch", []), ("tap", events.TOUCH, 0))
        self.assertEqual(events.parse_device_line("rear", []), ("tap", events.REAR, 0))

    def test_touch_with_edges(self):
        self.assertEqual(events.parse_device_line("touch", ["down"]), ("down", events.TOUCH, 0))

    def test_battery(self):
        self.assertEqual(events.parse_device_line("batt", ["73", "1"]), ("battery", 73, True))
        self.assertEqual(events.parse_device_line("batt", ["73"]), ("battery", 73, False))

    def test_garbage_is_dropped_not_raised(self):
        for kind, args in (("key", ["x", "down"]), ("key", ["3"]), ("enc", ["sideways"]),
                           ("batt", ["nope"]), ("ok", []), ("", []), ("key", [])):
            self.assertIsNone(events.parse_device_line(kind, args), (kind, args))


class _StubRenderer:
    def __init__(self):
        self.flashes = []
        self.mode = None
        self.profile = None
        self.activity = 0

    def flash(self, index, colour, seconds=0.35):
        self.flashes.append((index, colour))

    def set_mode(self, name):
        self.mode = name

    def set_profile(self, name):
        self.profile = name

    def note_activity(self):
        self.activity += 1


class _StubDaemon:
    def __init__(self, doc):
        self.cfg = Config(doc)
        self.renderer = _StubRenderer()
        self.battery = None
        self.reloaded = 0

    def reload_config(self):
        self.reloaded += 1
        return True


CONF = {
    "version": 2,
    "device": {"hold_ms": 400, "double_ms": 250},
    "profiles": {
        "default": {
            "keys": [
                {"index": 0, "label": "Slack", "on": {"press": {"launch": "Slack"}}},
                {"index": 1, "on": {"press": {"shortcut": "cmd+c"},
                                    "double": {"shortcut": "cmd+shift+c"},
                                    "hold": {"shell": "echo held"}}},
                {"index": 6, "on": {"press": {"mode": "media", "flash": "00ff88"}}},
                {"index": 7, "on": {"press": {"mode": "nonexistent"}}},
                {"index": 12, "on": {"press": {"action": "profile_next"}}},
            ],
            "encoder": {"cw": {"action": "vol_up"}, "press": {"action": "play_pause"}},
            "rear": {"press": {"action": "reload_config"}},
            "modes": {
                "media": {
                    "activate_key": 6, "flash": "00ff88", "timeout_s": 8,
                    "encoder": {"cw": {"action": "next_track"}},
                    "keys": [{"index": 0, "on": {"press": {"action": "mute"}}}],
                },
            },
        },
        "other": {"keys": [{"index": 0, "on": {"press": {"launch": "Finder"}}}]},
    },
}


class TestDispatcher(unittest.TestCase):
    def setUp(self):
        self.d = _StubDaemon(CONF)
        self.disp = Dispatcher(self.d)
        self.ran: list[tuple[dict, Context]] = []

        def fake_run(binding, ctx=None):
            self.ran.append((binding, ctx))
            return Result(True)

        self.disp.actions.run = fake_run

    def feed(self, kind, args, now=0.0):
        self.disp.feed(kind, args, now=now)

    # --- resolution ---------------------------------------------------------

    def test_resolves_key_press(self):
        self.assertEqual(self.disp.resolve(KEY, 0, PRESS), {"launch": "Slack"})

    def test_unbound_kind_resolves_to_none(self):
        self.assertIsNone(self.disp.resolve(KEY, 0, HOLD))
        self.assertIsNone(self.disp.resolve(KEY, 5, PRESS))

    def test_is_bound_agrees_with_resolve(self):
        # These must never disagree, or press timing goes wrong.
        for index in range(13):
            for kind in (PRESS, RELEASE, HOLD, DOUBLE):
                self.assertEqual(self.disp.is_bound(KEY, index, kind),
                                 self.disp.resolve(KEY, index, kind) is not None,
                                 (index, kind))

    def test_encoder_resolution(self):
        self.assertEqual(self.disp.resolve(events.ENCODER, 0, events.CW), {"action": "vol_up"})
        self.assertIsNone(self.disp.resolve(events.ENCODER, 0, events.CCW))

    def test_rear_resolution(self):
        self.assertEqual(self.disp.resolve(events.REAR, 0, PRESS), {"action": "reload_config"})

    # --- end to end ---------------------------------------------------------

    def test_key_press_runs_its_binding(self):
        self.feed("key", ["0", "down"], now=0.0)
        self.feed("key", ["0", "up"], now=0.05)
        self.assertEqual([b for b, _ in self.ran], [{"launch": "Slack"}])

    def test_context_carries_label_and_profile(self):
        self.feed("key", ["0", "down"], now=0.0)
        self.feed("key", ["0", "up"], now=0.05)
        _, ctx = self.ran[0]
        self.assertEqual((ctx.label, ctx.profile, ctx.index, ctx.kind),
                         ("Slack", "default", 0, PRESS))

    def test_double_tap_picks_the_double_binding(self):
        self.feed("key", ["1", "down"], now=0.0)
        self.feed("key", ["1", "up"], now=0.05)
        self.feed("key", ["1", "down"], now=0.15)
        self.assertEqual([b for b, _ in self.ran], [{"shortcut": "cmd+shift+c"}])

    def test_hold_picks_the_hold_binding(self):
        self.feed("key", ["1", "down"], now=0.0)
        self.disp.tick(0.5)
        self.assertEqual([b for b, _ in self.ran], [{"shell": "echo held"}])

    def test_deferred_press_needs_a_tick(self):
        self.feed("key", ["1", "down"], now=0.0)
        self.feed("key", ["1", "up"], now=0.05)
        self.assertEqual(self.ran, [], "key 1 has a double binding, so press defers")
        self.disp.tick(0.5)
        self.assertEqual([b for b, _ in self.ran], [{"shortcut": "cmd+c"}])

    def test_encoder_rotation_dispatches(self):
        self.feed("enc", ["cw"], now=0.0)
        self.assertEqual([b for b, _ in self.ran], [{"action": "vol_up"}])

    def test_events_register_activity(self):
        self.feed("key", ["0", "down"], now=0.0)
        self.assertGreater(self.d.renderer.activity, 0)

    def test_battery_event_updates_state_and_runs_nothing(self):
        self.feed("batt", ["66", "1"], now=0.0)
        self.assertEqual(self.d.battery, {"percent": 66, "charging": True})
        self.assertEqual(self.ran, [])

    # --- modes --------------------------------------------------------------

    def test_mode_activation(self):
        self.feed("key", ["6", "down"], now=0.0)
        self.feed("key", ["6", "up"], now=0.05)
        self.assertEqual(self.disp.mode, "media")
        self.assertEqual(self.d.renderer.mode, "media")
        self.assertIn((6, "00ff88"), self.d.renderer.flashes)
        self.assertEqual(self.ran, [], "a mode switch is not an action")

    def test_mode_rebinds_the_encoder(self):
        self.feed("key", ["6", "down"], now=0.0)
        self.feed("key", ["6", "up"], now=0.05)
        self.feed("enc", ["cw"], now=0.1)
        self.assertEqual([b for b, _ in self.ran], [{"action": "next_track"}])

    def test_mode_key_override_wins(self):
        self.feed("key", ["6", "down"], now=0.0)
        self.feed("key", ["6", "up"], now=0.05)
        self.feed("key", ["0", "down"], now=0.1)
        self.feed("key", ["0", "up"], now=0.15)
        self.assertEqual([b for b, _ in self.ran], [{"action": "mute"}])

    def test_pressing_the_mode_key_again_leaves_the_mode(self):
        for t in (0.0, 0.05):
            self.feed("key", ["6", "down" if t == 0.0 else "up"], now=t)
        self.assertEqual(self.disp.mode, "media")
        self.feed("key", ["6", "down"], now=1.0)
        self.feed("key", ["6", "up"], now=1.05)
        self.assertIsNone(self.disp.mode)
        self.assertIsNone(self.d.renderer.mode)

    def test_mode_times_out(self):
        self.feed("key", ["6", "down"], now=0.0)
        self.feed("key", ["6", "up"], now=0.05)
        self.disp.tick(4.0)
        self.assertEqual(self.disp.mode, "media")
        self.disp.tick(20.0)
        self.assertIsNone(self.disp.mode, "8s timeout should have elapsed")

    def test_encoder_activity_extends_the_mode(self):
        self.feed("key", ["6", "down"], now=0.0)
        self.feed("key", ["6", "up"], now=0.05)
        self.feed("enc", ["cw"], now=7.0)     # resets the 8s window
        self.disp.tick(12.0)
        self.assertEqual(self.disp.mode, "media", "activity should keep the mode alive")
        self.disp.tick(16.0)
        self.assertIsNone(self.disp.mode)

    def test_unknown_mode_fails_visibly(self):
        self.feed("key", ["7", "down"], now=0.0)
        self.feed("key", ["7", "up"], now=0.05)
        self.assertIsNone(self.disp.mode)
        self.assertTrue(any(i == 7 for i, _ in self.d.renderer.flashes))

    # --- profiles -----------------------------------------------------------

    def test_profile_next_cycles(self):
        # Uses the real Actions.run: `profile_next` routes through it to the dispatcher's
        # own switch_profile, and stubbing run would hide exactly that wiring. Safe to run
        # for real because no subprocess is involved.
        self.disp.actions = Actions(on_profile=self.disp.switch_profile)
        self.feed("key", ["12", "down"], now=0.0)
        self.feed("key", ["12", "up"], now=0.05)
        self.assertEqual(self.d.renderer.profile, "other")

    def test_profile_switch_wraps(self):
        self.disp.switch_profile("next")
        self.d.cfg.doc["active_profile"] = "other"
        self.disp.switch_profile("next")
        self.assertEqual(self.d.renderer.profile, "default")

    def test_unknown_profile_is_rejected(self):
        self.assertFalse(self.disp.switch_profile("ghost"))

    def test_switching_profile_leaves_the_mode(self):
        self.feed("key", ["6", "down"], now=0.0)
        self.feed("key", ["6", "up"], now=0.05)
        self.disp.switch_profile("other")
        self.assertIsNone(self.disp.mode)

    def test_config_change_clears_input_state(self):
        self.feed("key", ["1", "down"], now=0.0)
        self.disp.config_changed()
        self.disp.tick(2.0)
        self.assertEqual(self.ran, [], "a key held across a config change must not fire")

    # --- failure feedback ---------------------------------------------------

    def test_failed_binding_flashes_red(self):
        self.disp.actions.run = lambda b, ctx=None: Result(False, "nope")
        self.feed("key", ["0", "down"], now=0.0)
        self.feed("key", ["0", "up"], now=0.05)
        self.assertTrue(any(c == "ff2200" for _, c in self.d.renderer.flashes))


class TestActionsDispatchOnly(unittest.TestCase):
    """The bits of Actions that don't shell out."""

    def setUp(self):
        self.a = Actions()

    def test_unknown_binding_is_reported(self):
        r = self.a.run({"wat": 1})
        self.assertFalse(r)
        self.assertIn("no recognised action", r.detail)

    def test_non_dict_binding_is_reported(self):
        self.assertFalse(self.a.run("cmd+c"))

    def test_mode_and_profile_defer_to_the_dispatcher(self):
        self.assertTrue(self.a.run({"mode": "media"}))
        self.assertTrue(self.a.run({"profile": "next"}))

    def test_unknown_action_token(self):
        r = self.a.action("teleport", Context())
        self.assertFalse(r)
        self.assertIn("unknown action", r.detail)

    def test_empty_targets_are_rejected(self):
        self.assertFalse(self.a.launch(""))
        self.assertFalse(self.a.shell("   "))
        self.assertFalse(self.a.applescript(""))

    def test_missing_script_is_reported_clearly(self):
        r = self.a.script("/definitely/not/here.sh", Context())
        self.assertFalse(r)
        self.assertIn("not found", r.detail)

    def test_profile_action_needs_wiring(self):
        self.assertFalse(Actions().action("profile_next", Context()))
        seen = []
        wired = Actions(on_profile=seen.append)
        self.assertTrue(wired.action("profile_next", Context()))
        self.assertEqual(seen, ["next"])

    def test_context_env_is_complete(self):
        env = Context(control="key", index=4, kind="hold", label="Deploy",
                      profile="default", mode="media").env()
        self.assertEqual(env["LM_INDEX"], "4")
        self.assertEqual(env["LM_KIND"], "hold")
        self.assertEqual(env["LM_LABEL"], "Deploy")
        self.assertEqual(env["LM_MODE"], "media")
        self.assertTrue(all(k.startswith("LM_") for k in env))


class TestVolume(unittest.TestCase):
    """The volume dial's arithmetic and its helper plumbing.

    A fake `lmvol` (a shell script that logs its argv and answers a fixed level) stands in
    for the real CoreAudio helper, so these run anywhere and touch no actual volume. The
    coarse-mode grid prediction is tested directly on `_coarse_feedback` — going through
    `action()` would synthesise a real media keypress on a machine with lmkey built.
    """

    LEVEL = "37 unmuted"

    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.log = Path(self.dir.name) / "argv.log"
        fake = Path(self.dir.name) / "lmvol"
        fake.write_text(f"#!/bin/sh\necho \"$@\" >> {self.log}\necho \"{self.LEVEL}\"\n")
        fake.chmod(0o755)
        os.environ["LIBREMICRO_LMVOL"] = str(fake)
        self.levels: list[float] = []
        self.a = Actions(on_level=lambda f, label: self.levels.append(f),
                         volume_step=5, volume_mode="fine")

    def tearDown(self):
        os.environ.pop("LIBREMICRO_LMVOL", None)
        with self.a._lock:
            if self.a._vol_trueup is not None:
                self.a._vol_trueup.cancel()
        self.dir.cleanup()

    def seed(self, level: float):
        with self.a._lock:
            self.a._volume, self.a._volume_at = level, time.monotonic()

    def logged(self, timeout=2.0):
        """Wait for the fire-and-forget helper spawn to have run, then return its argv."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.log.exists() and self.log.read_text().strip():
                return self.log.read_text().strip().splitlines()
            time.sleep(0.01)
        return []

    # --- reading ------------------------------------------------------------

    def test_read_volume_prefers_lmvol(self):
        self.assertEqual(self.a._read_volume(), 37.0)
        self.assertEqual(self.logged(), ["get"])

    def test_read_volume_survives_a_missing_helper(self):
        os.environ["LIBREMICRO_LMVOL"] = "/definitely/not/built"
        # Falls back to osascript; whatever that returns, it must not raise.
        try:
            level = self.a._read_volume()
        except Exception as exc:
            self.fail(f"_read_volume raised {exc!r}")
        self.assertTrue(level is None or 0.0 <= level <= 100.0)

    # --- fine mode ------------------------------------------------------------

    def test_nudge_sets_an_absolute_level_and_unmutes_on_up(self):
        self.seed(40)
        self.assertTrue(self.a.nudge_volume(+1))
        self.assertEqual(self.logged(), ["set 45 --no-osd --unmute"])
        self.assertEqual(self.levels, [0.45])

    def test_nudge_down_does_not_unmute(self):
        self.seed(40)
        self.assertTrue(self.a.nudge_volume(-1))
        self.assertEqual(self.logged(), ["set 35 --no-osd"])

    def test_nudge_clamps_at_the_rails(self):
        self.seed(2)
        self.a.nudge_volume(-1)
        self.assertEqual(self.logged(), ["set 0 --no-osd"])

    # --- coarse mode ----------------------------------------------------------

    def test_prediction_snaps_to_the_next_grid_line(self):
        # 40% is off-grid; macOS lands the press on the next 6.25% multiple: 43.75.
        self.seed(40)
        self.a._coarse_feedback(+1)
        self.assertAlmostEqual(self.levels[0], 0.4375)

    def test_prediction_from_a_grid_line_moves_a_full_step(self):
        self.seed(43.75)
        self.a._coarse_feedback(+1)
        self.assertAlmostEqual(self.levels[0], 0.50)

    def test_prediction_down_and_clamped(self):
        self.seed(40)
        self.a._coarse_feedback(-1)
        self.assertAlmostEqual(self.levels[0], 0.375)
        self.seed(1)
        self.a._coarse_feedback(-1)
        self.assertAlmostEqual(self.levels[-1], 0.0)

    def test_stale_cache_is_reseeded_from_a_real_read(self):
        # No seed: the cache is empty, so prediction must first read (fake says 37),
        # then snap up from there to the next grid line, 37.5.
        self.a._coarse_feedback(+1)
        self.assertAlmostEqual(self.levels[0], 0.375)

    def test_trueup_corrects_a_drifted_prediction(self):
        from libremicro import actions as actions_mod
        old = actions_mod._VOL_TRUEUP_S
        actions_mod._VOL_TRUEUP_S = 0.05
        try:
            self.seed(90)
            self.a._coarse_feedback(+1)          # predicts 93.75; the "real" level is 37
            deadline = time.monotonic() + 2.0
            while len(self.levels) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(self.levels[-1], 0.37, "true-up must re-read and correct")
        finally:
            actions_mod._VOL_TRUEUP_S = old

    def test_trueup_is_coalesced_across_a_burst(self):
        self.seed(40)
        for _ in range(4):
            self.a._coarse_feedback(+1)
        with self.a._lock:
            timer = self.a._vol_trueup
        self.assertIsNotNone(timer)
        # Four detents armed and re-armed one timer; no read has happened yet, so the
        # helper log holds nothing (predictions are pure arithmetic).
        self.assertFalse(self.log.exists() and "get" in self.log.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
