"""Key-synthesis tests. Run: python -m unittest discover -s host/daemon/tests

Nothing here synthesises a real keystroke. Two reasons: a test suite that types into whatever
app happens to be focused is a hazard, and the interesting logic — spec parsing, and behaving
sanely when the Swift helper isn't built — is all reachable without posting an event. The
helper's own posting is verified by hand (see its header comment); `lmkey --dry-run` builds
events without posting them if you want to check a spec.

`LIBREMICRO_LMKEY` is pointed at a nonexistent path for most of this file, so the tests give
the same answers on a machine that has built the helper and one that hasn't.
"""
import contextlib
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libremicro import keys  # noqa: E402
from libremicro.keys import Shortcut, parse_shortcut  # noqa: E402


class NoHelper:
    """Context manager: make `helper_path()` report nothing built, whatever the machine has.

    Also captures stderr, both so the suite stays readable and so tests can assert on what the
    user would have been told. Read it back as `ctx.stderr` after the block, or during it.
    """

    def __init__(self, path: str = "/nonexistent/libremicro/lmkey"):
        self.path = path
        self._buf = io.StringIO()

    @property
    def stderr(self) -> str:
        return self._buf.getvalue()

    def __enter__(self):
        self.saved = os.environ.get(keys.HELPER_ENV)
        os.environ[keys.HELPER_ENV] = self.path
        keys.reset_warnings()
        self._redirect = contextlib.redirect_stderr(self._buf)
        self._redirect.__enter__()
        return self

    def __exit__(self, *exc):
        self._redirect.__exit__(*exc)
        if self.saved is None:
            os.environ.pop(keys.HELPER_ENV, None)
        else:
            os.environ[keys.HELPER_ENV] = self.saved
        keys.reset_warnings()
        return False


class TestParseShortcut(unittest.TestCase):
    def test_simple_chord(self):
        sc = parse_shortcut("cmd+shift+4")
        self.assertEqual(sc.key, "4")
        self.assertEqual(sc.mods, ("shift", "cmd"))
        self.assertEqual(sc.spec, "shift+cmd+4")

    def test_single_key_needs_no_modifier(self):
        for spec, key in (("f13", "f13"), ("escape", "escape"), ("space", "space"),
                          ("a", "a"), ("7", "7"), ("f20", "f20")):
            sc = parse_shortcut(spec)
            self.assertEqual(sc, Shortcut(key))
            self.assertEqual(sc.mods, ())
            self.assertEqual(sc.spec, key)

    def test_every_command_alias(self):
        for alias in ("cmd", "command", "⌘", "meta", "super", "win"):
            self.assertEqual(parse_shortcut(f"{alias}+a").mods, ("cmd",), alias)

    def test_every_control_alias(self):
        for alias in ("ctrl", "control", "ctl", "⌃"):
            self.assertEqual(parse_shortcut(f"{alias}+a").mods, ("ctrl",), alias)

    def test_every_option_alias(self):
        for alias in ("opt", "option", "alt", "⌥"):
            self.assertEqual(parse_shortcut(f"{alias}+a").mods, ("opt",), alias)

    def test_every_shift_alias(self):
        for alias in ("shift", "shft", "⇧"):
            self.assertEqual(parse_shortcut(f"{alias}+a").mods, ("shift",), alias)

    def test_fn_aliases(self):
        for alias in ("fn", "function"):
            self.assertEqual(parse_shortcut(f"{alias}+f1").mods, ("fn",), alias)

    def test_modifier_order_is_normalised(self):
        # Every permutation of the same chord must land on one representation, so the web UI
        # can tell "already bound" from "new binding".
        canonical = parse_shortcut("ctrl+opt+shift+cmd+k")
        for spec in ("cmd+shift+opt+ctrl+k", "shift+cmd+ctrl+opt+k",
                     "opt+cmd+ctrl+shift+k", "⌃+⌥+⇧+⌘+k"):
            self.assertEqual(parse_shortcut(spec), canonical, spec)
        self.assertEqual(canonical.spec, "ctrl+opt+shift+cmd+k")

    def test_fn_sorts_first(self):
        self.assertEqual(parse_shortcut("cmd+fn+f1").spec, "fn+cmd+f1")

    def test_case_insensitive(self):
        for spec in ("CMD+SHIFT+4", "Cmd+Shift+4", "cMd+sHiFt+4"):
            self.assertEqual(parse_shortcut(spec), parse_shortcut("cmd+shift+4"), spec)

    def test_surrounding_whitespace_tolerated(self):
        self.assertEqual(parse_shortcut("  cmd+shift+4  "), parse_shortcut("cmd+shift+4"))

    def test_duplicate_modifiers_collapse(self):
        self.assertEqual(parse_shortcut("cmd+command+⌘+a"), parse_shortcut("cmd+a"))

    def test_key_aliases(self):
        cases = {
            "esc": "escape", "enter": "return", "ret": "return", "cr": "return",
            "spc": "space", "backspace": "delete", "bksp": "delete",
            "fwddelete": "forwarddelete", "pgup": "pageup", "pgdn": "pagedown",
            "page_down": "pagedown", "uparrow": "up", "arrowleft": "left",
            "caps": "capslock", "-": "minus", "=": "equal", "[": "leftbracket",
            "]": "rightbracket", ";": "semicolon", "'": "quote", ",": "comma",
            ".": "period", "/": "slash", "`": "grave", "tilde": "grave",
            "equals": "equal", "num_enter": "kpenter",
        }
        for alias, canonical in cases.items():
            self.assertEqual(parse_shortcut(alias).key, canonical, alias)
            self.assertEqual(parse_shortcut(f"cmd+{alias}").key, canonical, alias)

    def test_all_function_keys(self):
        for n in range(1, 21):
            self.assertEqual(parse_shortcut(f"F{n}").key, f"f{n}")

    def test_arrows_escape_tab_return_space_delete(self):
        for name in ("up", "down", "left", "right", "escape", "tab", "return", "space",
                     "delete", "home", "end", "pageup", "pagedown"):
            self.assertEqual(parse_shortcut(f"cmd+{name}").key, name)

    def test_literal_plus(self):
        # '+' is the separator, so the key has to stay reachable.
        self.assertEqual(parse_shortcut("+"), Shortcut("plus"))
        self.assertEqual(parse_shortcut("cmd++"), Shortcut("plus", ("cmd",)))
        self.assertEqual(parse_shortcut("cmd+plus"), Shortcut("plus", ("cmd",)))
        self.assertEqual(parse_shortcut("cmd+shift++").spec, "shift+cmd+plus")

    def test_shortcut_passes_through(self):
        sc = parse_shortcut("cmd+a")
        self.assertIs(parse_shortcut(sc), sc)

    def test_str_matches_spec(self):
        sc = parse_shortcut("opt+cmd+space")
        self.assertEqual(str(sc), sc.spec)
        self.assertEqual(parse_shortcut(sc.spec), sc)

    def test_unknown_modifier_raises(self):
        for spec in ("hyper+a", "mod4+a", "cmd+hyper+a", "cmmd+a"):
            with self.assertRaises(ValueError) as cm:
                parse_shortcut(spec)
            self.assertIn("modifier", str(cm.exception))

    def test_unknown_key_raises(self):
        for spec in ("cmd+nope", "f21", "kp10", "cmd+shift+banana"):
            with self.assertRaises(ValueError) as cm:
                parse_shortcut(spec)
            self.assertIn("key", str(cm.exception))

    def test_modifier_only_raises(self):
        for spec in ("cmd", "shift", "cmd+shift", "⌘"):
            with self.assertRaises(ValueError):
                parse_shortcut(spec)

    def test_two_keys_raises(self):
        with self.assertRaises(ValueError) as cm:
            parse_shortcut("cmd+a+b")
        self.assertIn("not a modifier", str(cm.exception))

    def test_empty_and_malformed_raise(self):
        for spec in ("", "   ", "cmd+", "+a", "cmd++a", "a++b"):
            with self.assertRaises(ValueError, msg=spec):
                parse_shortcut(spec)

    def test_non_string_raises(self):
        for bad in (None, 4, ["cmd", "a"], {"key": "a"}):
            with self.assertRaises(ValueError):
                parse_shortcut(bad)

    def test_error_names_the_offender(self):
        with self.assertRaises(ValueError) as cm:
            parse_shortcut("cmd+shift+banana")
        self.assertIn("banana", str(cm.exception))


class TestMediaActions(unittest.TestCase):
    def test_schema_enum_tokens_are_all_recognised(self):
        # These are exactly the media entries of `binding.action` in host/config/schema.json.
        for token in ("vol_up", "vol_down", "mute", "play_pause", "next_track",
                      "prev_track", "bright_up", "bright_down"):
            self.assertTrue(keys.is_media_action(token), token)
            self.assertEqual(keys.canonical_media_action(token), token)

    def test_aliases_and_case(self):
        cases = {"play": "play_pause", "PAUSE": "play_pause", "next": "next_track",
                 "previous": "prev_track", "volup": "vol_up", "Volume_Down": "vol_down",
                 " mute ": "mute", "brightness_up": "bright_up"}
        for alias, canonical in cases.items():
            self.assertEqual(keys.canonical_media_action(alias), canonical, alias)

    def test_non_media_schema_actions_are_not_media(self):
        # The rest of the action enum is other parts of the daemon's job, not a keypress.
        for token in ("desk_up", "desk_down", "stand_sit", "sleep", "lock",
                      "profile_next", "profile_prev", "reload_config"):
            self.assertFalse(keys.is_media_action(token), token)

    def test_unknown_is_not_media(self):
        for token in ("", "nope", "vol", None, 7):
            self.assertFalse(keys.is_media_action(token), repr(token))

    def test_send_media_rejects_non_media(self):
        with NoHelper():
            for token in ("desk_up", "lock", "bogus"):
                with self.assertRaises(ValueError, msg=token):
                    keys.send_media(token)


class TestGracefulDegradation(unittest.TestCase):
    """With no helper built, every send must be a no-op that says so once."""

    def test_helper_path_is_none_when_missing(self):
        with NoHelper():
            self.assertIsNone(keys.helper_path())

    def test_sends_return_false(self):
        with NoHelper():
            self.assertFalse(keys.send_shortcut("cmd+shift+4"))
            self.assertFalse(keys.send_text("hello"))
            self.assertFalse(keys.send_media("play_pause"))
            self.assertFalse(keys.available())

    def test_sends_do_not_raise(self):
        with NoHelper():
            for i in range(20):
                keys.send_shortcut("cmd+c")
                keys.send_text("x")
                keys.send_media("vol_up")

    def test_warning_is_printed_once_and_names_the_build_command(self):
        with NoHelper() as ctx:
            for _ in range(5):
                keys.send_shortcut("cmd+c")
                keys.send_media("mute")
                keys.send_text("hi")
            out = ctx.stderr
        self.assertEqual(out.count("swiftc"), 1, f"expected one warning, got:\n{out}")
        self.assertIn("lmkey", out)

    def test_bad_spec_still_raises_when_helper_missing(self):
        # A config error must not be hidden by the helper also being absent.
        with NoHelper():
            with self.assertRaises(ValueError):
                keys.send_shortcut("hyper+a")

    def test_capabilities_reports_unbuilt(self):
        with NoHelper():
            caps = keys.capabilities()
        self.assertFalse(caps["built"])
        self.assertIsNone(caps["helper"])
        self.assertIsNone(caps["accessibility"])
        self.assertIn("swiftc", caps["hint"])
        self.assertIn("lmkey", caps["expected_at"])

    def test_non_executable_file_counts_as_unbuilt(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix="-lmkey") as tf:
            with NoHelper(tf.name):
                self.assertIsNone(keys.helper_path())
                self.assertFalse(keys.send_shortcut("cmd+a"))

    def test_default_location_is_host_swift(self):
        self.assertEqual(keys.DEFAULT_HELPER.name, "lmkey")
        self.assertEqual(keys.DEFAULT_HELPER.parent.name, "swift")
        self.assertEqual(keys.DEFAULT_HELPER.parent.parent.name, "host")

    def test_send_text_rejects_non_string(self):
        with NoHelper():
            with self.assertRaises(ValueError):
                keys.send_text(None)

    def test_empty_text_is_a_noop(self):
        with NoHelper():
            self.assertFalse(keys.send_text(""))


class TestSendingIsQueuedNotBlocking(unittest.TestCase):
    """Sends must be ordered and must not make the caller wait.

    Uses a stub helper — a shell script that appends its argv to a file — rather than the real
    one, so the ordering guarantee is checked without a single real keystroke.
    """

    def setUp(self):
        import stat
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.record = os.path.join(self.dir, "record")
        self.stub = os.path.join(self.dir, "lmkey")
        # `sleep` makes overlapping sends detectable: without serialisation the recorded
        # order would scramble.
        with open(self.stub, "w") as fh:
            fh.write('#!/bin/sh\nsleep 0.05\necho "$@" >> "%s"\n' % self.record)
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IXUSR)
        self.saved = os.environ.get(keys.HELPER_ENV)
        os.environ[keys.HELPER_ENV] = self.stub
        keys.reset_warnings()

    def tearDown(self):
        import shutil as _shutil
        keys.drain(timeout=5.0)
        if self.saved is None:
            os.environ.pop(keys.HELPER_ENV, None)
        else:
            os.environ[keys.HELPER_ENV] = self.saved
        keys.reset_warnings()
        _shutil.rmtree(self.dir, ignore_errors=True)

    def lines(self):
        with open(self.record) as fh:
            return [ln.strip() for ln in fh if ln.strip()]

    def test_caller_does_not_wait_for_the_helper(self):
        import time as _time
        start = _time.perf_counter()
        for _ in range(5):
            self.assertTrue(keys.send_shortcut("cmd+c"))
        elapsed = _time.perf_counter() - start
        # Five sends of a helper that takes 50 ms each: serialised, that's 250 ms of work the
        # caller must not have paid for.
        self.assertLess(elapsed, 0.10, "send_shortcut blocked on the helper")

    def test_sends_run_in_order_and_never_overlap(self):
        keys.send_shortcut("cmd+shift+4")
        keys.send_text("hello")
        keys.send_media("play_pause")
        keys.send_shortcut("f13")
        self.assertTrue(keys.drain(timeout=10.0), "queue did not drain")
        self.assertEqual(self.lines(), [
            "chord shift+cmd+4",
            "text -- hello",
            "media play_pause",
            "chord f13",
        ])

    def test_text_is_passed_after_a_double_dash(self):
        keys.send_text("-not-an-option")
        self.assertTrue(keys.drain(timeout=10.0))
        self.assertEqual(self.lines(), ["text -- -not-an-option"])

    def test_media_aliases_are_canonicalised_before_the_helper_sees_them(self):
        keys.send_media("PLAY")
        keys.send_media("volume_up")
        self.assertTrue(keys.drain(timeout=10.0))
        self.assertEqual(self.lines(), ["media play_pause", "media vol_up"])


class TestAccessibilityDenied(unittest.TestCase):
    """A helper that exits 3 means macOS is discarding our events. Say so once, then stop.

    Real TCC state can't be flipped from a test — and shouldn't be — so this drives the same
    path with a stub that exits 3, which is the contract `lmkey` documents for "not trusted".
    """

    def setUp(self):
        import stat
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.count = os.path.join(self.dir, "count")
        self.stub = os.path.join(self.dir, "lmkey")
        with open(self.stub, "w") as fh:
            fh.write('#!/bin/sh\necho x >> "%s"\n'
                     'echo "lmkey: not trusted for Accessibility" >&2\nexit 3\n' % self.count)
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IXUSR)
        self.saved = os.environ.get(keys.HELPER_ENV)
        os.environ[keys.HELPER_ENV] = self.stub
        keys.reset_warnings()
        self._buf = io.StringIO()
        self._redirect = contextlib.redirect_stderr(self._buf)
        self._redirect.__enter__()

    def tearDown(self):
        import shutil as _shutil
        keys.drain(timeout=5.0)
        self._redirect.__exit__(None, None, None)
        if self.saved is None:
            os.environ.pop(keys.HELPER_ENV, None)
        else:
            os.environ[keys.HELPER_ENV] = self.saved
        keys.reset_warnings()
        _shutil.rmtree(self.dir, ignore_errors=True)

    def runs(self):
        try:
            with open(self.count) as fh:
                return len(fh.readlines())
        except FileNotFoundError:
            return 0

    def test_denial_is_reported_once_then_sends_short_circuit(self):
        # The first send can't know yet, so it goes out and learns from the exit code.
        self.assertTrue(keys.send_shortcut("cmd+c"))
        self.assertTrue(keys.drain(timeout=5.0))
        self.assertEqual(self.runs(), 1)

        # Now it's known: no more processes, and no more complaining.
        for _ in range(10):
            self.assertFalse(keys.send_shortcut("cmd+c"))
            self.assertFalse(keys.send_media("mute"))
        self.assertTrue(keys.drain(timeout=5.0))
        self.assertEqual(self.runs(), 1, "kept spawning helpers after a known denial")

        out = self._buf.getvalue()
        self.assertEqual(out.count("not trusted"), 1, f"expected one warning, got:\n{out}")
        self.assertIn("Privacy & Security", out)
        self.assertFalse(keys.available())

    def test_capabilities_reports_denied_with_a_hint(self):
        caps = keys.capabilities(refresh=True)
        self.assertTrue(caps["built"])
        self.assertIs(caps["accessibility"], False)
        self.assertIn("Accessibility", caps["hint"])


@unittest.skipUnless(keys.DEFAULT_HELPER.exists(), "lmkey not built (see its header comment)")
class TestBuiltHelperAgreesOnNames(unittest.TestCase):
    """Guard against the Python and Swift name tables drifting apart.

    They have to be duplicated — validating a config must work with nothing compiled — so
    something has to check them against each other. `lmkey keys` prints its whole vocabulary
    for exactly this. Skipped, not failed, when the helper isn't built.
    """

    @classmethod
    def setUpClass(cls):
        out = subprocess.run([str(keys.DEFAULT_HELPER), "keys"],
                             capture_output=True, text=True, timeout=10)
        assert out.returncode == 0, out.stderr
        cls.swift = {"key": set(), "mod": set(), "media": set()}
        for line in out.stdout.splitlines():
            kind, _, name = line.partition(" ")
            if kind in cls.swift:
                cls.swift[kind].add(name)

    def test_key_names_match(self):
        ours = set(keys.KEY_NAMES) | set(keys.KEY_ALIASES)
        # Swift resolves 'plus'/'+' to the equal key, so it lists both spellings as keys.
        self.assertEqual(ours, self.swift["key"] | {"plus"})

    def test_modifier_names_match(self):
        self.assertEqual(set(keys.MODIFIER_ALIASES), self.swift["mod"])

    def test_media_names_match(self):
        self.assertEqual(set(keys.MEDIA_ALIASES), self.swift["media"])

    def test_every_canonical_key_parses_and_round_trips(self):
        for name in sorted(self.swift["key"]):
            sc = parse_shortcut(f"cmd+{name}")
            self.assertEqual(parse_shortcut(sc.spec), sc, name)

    def test_helper_reports_its_accessibility_state(self):
        caps = keys.capabilities(refresh=True)
        self.assertTrue(caps["built"])
        self.assertIn(caps["accessibility"], (True, False),
                      "check should give a definite answer when the helper runs")


if __name__ == "__main__":
    unittest.main()
