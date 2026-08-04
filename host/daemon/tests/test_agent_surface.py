"""The agentic-coding control surface.

There is no live Claude Code session here and no network, so the status source is faked at
the only place it enters the system: `ingest()`, which is the body of the HTTP route a hook
POSTs to. That is the whole point of the seam — the mapping table, the expiry rules and the
LED decisions are all reachable without a session, a microphone, or a real clock.

Time is injected everywhere. Staleness and the `done` decay are the two rules most likely to
be got wrong and the two least likely to be noticed if they are, so they are tested against a
fake clock rather than sleeps.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libremicro import agent_surface as ag  # noqa: E402
from libremicro.actions import Actions, Context, Result  # noqa: E402
from libremicro.agent_surface import (  # noqa: E402
    DONE, ERROR, IDLE, UNKNOWN, WAITING, WORKING, AgentSurface, Dictation, map_event,
)
from libremicro.config import Config  # noqa: E402


# --- stubs ------------------------------------------------------------------

class _StubRenderer:
    """Records the two renderer calls the surface is allowed to make."""

    def __init__(self):
        self.calls: list[tuple] = []

    def pulse(self, index, color, period=1.4):
        self.calls.append(("pulse", index, color, period))

    def flash(self, index, color, seconds=0.35):
        self.calls.append(("flash", index, color, seconds))

    # --- queries the tests use ---
    def pulsing(self) -> dict:
        """Final pulse state per key: colour, or None if cleared."""
        out = {}
        for call in self.calls:
            if call[0] == "pulse":
                out[call[1]] = call[2]
        return out

    def flashed(self) -> dict:
        out = {}
        for call in self.calls:
            if call[0] == "flash":
                out[call[1]] = (call[2], call[3])
        return out

    def clear(self):
        self.calls.clear()


class _StubSender:
    """Stands in for libremicro.keys."""

    def __init__(self, ok=True):
        self.ok = ok
        self.shortcuts: list[str] = []
        self.texts: list[str] = []

    def send_shortcut(self, spec):
        self.shortcuts.append(spec)
        return self.ok

    def send_text(self, text):
        self.texts.append(text)
        return self.ok


class _StubDaemon:
    def __init__(self, agent_cfg=None):
        doc = {"version": 2, "profiles": {"default": {}}}
        if agent_cfg is not None:
            doc["agent"] = agent_cfg
        self.cfg = Config(doc)
        self.renderer = _StubRenderer()


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(agent_cfg=None, sender=None, clock=None, dictation=None):
    daemon = _StubDaemon(agent_cfg)
    clock = clock or _Clock()
    surface = AgentSurface(daemon, clock=clock, sender=sender or _StubSender(),
                           dictation=dictation, probe=lambda: 0)
    return daemon, surface, clock


def hook(event, session="s1", **extra):
    payload = {"session_id": session, "event": event, "cwd": "/Users/x/AI/libremicro"}
    payload.update(extra)
    return payload


# --- the status model -------------------------------------------------------

class TestMapEvent(unittest.TestCase):
    """One case per row of the event -> status table."""

    def status(self, event, **extra):
        return map_event(hook(event, **extra))[0]

    def test_turn_lifecycle(self):
        self.assertEqual(self.status("SessionStart", source="startup"), IDLE)
        self.assertEqual(self.status("UserPromptSubmit"), WORKING)
        self.assertEqual(self.status("PreToolUse", tool_name="Bash"), WORKING)
        self.assertEqual(self.status("PostToolUse", tool_name="Bash"), WORKING)
        self.assertEqual(self.status("Stop"), DONE)

    def test_permission_request_is_waiting(self):
        self.assertEqual(self.status("PermissionRequest", tool_name="Bash"), WAITING)

    def test_permission_notification_is_waiting(self):
        status, detail = map_event(hook("Notification",
                                        notification_type="permission_prompt",
                                        message="Claude needs your permission to run Bash"))
        self.assertEqual(status, WAITING)
        self.assertIn("permission", detail)

    def test_other_notification_types(self):
        self.assertEqual(self.status("Notification", notification_type="idle_prompt"), IDLE)
        self.assertEqual(self.status("Notification", notification_type="agent_needs_input"),
                         WAITING)
        self.assertEqual(self.status("Notification", notification_type="agent_completed"),
                         DONE)

    def test_unbearing_notification_leaves_status_alone(self):
        # auth_success says nothing about what the session is doing.
        self.assertIsNone(self.status("Notification", notification_type="auth_success"))

    def test_api_failure_is_error(self):
        status, detail = map_event(hook("StopFailure", error_type="rate_limit"))
        self.assertEqual(status, ERROR)
        self.assertEqual(detail, "rate_limit")

    def test_failed_tool_is_not_an_error(self):
        # A grep that matched nothing is routine. Calling it ERROR would make the state that
        # means "the session is broken" meaningless.
        self.assertEqual(self.status("PostToolUseFailure", tool_name="Grep"), WORKING)

    def test_session_end_is_a_removal_not_a_status(self):
        status, detail = map_event(hook("SessionEnd", reason="logout"))
        self.assertEqual(status, ag.ENDED)
        self.assertNotIn(ag.ENDED, ag.STATUSES)
        self.assertEqual(detail, "logout")

    def test_unknown_event_changes_nothing(self):
        # Claude Code adds hook events. A new one must not be able to invent a state.
        for event in ("WorktreeCreate", "ConfigChange", "SomethingFromTheFuture", ""):
            self.assertIsNone(self.status(event), event)

    def test_detail_carries_the_tool_name(self):
        self.assertEqual(map_event(hook("PreToolUse", tool_name="Edit"))[1], "Edit")

    def test_long_assistant_message_is_clipped(self):
        detail = map_event(hook("Stop", last_assistant_message="x " * 200))[1]
        self.assertLessEqual(len(detail), 81)

    def test_garbage_payload_does_not_raise(self):
        for payload in ({}, {"event": None}, {"event": 5}, {"notification_type": 1}):
            self.assertIsInstance(map_event(payload), tuple)


class TestLedMapping(unittest.TestCase):
    """The design decisions, asserted so they can't drift silently."""

    def test_every_status_has_a_mapping(self):
        for status in ag.STATUSES:
            self.assertIn(status, ag.LED_MAP, status)

    def test_states_that_need_you_pulse_and_states_that_dont_are_solid(self):
        for status in (WORKING, WAITING, ERROR):
            self.assertEqual(ag.LED_MAP[status].behaviour, "pulse", status)
        for status in (UNKNOWN, IDLE, DONE):
            self.assertEqual(ag.LED_MAP[status].behaviour, "solid", status)

    def test_waiting_pulses_fastest(self):
        # Rate is how urgency is ranked, so waiting must be unambiguously the quickest.
        waiting = ag.LED_MAP[WAITING].period
        for status in (WORKING, ERROR):
            self.assertLess(waiting, ag.LED_MAP[status].period, status)
        self.assertLess(waiting * 2, ag.LED_MAP[WORKING].period)

    def test_unknown_is_dim_but_not_off(self):
        # An unlit key is indistinguishable from a dead daemon, so 'no signal' must still glow.
        colour = ag.LED_MAP[UNKNOWN].color
        self.assertNotEqual(colour, "000000")
        self.assertLess(int(colour, 16), 0x333333)

    def test_waiting_hue_is_unique(self):
        others = [led.color for status, led in ag.LED_MAP.items() if status != WAITING]
        self.assertNotIn(ag.LED_MAP[WAITING].color, others)

    def test_colour_can_be_overridden_without_changing_behaviour(self):
        _, surface, _ = make({"colors": {"working": "abcdef"}})
        led = surface.led_for(WORKING)
        self.assertEqual(led.color, "abcdef")
        self.assertEqual(led.behaviour, "pulse")
        self.assertEqual(led.period, ag.LED_MAP[WORKING].period)


# --- ingest -----------------------------------------------------------------

class TestIngest(unittest.TestCase):
    def setUp(self):
        self.d, self.s, self.clock = make({"status_key": 3})

    def test_first_report_creates_a_session(self):
        result = self.s.ingest(hook("UserPromptSubmit"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], WORKING)
        self.assertEqual(len(self.s.sessions), 1)

    def test_label_defaults_to_the_project_directory(self):
        self.s.ingest(hook("SessionStart", cwd="/Users/x/AI/libremicro"))
        self.assertEqual(self.s.selected().label, "libremicro")

    def test_effort_is_read_from_the_payload(self):
        self.s.ingest(hook("PreToolUse", effort={"level": "xhigh"}))
        self.assertEqual(self.s.selected().effort, "xhigh")

    def test_effort_also_accepts_a_bare_string(self):
        self.s.ingest(hook("PreToolUse", effort="high"))
        self.assertEqual(self.s.selected().effort, "high")

    def test_session_id_is_required(self):
        result = self.s.ingest({"event": "Stop"})
        self.assertFalse(result["ok"])
        self.assertIn("session_id", result["errors"][0])

    def test_non_object_payload_is_rejected_not_raised(self):
        self.assertFalse(self.s.ingest("not a dict")["ok"])
        self.assertFalse(self.s.ingest(None)["ok"])

    def test_unknown_event_keeps_the_status_but_proves_liveness(self):
        self.s.ingest(hook("PreToolUse", tool_name="Bash"))
        self.clock.t += 30
        self.s.ingest(hook("WorktreeCreate"))
        self.assertEqual(self.s.status(), WORKING, "an unknown event must not clear status")
        self.assertEqual(self.s.selected().last_seen, self.clock.t,
                         "but it must refresh staleness")

    def test_session_end_removes_the_session(self):
        self.s.ingest(hook("Stop"))
        self.s.ingest(hook("SessionEnd", reason="logout"))
        self.assertEqual(self.s.sessions, {})
        self.assertEqual(self.s.status(), UNKNOWN)

    def test_explicit_status_is_the_escape_hatch_for_other_harnesses(self):
        self.s.ingest({"session_id": "z", "status": WAITING, "detail": "codex says so"})
        self.assertEqual(self.s.status(), WAITING)
        self.assertEqual(self.s.selected().detail, "codex says so")

    def test_bogus_explicit_status_is_ignored(self):
        self.s.ingest(hook("PreToolUse"))
        self.s.ingest({"session_id": "s1", "status": "on fire"})
        self.assertEqual(self.s.status(), WORKING)

    def test_terminal_hints_are_kept(self):
        self.s.ingest(hook("SessionStart", terminal={"app": "iTerm2", "tmux_pane": "%3"}))
        self.assertEqual(self.s.selected().terminal["tmux_pane"], "%3")


class TestExpiry(unittest.TestCase):
    """The rule that keeps the pad honest when a session dies mid-turn."""

    def setUp(self):
        self.d, self.s, self.clock = make(
            {"stale_after_s": 60, "session_ttl_s": 600, "done_hold_s": 10})

    def test_working_expires_to_unknown(self):
        self.s.ingest(hook("PreToolUse", tool_name="Bash"))
        self.clock.t += 59
        self.assertEqual(self.s.status(), WORKING)
        self.clock.t += 2
        self.assertEqual(self.s.status(), UNKNOWN,
                         "a session killed mid-turn must not read as working forever")

    def test_waiting_expires_too(self):
        self.s.ingest(hook("PermissionRequest", tool_name="Bash"))
        self.clock.t += 61
        self.assertEqual(self.s.status(), UNKNOWN)

    def test_idle_does_not_expire_at_stale_after(self):
        # Idle is a resting state: silence is exactly what it predicts, so silence is not
        # evidence against it. Only session_ttl retires it.
        self.s.ingest(hook("SessionStart"))
        self.clock.t += 61
        self.assertEqual(self.s.status(), IDLE)

    def test_a_fresh_report_resets_staleness(self):
        self.s.ingest(hook("PreToolUse"))
        self.clock.t += 50
        self.s.ingest(hook("PostToolUse"))
        self.clock.t += 50
        self.assertEqual(self.s.status(), WORKING)

    def test_done_decays_to_idle(self):
        self.s.ingest(hook("Stop"))
        self.assertEqual(self.s.status(), DONE)
        self.clock.t += 11
        self.assertEqual(self.s.status(), IDLE, "'done' is a notification, not a state")

    def test_session_is_forgotten_after_the_ttl(self):
        self.s.ingest(hook("SessionStart"))
        self.clock.t += 601
        self.s.ingest(hook("SessionStart", session="s2"))
        self.assertEqual(list(self.s.sessions), ["s2"])

    def test_expired_status_is_reported_as_stale(self):
        self.s.ingest(hook("PreToolUse"))
        self.clock.t += 61
        entry = self.s.snapshot()["sessions"][0]
        self.assertTrue(entry["stale"])
        self.assertEqual(entry["status"], UNKNOWN)
        self.assertEqual(entry["reported_status"], WORKING,
                         "the raw report is still visible for diagnosis")

    def test_session_ttl_cannot_undercut_stale_after(self):
        _, surface, _ = make({"stale_after_s": 900, "session_ttl_s": 5})
        self.assertGreaterEqual(surface.session_ttl, surface.stale_after)

    def test_nonsense_timings_fall_back_to_defaults(self):
        _, surface, _ = make({"stale_after_s": "soon", "done_hold_s": None})
        self.assertEqual(surface.stale_after, 90.0)
        self.assertEqual(surface.done_hold, 10.0)


# --- selection --------------------------------------------------------------

class TestSelection(unittest.TestCase):
    def setUp(self):
        self.d, self.s, self.clock = make({"status_key": 3})

    def test_auto_follows_the_most_recent_session(self):
        self.s.ingest(hook("SessionStart", session="a"))
        self.clock.t += 5
        self.s.ingest(hook("SessionStart", session="b"))
        self.assertEqual(self.s.selected().id, "b")

    def test_waiting_takes_priority_over_recency(self):
        self.s.ingest(hook("PermissionRequest", session="a"))
        self.clock.t += 5
        self.s.ingest(hook("PreToolUse", session="b"))
        self.assertEqual(self.s.selected().id, "a",
                         "the session that needs you wins the display")

    def test_priority_can_be_turned_off(self):
        _, surface, clock = make({"prioritise_waiting": False})
        surface.ingest(hook("PermissionRequest", session="a"))
        clock.t += 5
        surface.ingest(hook("PreToolUse", session="b"))
        self.assertEqual(surface.selected().id, "b")

    def test_cycling_pins_a_session(self):
        self.s.ingest(hook("SessionStart", session="a"))
        self.clock.t += 5
        self.s.ingest(hook("SessionStart", session="b"))
        self.assertTrue(self.s.cycle_session(+1))
        self.assertEqual(self.s.selected().id, "b")     # slots: auto, b, a
        self.s.cycle_session(+1)
        self.assertEqual(self.s.selected().id, "a")

    def test_the_cycle_always_offers_a_way_back_to_auto(self):
        self.s.ingest(hook("SessionStart", session="a"))
        self.s.cycle_session(+1)
        self.assertIsNotNone(self.s.snapshot()["pinned"])
        self.s.cycle_session(+1)
        self.assertIsNone(self.s.snapshot()["pinned"], "a pin you can't undo is a trap")

    def test_pinning_survives_a_newer_session(self):
        self.s.ingest(hook("SessionStart", session="a"))
        self.s.cycle_session(+1)
        self.clock.t += 5
        self.s.ingest(hook("PreToolUse", session="b"))
        self.assertEqual(self.s.selected().id, "a")

    def test_ending_the_pinned_session_reverts_to_auto(self):
        self.s.ingest(hook("SessionStart", session="a"))
        self.s.ingest(hook("SessionStart", session="b"))
        self.s.cycle_session(+1)
        pinned = self.s.snapshot()["pinned"]
        self.s.ingest(hook("SessionEnd", session=pinned))
        self.assertIsNone(self.s.snapshot()["pinned"])
        self.assertIsNotNone(self.s.selected())

    def test_cycling_with_no_sessions_fails_visibly(self):
        result = self.s.cycle_session(+1)
        self.assertFalse(result)
        self.assertIn("no sessions", result.detail)


# --- LED painting -----------------------------------------------------------

class TestPainting(unittest.TestCase):
    CONF = {"status_key": 3, "alert_key": 4, "approve_key": 9, "deny_key": 10,
            "effort_keys": [0, 1, 2, 5, 6], "dictate_key": 11}

    def setUp(self):
        self.d, self.s, self.clock = make(self.CONF)

    def test_unknown_is_painted_before_anything_reports(self):
        self.assertEqual(self.s.targets()[3], ag.LED_MAP[UNKNOWN])

    def test_status_key_tracks_the_selected_session(self):
        self.s.ingest(hook("PreToolUse", tool_name="Bash"))
        self.assertEqual(self.s.targets()[3], ag.LED_MAP[WORKING])
        self.s.ingest(hook("Stop"))
        self.assertEqual(self.s.targets()[3], ag.LED_MAP[DONE])

    def test_approve_and_deny_light_up_only_while_waiting(self):
        self.s.ingest(hook("PreToolUse"))
        targets = self.s.targets()
        self.assertNotIn(9, targets)
        self.assertNotIn(10, targets)
        self.s.ingest(hook("PermissionRequest", tool_name="Bash"))
        targets = self.s.targets()
        # Lighting the keys you are being asked to press is the whole affordance.
        self.assertEqual(targets[9], ag.LED_MAP[WAITING])
        self.assertEqual(targets[10], ag.LED_MAP[WAITING])

    def test_alert_key_fires_for_a_session_you_are_not_watching(self):
        self.s.ingest(hook("PreToolUse", session="a"))
        self.s.cycle_session(+1)                        # pin a
        self.s.ingest(hook("PermissionRequest", session="b"))
        targets = self.s.targets()
        self.assertEqual(targets[3], ag.LED_MAP[WORKING], "still showing the pinned session")
        self.assertEqual(targets[4], ag.LED_MAP[WAITING], "but the other one is flagged")

    def test_alert_key_is_dark_when_nothing_else_waits(self):
        self.s.ingest(hook("PermissionRequest", session="a"))
        self.assertNotIn(4, self.s.targets())

    def test_effort_bar_lights_up_to_the_reported_level(self):
        self.s.ingest(hook("PreToolUse", effort={"level": "high"}))
        targets = self.s.targets()
        self.assertEqual([targets[i].color for i in (0, 1, 2)], [ag.EFFORT_ON] * 3)
        self.assertNotIn(5, targets)
        self.assertNotIn(6, targets)

    def test_pending_effort_pulses_against_the_reported_level(self):
        self.s.ingest(hook("PreToolUse", effort={"level": "high"}))
        self.s.nudge_effort(+1)                         # -> xhigh, not yet applied
        targets = self.s.targets()
        self.assertEqual(targets[5].color, ag.EFFORT_PENDING)
        self.assertEqual(targets[5].behaviour, "pulse", "uncommitted must look uncommitted")
        self.assertEqual(targets[2].color, ag.EFFORT_ON)

    def test_dictation_wins_its_key(self):
        self.s.dictation = Dictation({"enabled": True}, recorder=_FakeRecorder(),
                                     transcriber=lambda p: "hi")
        self.s.dictation.start()
        self.assertEqual(self.s.targets()[11].color, ag.DICTATE_RECORDING)

    # --- how it reaches the renderer ---

    def test_solid_status_is_held_with_a_long_flash(self):
        # The renderer has no "hold this colour" call and shouldn't grow one. A flash longer
        # than its fade window holds full colour, so re-arming it is how a status sits solid.
        self.s.tick(self.clock.t)
        colour, seconds = self.d.renderer.flashed()[3]
        self.assertEqual(colour, ag.LED_MAP[UNKNOWN].color)
        self.assertGreater(seconds, 0.35, "must outlast the renderer's flash fade")

    def test_pulsing_status_uses_pulse_with_its_period(self):
        self.s.ingest(hook("PermissionRequest"))
        self.s.tick(self.clock.t)
        self.assertEqual(self.d.renderer.pulsing()[3], ag.LED_MAP[WAITING].color)
        periods = [c[3] for c in self.d.renderer.calls if c[0] == "pulse" and c[1] == 3]
        self.assertEqual(periods[-1], ag.LED_MAP[WAITING].period)

    def test_sustain_is_rearmed_but_not_every_frame(self):
        self.s.tick(self.clock.t)
        self.d.renderer.clear()
        self.s.tick(self.clock.t + 0.05)
        self.assertEqual(self.d.renderer.calls, [], "30 fps must not mean 30 flashes")
        self.s.tick(self.clock.t + 0.5)
        self.assertIn(3, self.d.renderer.flashed())

    def test_a_key_that_stops_having_a_role_is_released(self):
        self.s.ingest(hook("PermissionRequest"))
        self.s.tick(self.clock.t)
        self.assertIn(9, self.d.renderer.pulsing())
        self.d.renderer.clear()
        self.s.ingest(hook("Stop"))
        self.s.tick(self.clock.t + 1)
        self.assertIsNone(self.d.renderer.pulsing()[9],
                          "approve must go dark when nothing is waiting")

    def test_tick_survives_a_daemon_with_no_renderer(self):
        self.d.renderer = None
        self.s.tick(self.clock.t)        # must not raise

    def test_tick_does_nothing_when_disabled(self):
        _, surface, clock = make({"enabled": False, "status_key": 3})
        surface.tick(clock.t)
        self.assertEqual(surface.d.renderer.calls, [])

    def test_no_roles_configured_paints_nothing(self):
        _, surface, clock = make({})
        surface.ingest(hook("PreToolUse"))
        surface.tick(clock.t)
        self.assertEqual(surface.d.renderer.calls, [])

    def test_out_of_range_key_indices_are_ignored(self):
        _, surface, clock = make({"status_key": 99, "effort_keys": [0, "x", 40, 2]})
        surface.tick(clock.t)
        self.assertEqual(surface._key("status_key"), None)
        self.assertEqual(surface._key_list("effort_keys"), [0, 2])


# --- actions ----------------------------------------------------------------

class TestActionRouting(unittest.TestCase):
    def setUp(self):
        self.sender = _StubSender()
        # Fake recorder and transcriber, so exercising every token cannot open the real
        # microphone or shell out to whisper.
        dictation = Dictation({"enabled": True}, insert=lambda t: True,
                              recorder=_FakeRecorder(), transcriber=lambda p: "spoken")
        self.d, self.s, self.clock = make({"status_key": 3}, sender=self.sender,
                                          dictation=dictation)

    def test_extend_actions_routes_agent_tokens_and_passes_the_rest_through(self):
        seen = []
        actions = Actions(on_profile=seen.append)
        self.s.extend_actions(actions)
        # Still reaches the built-in table.
        self.assertTrue(actions.run({"action": "profile_next"}, Context()))
        self.assertEqual(seen, ["next"])
        # And an unknown token still reports itself as unknown.
        self.assertFalse(actions.run({"action": "teleport"}, Context()))
        # Agent tokens now resolve.
        self.s.ingest(hook("SessionStart", session="a"))
        self.assertTrue(actions.run({"action": "agent_session_next"}, Context()))

    def test_every_advertised_token_is_handled(self):
        for token in ag.AGENT_ACTIONS:
            result = self.s.run_action(token, Context())
            self.assertIsInstance(result, Result, token)
            self.assertNotIn("unknown agent action", result.detail, token)

    def test_unknown_agent_token_is_reported(self):
        result = self.s.run_action("agent_do_my_taxes", Context())
        self.assertFalse(result)
        self.assertIn("unknown agent action", result.detail)

    def test_disabled_surface_refuses_every_action(self):
        _, surface, _ = make({"enabled": False}, sender=self.sender)
        result = surface.run_action("agent_approve", Context())
        self.assertFalse(result)
        self.assertIn("disabled", result.detail)


class TestApproveDeny(unittest.TestCase):
    def setUp(self):
        self.sender = _StubSender()
        self.d, self.s, self.clock = make({"approve_key": 9, "deny_key": 10},
                                          sender=self.sender)

    def test_approve_is_refused_unless_the_session_says_it_is_waiting(self):
        self.s.ingest(hook("PreToolUse"))
        result = self.s.respond("approve")
        self.assertFalse(result)
        self.assertIn("not waiting", result.detail)
        self.assertEqual(self.sender.shortcuts, [],
                         "an approve key must never be a blind Enter into whatever is focused")

    def test_approve_is_refused_when_status_is_unknown(self):
        self.assertFalse(self.s.respond("approve"))

    def test_approve_sends_return_when_waiting(self):
        self.s.ingest(hook("PermissionRequest", tool_name="Bash"))
        self.assertTrue(self.s.respond("approve"))
        self.assertEqual(self.sender.shortcuts, ["return"])

    def test_deny_sends_escape(self):
        self.s.ingest(hook("Notification", notification_type="permission_prompt"))
        self.assertTrue(self.s.respond("deny"))
        self.assertEqual(self.sender.shortcuts, ["escape"])

    def test_keystrokes_are_configurable(self):
        _, surface, _ = make({"approve": {"shortcut": "2"}, "deny": {"shortcut": "3"}},
                             sender=self.sender)
        surface.ingest(hook("PermissionRequest"))
        surface.respond("approve")
        surface.respond("deny")
        self.assertEqual(self.sender.shortcuts, ["2", "3"])

    def test_deny_can_type_a_reason_before_submitting(self):
        _, surface, _ = make({"deny": {"text": "stop, use the existing helper",
                                       "shortcut": "return"}}, sender=self.sender)
        surface.ingest(hook("PermissionRequest"))
        self.assertTrue(surface.respond("deny"))
        self.assertEqual(self.sender.texts, ["stop, use the existing helper"])

    def test_the_guard_can_be_removed_deliberately(self):
        _, surface, _ = make({"require_waiting": False}, sender=self.sender)
        self.assertTrue(surface.respond("approve"))

    def test_missing_key_synthesis_degrades_to_a_failed_result(self):
        broken = _StubSender(ok=False)
        _, surface, _ = make({"require_waiting": False}, sender=broken)
        result = surface.respond("approve")
        self.assertFalse(result)
        self.assertIn("did not fire", result.detail)

    def test_a_sender_that_raises_does_not_propagate(self):
        class _Exploding:
            def send_shortcut(self, spec):
                raise RuntimeError("helper died")

            def send_text(self, text):
                raise RuntimeError("helper died")

        ag.reset_warnings()
        _, surface, _ = make({"require_waiting": False}, sender=_Exploding())
        self.assertFalse(surface.respond("approve"))


class TestEffortKnob(unittest.TestCase):
    def setUp(self):
        self.sender = _StubSender()
        self.d, self.s, self.clock = make({"effort_keys": [0, 1, 2, 3, 4]},
                                          sender=self.sender)

    def test_nudging_moves_one_rung_from_the_reported_level(self):
        self.s.ingest(hook("PreToolUse", effort={"level": "medium"}))
        self.assertIn("high", self.s.nudge_effort(+1).detail)
        self.assertEqual(self.s.snapshot()["effort"]["pending"], "high")

    def test_nudging_clamps_at_both_ends(self):
        self.s.ingest(hook("PreToolUse", effort={"level": "max"}))
        for _ in range(3):
            self.s.nudge_effort(+1)
        self.assertEqual(self.s.snapshot()["effort"]["pending"], "max")
        for _ in range(10):
            self.s.nudge_effort(-1)
        self.assertEqual(self.s.snapshot()["effort"]["pending"], "low")

    def test_nudging_does_not_apply_anything(self):
        # A dial gets spun. Applying per detent would fire five slash commands.
        self.s.ingest(hook("PreToolUse", effort={"level": "low"}))
        for _ in range(4):
            self.s.nudge_effort(+1)
        self.assertEqual(self.sender.texts, [])

    def test_apply_types_the_slash_command(self):
        self.s.ingest(hook("PreToolUse", effort={"level": "low"}))
        self.s.nudge_effort(+1)
        result = self.s.apply_effort()
        self.assertTrue(result)
        self.assertEqual(self.sender.texts, ["/effort medium"])
        self.assertEqual(self.sender.shortcuts, ["return"])

    def test_apply_with_nothing_pending_is_refused(self):
        result = self.s.apply_effort()
        self.assertFalse(result)
        self.assertIn("no pending", result.detail)

    def test_apply_none_makes_the_dial_read_only_rather_than_fake(self):
        # There is no IPC to set a live session's effort. Saying so beats pretending.
        _, surface, _ = make({"effort": {"apply": "none"}}, sender=self.sender)
        surface.nudge_effort(+1)
        result = surface.apply_effort()
        self.assertFalse(result)
        self.assertIn("display-only", result.detail)
        self.assertEqual(self.sender.texts, [])

    def test_the_session_reporting_the_level_clears_the_pending_flag(self):
        self.s.ingest(hook("PreToolUse", effort={"level": "low"}))
        self.s.nudge_effort(+1)
        self.assertEqual(self.s.snapshot()["effort"]["pending"], "medium")
        self.s.ingest(hook("PostToolUse", effort={"level": "medium"}))
        self.assertIsNone(self.s.snapshot()["effort"]["pending"],
                          "the ask landed, so stop showing it as outstanding")

    def test_drift_stays_visible_when_the_ask_did_not_land(self):
        self.s.ingest(hook("PreToolUse", effort={"level": "low"}))
        self.s.nudge_effort(+1)
        self.s.apply_effort()
        self.s.ingest(hook("PostToolUse", effort={"level": "low"}))
        snapshot = self.s.snapshot()["effort"]
        self.assertEqual((snapshot["reported"], snapshot["pending"]), ("low", "medium"))

    def test_the_ladder_is_configurable(self):
        _, surface, _ = make({"effort": {"levels": ["cheap", "spendy"]}})
        self.assertEqual(surface.effort_levels, ["cheap", "spendy"])
        surface.nudge_effort(+1)
        self.assertEqual(surface.snapshot()["effort"]["pending"], "spendy")

    def test_an_empty_ladder_fails_rather_than_dividing_by_zero(self):
        _, surface, _ = make({"effort": {"levels": []}})
        self.assertEqual(surface.effort_levels, list(ag.EFFORT_LEVELS))


class TestFocus(unittest.TestCase):
    def test_focus_needs_a_session(self):
        _, surface, _ = make({})
        result = surface.focus_selected()
        self.assertFalse(result)
        self.assertIn("no session", result.detail)

    def test_missing_focus_hook_names_the_fix(self):
        _, surface, _ = make({})
        surface.ingest(hook("SessionStart"))
        result = surface.focus_selected()
        self.assertFalse(result)
        self.assertIn(ag.FOCUS_HOOK, result.detail)


# --- dictation --------------------------------------------------------------

class _FakeRecorder:
    def __init__(self, stop_ok=True, start_raises=False):
        self.stop_ok = stop_ok
        self.start_raises = start_raises
        self.started: list[str] = []
        self.stopped = 0

    def available(self):
        return "/fake/ffmpeg"

    def start(self, path):
        if self.start_raises:
            raise OSError("no microphone")
        self.started.append(path)
        Path(path).write_bytes(b"RIFF")

    def stop(self):
        self.stopped += 1
        return self.stop_ok


class TestDictation(unittest.TestCase):
    def make(self, **kw):
        inserted: list[str] = []
        settings = kw.pop("settings", {"enabled": True})
        dictation = Dictation(settings, insert=lambda t: (inserted.append(t), True)[1],
                              recorder=kw.pop("recorder", _FakeRecorder()),
                              transcriber=kw.pop("transcriber", lambda p: " hello world \n"),
                              **kw)
        return dictation, inserted

    def test_hold_release_transcribes_and_inserts(self):
        dictation, inserted = self.make()
        self.assertTrue(dictation.start())
        self.assertEqual(dictation.state, Dictation.RECORDING)
        self.assertTrue(dictation.stop())
        self.assertEqual(inserted, ["hello world"])
        self.assertEqual(dictation.state, Dictation.OFF)

    def test_transcript_is_kept_for_inspection(self):
        dictation, _ = self.make()
        dictation.start()
        dictation.stop()
        self.assertEqual(dictation.last_text, "hello world")

    def test_starting_twice_is_refused(self):
        dictation, _ = self.make()
        dictation.start()
        result = dictation.start()
        self.assertFalse(result)
        self.assertIn("already", result.detail)

    def test_stopping_when_not_recording_is_refused_not_raised(self):
        dictation, _ = self.make()
        result = dictation.stop()
        self.assertFalse(result)
        self.assertIn("not recording", result.detail)

    def test_disabled_dictation_says_so(self):
        dictation, _ = self.make(settings={"enabled": False})
        self.assertFalse(dictation.preflight()["available"])
        result = dictation.start()
        self.assertFalse(result)
        self.assertIn("disabled", result.detail)

    def test_missing_whisper_degrades_with_an_actionable_reason(self):
        # No transcriber injected and the real binary is looked up against a path that
        # cannot exist, so this exercises the genuine "not installed" branch.
        dictation = Dictation({"enabled": True, "whisper_bin": "/nope/whisper-cli",
                               "model": "/nope/model.bin"},
                              recorder=_FakeRecorder())
        caps = dictation.preflight()
        self.assertFalse(caps["available"])
        self.assertIn("whisper", caps["reason"])
        result = dictation.start()
        self.assertFalse(result, "must degrade, not raise")
        self.assertEqual(dictation.state, Dictation.OFF)

    def test_missing_recorder_degrades_with_an_actionable_reason(self):
        dictation = Dictation({"enabled": True, "ffmpeg_bin": "definitely-not-ffmpeg"},
                              transcriber=lambda p: "x")
        caps = dictation.preflight()
        self.assertFalse(caps["available"])
        self.assertIn("definitely-not-ffmpeg", caps["reason"])
        self.assertIn("brew install ffmpeg", caps["reason"])
        result = dictation.start()
        self.assertFalse(result, "must degrade, not raise")
        self.assertEqual(dictation.state, Dictation.OFF)

    def test_binary_and_model_lookup_reject_paths_that_do_not_exist(self):
        self.assertIsNone(ag.find_whisper("/definitely/not/here"))
        self.assertIsNone(ag.find_model("/definitely/not/here.bin"))
        self.assertIsNone(ag.FfmpegRecorder(":0", "definitely-not-ffmpeg").available())

    def test_a_recorder_that_cannot_start_is_reported(self):
        dictation, _ = self.make(recorder=_FakeRecorder(start_raises=True))
        result = dictation.start()
        self.assertFalse(result)
        self.assertIn("failed to start", result.detail)
        self.assertEqual(dictation.state, Dictation.OFF)

    def test_a_recorder_that_cannot_finish_leaves_no_stuck_state(self):
        dictation, inserted = self.make(recorder=_FakeRecorder(stop_ok=False))
        dictation.start()
        result = dictation.stop()
        self.assertFalse(result)
        self.assertEqual(dictation.state, Dictation.OFF)
        self.assertEqual(inserted, [])

    def test_an_empty_transcript_is_not_inserted(self):
        dictation, inserted = self.make(transcriber=lambda p: "   ")
        dictation.start()
        dictation.stop()
        self.assertEqual(inserted, [])
        self.assertIn("empty", dictation.last_error)

    def test_a_transcriber_that_raises_does_not_escape(self):
        def explode(path):
            raise RuntimeError("model corrupt")

        dictation, inserted = self.make(transcriber=explode)
        dictation.start()
        dictation.stop()
        self.assertEqual(inserted, [])
        self.assertIn("transcription failed", dictation.last_error)
        self.assertEqual(dictation.state, Dictation.OFF)

    def test_a_runaway_recording_is_capped(self):
        clock = _Clock()
        dictation, _ = self.make(settings={"enabled": True, "max_seconds": 30}, clock=clock)
        dictation.start()
        self.assertFalse(dictation.over_limit(clock.t))
        clock.t += 31
        self.assertTrue(dictation.over_limit(clock.t))

    def test_the_surface_stops_a_runaway_recording_on_tick(self):
        dictation, inserted = self.make(settings={"enabled": True, "max_seconds": 5})
        clock = _Clock()
        dictation._clock = clock
        _, surface, _ = make({"dictate_key": 11}, clock=clock, dictation=dictation)
        dictation.start()
        clock.t += 6
        surface.tick(clock.t)
        self.assertEqual(dictation.state, Dictation.OFF)
        self.assertEqual(inserted, ["hello world"])

    def test_cancel_discards_without_transcribing(self):
        dictation, inserted = self.make()
        dictation.start()
        self.assertTrue(dictation.cancel())
        self.assertEqual(dictation.state, Dictation.OFF)
        self.assertEqual(inserted, [])

    def test_surface_tokens_drive_it(self):
        dictation, inserted = self.make()
        _, surface, _ = make({"dictate_key": 11}, dictation=dictation)
        self.assertTrue(surface.run_action("agent_dictate_start", Context()))
        self.assertTrue(surface.run_action("agent_dictate_stop", Context()))
        self.assertEqual(inserted, ["hello world"])

    def test_the_single_token_toggles(self):
        dictation, inserted = self.make()
        _, surface, _ = make({"dictate_key": 11}, dictation=dictation)
        surface.run_action("agent_dictate", Context())
        self.assertEqual(dictation.state, Dictation.RECORDING)
        surface.run_action("agent_dictate", Context())
        self.assertEqual(inserted, ["hello world"])


# --- reporting and degradation ---------------------------------------------

class TestSnapshot(unittest.TestCase):
    def test_no_status_source_reads_as_unknown_not_idle(self):
        _, surface, _ = make({"status_key": 3})
        snapshot = surface.snapshot()
        self.assertEqual(snapshot["status"], UNKNOWN)
        self.assertEqual(snapshot["source"], "none")
        self.assertFalse(snapshot["hooks_installed"])
        self.assertIsNone(snapshot["selected"])
        self.assertEqual(snapshot["sessions"], [])

    def test_a_report_flips_the_source_to_hooks(self):
        _, surface, _ = make({"status_key": 3})
        surface.ingest(hook("SessionStart"))
        snapshot = surface.snapshot()
        self.assertEqual(snapshot["source"], "hooks")
        self.assertTrue(snapshot["hooks_installed"])
        self.assertEqual(snapshot["reports"], 1)

    def test_snapshot_carries_the_led_decision(self):
        _, surface, _ = make({"status_key": 3})
        surface.ingest(hook("PermissionRequest"))
        led = surface.snapshot()["led"]
        self.assertEqual(led["color"], ag.LED_MAP[WAITING].color)
        self.assertEqual(led["behaviour"], "pulse")

    def test_process_probe_is_diagnostic_only(self):
        # It can say "something is running but not reporting". It must never set a status.
        daemon = _StubDaemon({"status_key": 3})
        surface = AgentSurface(daemon, clock=_Clock(), sender=_StubSender(),
                               probe=lambda: 3)
        snapshot = surface.snapshot()
        self.assertEqual(snapshot["claude_processes"], 3)
        self.assertEqual(snapshot["status"], UNKNOWN,
                         "a running process is not evidence of any particular state")

    def test_a_failing_probe_reports_none_rather_than_zero(self):
        def explode():
            raise OSError("ps missing")

        daemon = _StubDaemon({})
        surface = AgentSurface(daemon, clock=_Clock(), sender=_StubSender(), probe=explode)
        self.assertIsNone(surface.snapshot()["claude_processes"])

    def test_real_probe_returns_a_count_or_none(self):
        value = ag.claude_processes()
        self.assertTrue(value is None or isinstance(value, int))

    def test_snapshot_states_the_expiry_rules(self):
        _, surface, _ = make({"stale_after_s": 45})
        self.assertEqual(surface.snapshot()["expiry"]["stale_after_s"], 45.0)

    def test_snapshot_never_raises_without_an_agent_config(self):
        daemon = _StubDaemon(None)
        surface = AgentSurface(daemon, clock=_Clock(), sender=_StubSender(), probe=lambda: 0)
        self.assertEqual(surface.settings, {})
        self.assertTrue(surface.enabled, "absent config means defaults, not disabled")
        self.assertEqual(surface.snapshot()["status"], UNKNOWN)
        surface.tick(1000.0)

    def test_a_non_object_agent_key_is_ignored(self):
        daemon = _StubDaemon(None)
        daemon.cfg.doc["agent"] = "yes please"
        surface = AgentSurface(daemon, clock=_Clock(), sender=_StubSender(), probe=lambda: 0)
        self.assertEqual(surface.settings, {})

    def test_config_change_releases_painted_keys(self):
        daemon, surface, clock = make({"status_key": 3})
        surface.tick(clock.t)
        surface.d.cfg.doc["agent"] = {"status_key": 7}
        surface.config_changed()
        surface.tick(clock.t + 1)
        self.assertIn(7, daemon.renderer.flashed())

    def test_close_is_safe_to_call_twice(self):
        _, surface, _ = make({})
        surface.submit(lambda: None)
        surface.close()
        surface.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
