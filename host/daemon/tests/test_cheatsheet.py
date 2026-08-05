"""The cheat sheet's label logic and grid layout, with no helper binary and no window server.

`cheatsheet.build` is deliberately pure so it can be tested exactly like this: the interesting
bugs are a key landing in the wrong grid slot, a mode override being ignored, and a label that
says the mechanism instead of the thing. None of those need AppKit to catch.

Run: python -m unittest discover -s host/daemon/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libremicro import cheatsheet  # noqa: E402
from libremicro.config import Config  # noqa: E402
from libremicro.dispatch import Dispatcher  # noqa: E402
from libremicro.layout import KEY_ROWS  # noqa: E402


class FakeRenderer:
    def flash(self, *a, **k): pass
    def bar(self, *a, **k): pass
    def set_mode(self, *a, **k): pass
    def set_profile(self, *a, **k): pass
    def note_activity(self): pass


class FakeDaemon:
    """Only what the dispatcher touches. Deliberately has no `cheat_sheet` attribute, which is
    also the case the getattr guards in dispatch.py exist for."""

    def __init__(self, doc):
        self.cfg = Config(doc)
        self.renderer = FakeRenderer()
        self.battery = None

    def reload_config(self): return True


def sheet_for(doc):
    d = FakeDaemon(doc)
    return cheatsheet.build(d.cfg, Dispatcher(d))


BASE = {
    "version": 2,
    "active_profile": "default",
    "profiles": {"default": {"keys": [], "encoder": {}, "touch": {}}},
}


def with_profile(**profile):
    doc = {"version": 2, "active_profile": "default", "profiles": {"default": profile}}
    return doc


class TestLabels(unittest.TestCase):
    def test_launch_shows_the_app_not_the_verb(self):
        self.assertEqual(cheatsheet.label_for({"launch": "Slack"}), ("Slack", "launch"))

    def test_shortcut_becomes_mac_glyphs(self):
        label, detail = cheatsheet.label_for({"shortcut": "cmd+shift+4"})
        self.assertEqual(label, "⌘⇧4")
        self.assertEqual(detail, "shortcut")

    def test_shell_open_shows_its_target(self):
        label, _ = cheatsheet.label_for({"shell": "open -na 'Google Chrome'"})
        self.assertEqual(label, "Google Chrome")

    def test_script_shows_the_filename_only(self):
        label, _ = cheatsheet.label_for({"script": "~/bin/deploy.sh"})
        self.assertEqual(label, "deploy.sh")

    def test_action_token_is_humanised(self):
        self.assertEqual(cheatsheet.label_for({"action": "play_pause"}), ("play pause", "built-in"))

    def test_long_text_is_elided_not_dropped(self):
        label, _ = cheatsheet.label_for({"text": "x" * 40})
        self.assertTrue(label.endswith("…"))
        self.assertLessEqual(len(label), 19)

    def test_nothing_bound_is_empty(self):
        self.assertEqual(cheatsheet.label_for(None), ("", ""))
        self.assertEqual(cheatsheet.label_for({}), ("", ""))


class TestGrid(unittest.TestCase):
    def test_grid_is_four_by_rows_with_the_controls_in_their_corners(self):
        s = sheet_for(with_profile(keys=[]))
        self.assertEqual(len(s["rows"]), len(KEY_ROWS))
        for row in s["rows"]:
            self.assertEqual(len(row), cheatsheet.GRID)
        self.assertEqual(s["rows"][0][0]["kind"], "encoder")
        self.assertEqual(s["rows"][0][cheatsheet.GRID - 1]["kind"], "joystick")
        self.assertEqual(s["rows"][-1][0]["kind"], "touch")

    def test_every_logical_key_appears_exactly_once(self):
        # 13 keycaps have to be on the sheet: one missing is a key you can't look up, which is
        # the entire point of the feature.
        keys = [{"index": i, "label": f"K{i}"} for i in range(sum(KEY_ROWS))]
        s = sheet_for(with_profile(keys=keys))
        labels = [c.get("label") for row in s["rows"] for c in row if c["kind"] == "key"]
        self.assertEqual(len(labels), sum(KEY_ROWS))
        self.assertEqual(sorted(labels), sorted(f"K{i}" for i in range(sum(KEY_ROWS))))

    def test_declared_label_wins_over_the_derived_one(self):
        s = sheet_for(with_profile(keys=[
            {"index": 0, "label": "Chat", "on": {"press": {"launch": "Slack"}}}]))
        cell = next(c for row in s["rows"] for c in row if c.get("label") == "Chat")
        self.assertEqual(cell["detail"], "launch")

    def test_label_is_derived_when_none_is_declared(self):
        s = sheet_for(with_profile(keys=[
            {"index": 0, "on": {"press": {"launch": "Slack"}}}]))
        labels = [c.get("label") for row in s["rows"] for c in row]
        self.assertIn("Slack", labels)

    def test_unbound_keys_carry_no_label(self):
        s = sheet_for(with_profile(keys=[]))
        for row in s["rows"]:
            for cell in row:
                if cell["kind"] == "key":
                    self.assertNotIn("label", cell)

    def test_hold_is_shown_when_press_is_unbound(self):
        s = sheet_for(with_profile(keys=[
            {"index": 0, "on": {"hold": {"launch": "Xcode"}}}]))
        labels = [c.get("label") for row in s["rows"] for c in row]
        self.assertIn("Xcode", labels)

    def test_encoder_shows_turn_and_press_together(self):
        s = sheet_for(with_profile(
            keys=[], encoder={"cw": {"action": "vol_up"}, "press": {"action": "mute"}}))
        enc = s["rows"][0][0]
        self.assertEqual(enc["label"], "vol up")
        self.assertEqual(enc["detail"], "press: mute")

    def test_joystick_summarises_when_several_directions_are_bound(self):
        s = sheet_for(with_profile(keys=[], joystick={
            "n": {"press": {"launch": "A"}}, "s": {"press": {"launch": "B"}}}))
        joy = s["rows"][0][cheatsheet.GRID - 1]
        self.assertEqual(joy["detail"], "2 dirs")

    def test_single_joystick_direction_shows_its_own_label(self):
        s = sheet_for(with_profile(keys=[], joystick={"n": {"press": {"launch": "Notes"}}}))
        self.assertEqual(s["rows"][0][cheatsheet.GRID - 1]["label"], "Notes")


class TestModeAndProfile(unittest.TestCase):
    def test_the_sheet_names_the_active_profile(self):
        doc = {"version": 2, "active_profile": "coding",
               "profiles": {"coding": {"keys": []}, "default": {"keys": []}}}
        self.assertEqual(sheet_for(doc)["title"], "coding")

    def test_no_mode_is_reported_as_none(self):
        self.assertIsNone(sheet_for(with_profile(keys=[]))["mode"])

    def test_a_mode_overrides_the_labels_it_rebinds(self):
        doc = with_profile(
            keys=[{"index": 0, "on": {"press": {"launch": "Slack"}}},
                  {"index": 1, "on": {"press": {"launch": "Mail"}}}],
            modes={"media": {"keys": [{"index": 0, "on": {"press": {"action": "play_pause"}}}]}})
        d = FakeDaemon(doc)
        disp = Dispatcher(d)
        disp._mode = "media"
        s = cheatsheet.build(d.cfg, disp)
        labels = [c.get("label") for row in s["rows"] for c in row]
        self.assertEqual(s["mode"], "media")
        self.assertIn("play pause", labels)      # rebound by the mode
        self.assertNotIn("Slack", labels)
        self.assertIn("Mail", labels)            # untouched by the mode, still shown


class TestHelperAbsence(unittest.TestCase):
    def test_state_reports_an_unbuilt_helper_with_a_hint(self):
        import os
        old = os.environ.get(cheatsheet.HELPER_ENV)
        os.environ[cheatsheet.HELPER_ENV] = "/nonexistent/lmhud"
        try:
            sheet = cheatsheet.CheatSheet(FakeDaemon(with_profile(keys=[])))
            state = sheet.state()
            self.assertFalse(state["built"])
            self.assertIn("swiftc", state["hint"])
            # Showing must fail softly: a missing helper is a warning, never an exception on
            # the dispatch path.
            self.assertFalse(sheet.show())
            self.assertFalse(sheet.visible)
            self.assertFalse(sheet.hide())
        finally:
            if old is None:
                del os.environ[cheatsheet.HELPER_ENV]
            else:
                os.environ[cheatsheet.HELPER_ENV] = old


if __name__ == "__main__":
    unittest.main()
