# Agent surface — the pad as a controller for a live Claude Code session

Use case 4 in [`VISION.md`](../VISION.md), Phase 7 in [`ROADMAP.md`](ROADMAP.md). Agent status
on the LEDs, session switching, push-to-dictate, approve/deny keys, and the dial as an effort
knob.

Implementation: `host/daemon/libremicro/agent_surface.py`.
Tests: `host/daemon/tests/test_agent_surface.py`.

This document leads with what is *actually* observable from outside a running Claude Code
session, because that question decides how much of the feature can exist at all. The honest
answer is: **more than expected for observing, almost nothing for controlling.**

---

## 1. What is actually possible

### Hooks are the status source. They are the only good one.

Claude Code's `settings.json` can run a command on any of ~31 named events, and each command
is handed a JSON object on **stdin**. Verified against
<https://code.claude.com/docs/en/hooks.md> (Claude Code 2.1.221).

Every payload carries these fields, on every event:

```json
{
  "session_id":      "abc123",
  "prompt_id":       "550e8400-…",
  "transcript_path": "/Users/you/.claude/projects/-Users-you-AI-libremicro/abc123.jsonl",
  "cwd":             "/Users/you/AI/libremicro",
  "permission_mode": "default|plan|acceptEdits|auto|dontAsk|bypassPermissions",
  "effort":          { "level": "low|medium|high|xhigh|max" },
  "hook_event_name": "PreToolUse"
}
```

That is a push feed of exactly the transitions we want, with a session identity, a project
path, and — usefully — **the session's current effort level on every single event**. It costs
no polling, it needs no private API, and it is entirely under the user's control.

The events that matter here, and what each one proves:

| Event | Matcher matches on | What it tells us |
|---|---|---|
| `SessionStart` | `startup`/`resume`/`clear`/`compact`/`fork` | a session exists, and it's your turn |
| `UserPromptSubmit` | *(none)* | a turn started |
| `PreToolUse` / `PostToolUse` | `tool_name` | a turn is in progress, and which tool |
| `PostToolUseFailure` | `tool_name` | a tool failed — routine, **not** a session error |
| `PermissionRequest` | `tool_name` | a permission decision is pending |
| `Notification` | `notification_type` | `permission_prompt`, `idle_prompt`, `agent_needs_input`, `agent_completed` |
| `Stop` | *(none)* | the turn finished; carries `last_assistant_message` |
| `StopFailure` | `rate_limit`, `overloaded`, `authentication_failed`, `billing_error`, … | the turn died at the API |
| `SessionEnd` | `clear`/`resume`/`logout`/`prompt_input_exit`/`other` | the session is gone |

**Waiting-for-approval — the state that matters most — is observable.** Two events cover it:

- `PermissionRequest` fires when a tool call needs a decision, *before* the prompt is drawn,
  and runs even headless. It is a **blocking, decision-capable** hook: it can auto-allow or
  auto-deny. That makes it the wrong tool for merely watching.
- `Notification` with `matcher: "permission_prompt"` fires for the same moment and **cannot
  block** — it is documented as fire-and-forget. That is the one to use.

So: use `Notification`. `PermissionRequest` is listed in the mapping table as well, because
some setups will want it, but the shipped configuration does not register it.

### What is *not* possible

- **No IPC, no API, no query interface.** Nothing outside the process can ask a running
  session for its state, list live sessions, switch between them, or change its model or
  effort. `/model` and `/effort` are in-session slash commands only. `claude --resume` and
  `claude -r` start *new* processes; they do not reach into an existing one.
- **The transcript JSONL is the wrong tool, and I rejected it.** It exists at
  `~/.claude/projects/<escaped-cwd>/<session-id>.jsonl`, and it is real (7.8 MB of it for the
  session that wrote this document). But: Anthropic documents the format as internal and
  subject to change between versions; mtime tells you "wrote recently", which is not the same
  as "busy"; and — decisively — **a session sitting on a permission prompt writes nothing at
  all.** The single most important state is invisible to a file watcher. Adding a tailer would
  mean more code, a parsing dependency on an unstable format, and worse answers than hooks.
- **Process inspection is a diagnostic, nothing more.** It is implemented
  (`claude_processes()`), it is cached, and it is deliberately barred from feeding the status
  model. Two findings shaped it. First, `pgrep -f claude` is unusable: it matches every helper
  process of the Claude *desktop* app and reports 8 hits with no CLI running at all. `ps -Ao
  comm` with an exact match on the command name finds precisely the one real CLI process.
  Second, even when it works, it can only answer "is a CLI running" — never per-session, never
  busy-versus-idle. Its entire job is to let the UI distinguish *"nothing is running"* from
  *"something is running but not reporting, so your hooks aren't installed"*.

### The consequence that shapes the whole design

Hooks tell you about **transitions**, never about **steady state**. Nothing fires while a turn
is merely in progress, and if a session is killed mid-turn, nothing fires at all — ever.

A surface that trusted the last thing it heard would show `working` for the rest of the day.
So every live status **expires**, and `unknown` is a real, visible, deliberately unconfident
state rather than an error. That rule is the difference between a status light and a
decoration.

---

## 2. The status model

Six states. Five carry information; the sixth admits it has none.

| Status | Means | Set by |
|---|---|---|
| `unknown` | no usable information | nothing has reported yet, or a report went stale |
| `idle` | alive, your turn | `SessionStart`, `Notification/idle_prompt`, `TeammateIdle` |
| `working` | a turn is in flight | `UserPromptSubmit`, `Pre`/`PostToolUse`, subagents, compaction |
| `waiting` | it wants something from you | `Notification/permission_prompt`, `agent_needs_input`, `PermissionRequest` |
| `error` | the turn died at the API | `StopFailure` |
| `done` | a turn just finished | `Stop`, `Notification/agent_completed` |

Two mapping decisions worth defending:

- **`PostToolUseFailure` is `working`, not `error`.** A grep that matched nothing or a test
  that went red is routine. Painting the pad red for it would make the state that means *"the
  session is broken"* meaningless within a minute.
- **An unrecognised event changes nothing.** Claude Code adds hook events; a payload we don't
  understand still proves the session is alive, so it refreshes the staleness clock — but it is
  never allowed to invent a state. A future release degrades to "no new information", not to a
  confident wrong colour.

### Expiry — three separate clocks, three separate jobs

| Knob | Default | Job |
|---|---|---|
| `stale_after_s` | 90 s | `working`/`waiting` with no report since → **`unknown`**. This is what catches a session killed mid-turn. |
| `done_hold_s` | 10 s | `done` → **`idle`**. "Finished" is a notification, not a state. |
| `session_ttl_s` | 1800 s | any session unheard-from → **forgotten entirely**. |

Staleness is measured from the **last report of any kind**, not from when the status was set: a
busy session keeps emitting tool calls, so *silence* — not an old label — is what proves it is
gone. `idle` deliberately does **not** expire at `stale_after_s`: silence is exactly what idle
predicts, so silence is not evidence against it. Only `session_ttl_s` retires it.

The web UI still shows the raw report alongside the expired one (`reported_status` and
`stale: true`), so "it says working but we stopped believing it" is diagnosable.

### LED mapping

Colour says **what**. Behaviour and rate say **how urgently**. That split is the design:

> A solid key is information you can ignore. A pulse is a request. Rate ranks the requests.

| Status | Colour | Behaviour | Period | Why |
|---|---|---|---|---|
| `unknown` | `141414` dim grey | solid | — | Reads as "no signal". Deliberately **not off** — an unlit key is indistinguishable from a dead daemon. |
| `idle` | `0d2a44` dim blue | solid | — | Present, unremarkable, ignorable. |
| `working` | `ff8c00` amber | pulse | 1.8 s | Busy is not urgent. It must not compete for attention. |
| `waiting` | `ff2fd0` magenta | pulse | **0.45 s** | The one that has to grab you: 4× the rate of `working`. Magenta because no other state uses that hue, so it survives red/green colourblindness. |
| `error` | `ff2200` red | pulse | 1.0 s | Worth your attention, but nothing waits on your keypress. |
| `done` | `00e676` green | solid | — | Nothing is required of you. The information is "it finished". |

**`waiting` gets a second, stronger affordance: the approve and deny keys light up.** Nothing
else on a macropad communicates "act here" as directly as the two keys you are being asked to
press illuminating. If a *non-selected* session starts waiting, a separate `alert_key` pulses
instead, so a background session can shout without hijacking the display.

Overridable per status via `agent.colors`; behaviour and rate are not overridable, because
they are the part carrying the meaning.

**One implementation note.** `renderer.py` has no "hold this colour" call, and it should not
grow one — the base layer is config's job. A `flash` longer than the renderer's 0.35 s fade
window holds full colour, so a solid status is a long flash re-armed every 0.35 s
(rate-limited, so 30 fps does not mean 30 flashes). It self-clears within a second if the tick
loop ever stops, which is the right failure mode.

---

## 3. Setting it up

### Step 1 — the forwarding hook

Because the daemon accepts the hook payload **verbatim** (it reads `hook_event_name` itself),
the hook is a one-liner with no `jq` and no parsing:

```bash
mkdir -p ~/.claude/hooks
cat > ~/.claude/hooks/libremicro-status.sh <<'EOF'
#!/bin/sh
# Forward the hook payload to the LibreMicro daemon. Fire and forget.
curl -sS -o /dev/null --max-time 2 \
     -X POST -H 'Content-Type: application/json' --data-binary @- \
     http://127.0.0.1:8777/api/agent/status 2>/dev/null
exit 0
EOF
chmod +x ~/.claude/hooks/libremicro-status.sh
```

Three details that are not optional:

- **`-o /dev/null`.** For `SessionStart` and `UserPromptSubmit`, a hook's stdout is *added to
  Claude's context*. Letting curl print the JSON response would inject daemon output into
  every session.
- **`exit 0`, always.** Exit code 2 is a *blocking* error. A status hook must never be able to
  block a tool call because the macropad daemon isn't running.
- **`--max-time 2`.** A hung socket must not stall a turn.

### Step 2 — register it

In `~/.claude/settings.json` (or `.claude/settings.local.json` for one project — hooks are
honoured in both):

```json
{
  "hooks": {
    "SessionStart":      [{ "hooks": [{ "type": "command", "command": "~/.claude/hooks/libremicro-status.sh", "async": true, "timeout": 5 }] }],
    "UserPromptSubmit":  [{ "hooks": [{ "type": "command", "command": "~/.claude/hooks/libremicro-status.sh", "async": true, "timeout": 5 }] }],
    "PreToolUse":        [{ "matcher": "*", "hooks": [{ "type": "command", "command": "~/.claude/hooks/libremicro-status.sh", "async": true, "timeout": 5 }] }],
    "Notification":      [{ "matcher": "*", "hooks": [{ "type": "command", "command": "~/.claude/hooks/libremicro-status.sh", "async": true, "timeout": 5 }] }],
    "Stop":              [{ "hooks": [{ "type": "command", "command": "~/.claude/hooks/libremicro-status.sh", "async": true, "timeout": 5 }] }],
    "StopFailure":       [{ "hooks": [{ "type": "command", "command": "~/.claude/hooks/libremicro-status.sh", "async": true, "timeout": 5 }] }],
    "SessionEnd":        [{ "hooks": [{ "type": "command", "command": "~/.claude/hooks/libremicro-status.sh", "async": true, "timeout": 5 }] }]
  }
}
```

`async: true` runs the hook in the background so it cannot contribute latency.

`PostToolUse` is deliberately **omitted**: `PreToolUse` already establishes `working` and
`Stop` ends it, so adding `PostToolUse` doubles the number of curls per turn for no new state.
Add it only if you want the LED detail text to track tool completion.

### Step 3 — check it is arriving

```
curl -s localhost:8777/api/status | python3 -m json.tool | grep -A3 '"agent"'
```

`source` goes from `"none"` to `"hooks"` on the first report. If it stays `"none"` while
`claude_processes` is non-zero, the hook is registered wrong — that is exactly the case the
process probe exists to name.

### Step 4 — bind some keys

```json
{
  "agent": {
    "status_key": 3,
    "alert_key": 7,
    "approve_key": 9,
    "deny_key": 10,
    "dictate_key": 11,
    "effort_keys": [0, 1, 2, 5, 6]
  },
  "profiles": {
    "default": {
      "keys": [
        { "index": 9,  "label": "Approve", "on": { "press": { "action": "agent_approve" } } },
        { "index": 10, "label": "Deny",    "on": { "press": { "action": "agent_deny" } } },
        { "index": 8,  "label": "Session", "on": { "press":  { "action": "agent_session_next" },
                                                   "double": { "action": "agent_session_focus" } } },
        { "index": 11, "label": "Dictate", "on": { "hold":    { "action": "agent_dictate_start" },
                                                   "release": { "action": "agent_dictate_stop" } } }
      ],
      "encoder": {
        "cw":    { "action": "agent_effort_up" },
        "ccw":   { "action": "agent_effort_down" },
        "press": { "action": "agent_effort_apply" }
      }
    }
  }
}
```

> **The `agent` key is not in `host/config/schema.json` yet.** Until it is folded in,
> `libremicro --validate` will report `(root): Additional properties are not allowed
> ('agent' was unexpected)` and the web UI's config save will refuse the document. The daemon
> itself reads the key defensively and runs fine without it. See §7 for the schema surface
> required.

---

## 4. Controls

### Approve / deny

`agent_approve` and `agent_deny` synthesise a keystroke through `keys.py` — `return` and
`escape` by default, which accept the highlighted "Yes" and dismiss the prompt respectively.
Configurable as `agent.approve.shortcut` / `agent.deny.shortcut` (use `"2"` / `"3"` if you
prefer selecting numbered options directly), and `agent.deny.text` types a reason before
submitting.

**There is no way to address a keystroke *to* a session.** It goes to whatever is frontmost.
An unguarded approve key is therefore an Enter key that fires into whatever you happen to be
looking at — a genuinely bad thing to hand someone. So it is guarded on the one fact we
actually have: **the session told us it is waiting.** `require_waiting` defaults to `true`;
turning it off is a deliberate choice, and the failure mode is silent, which is why the default
is the safe one.

The residual risk is real and worth stating: if the session is waiting but you have alt-tabbed
away, the keystroke lands in the wrong window. `focus_first: true` mitigates it by running the
focus hook, waiting `focus_delay_ms`, then sending — on the background worker, so nothing
blocks the input path.

### Session switching

The daemon knows every session that has ever reported: id, project, status, effort, age. So
*selection* — which session the LEDs show and the approve keys act on — is completely real.

Auto-follow is the default, and it ranks **any waiting session first**, then most recent. The
session that needs you wins the display. `agent_session_next`/`prev` pin a specific session
instead, and the cycle **includes an "auto" slot**, because a pin you cannot undo from the pad
is a trap.

*Focusing* a session's terminal is the part that is not portable. tmux, iTerm2, Terminal.app
and Ghostty each need a different incantation, and only a hook running *inside* the session can
know which it is. So `agent_session_focus` runs a user-supplied executable at
`~/.config/libremicro/hooks/agent_focus` — the same escape hatch `actions.py` uses for desk
height — with the session's details in the environment:

```
LM_AGENT_SESSION, LM_AGENT_LABEL, LM_AGENT_CWD, LM_AGENT_STATUS, LM_AGENT_TRANSCRIPT
LM_AGENT_TERM_*   (whatever the status hook reported under `terminal`)
```

To populate those hints, extend the status hook to add the terminal identifiers it inherits
from the session's own process:

```sh
python3 -c 'import json,os,sys
d=json.load(sys.stdin)
d["terminal"]={k:v for k,v in {
  "tmux": os.environ.get("TMUX_PANE",""),
  "iterm": os.environ.get("ITERM_SESSION_ID",""),
  "term_session": os.environ.get("TERM_SESSION_ID",""),
  "wezterm": os.environ.get("WEZTERM_PANE","")}.items() if v}
json.dump(d,sys.stdout)' | curl -sS -o /dev/null --max-time 2 -X POST \
  -H 'Content-Type: application/json' --data-binary @- \
  http://127.0.0.1:8777/api/agent/status
```

With no hook installed, `agent_session_focus` fails with a message naming the file to create.
It does not guess.

### The effort knob

This is the control where the honest answer is the interesting one.

**Reading effort is real.** Every hook payload carries `effort.level`, so the pad always knows
what the session is actually running at.

**Writing it has no API.** `/effort` is an in-session slash command and nothing outside the
process can invoke it. There are exactly two options, and both are named for what they are:

- `effort.apply: "slash_command"` (default) — **types** `/effort <level>` and Enter into the
  focused session. It genuinely works, and it genuinely requires the session focused with an
  empty prompt. That constraint is stated rather than hidden.
- `effort.apply: "none"` — the dial becomes a **read-only display** of what the session
  reports. For anyone who would rather have no action than a fragile one.

Writing `effortLevel` in `settings.json` was considered and rejected: it is documented as
affecting sessions at startup, and I have no evidence it applies to a live one. Shipping it
would be exactly the kind of confident-looking wrong behaviour this design is trying to avoid.

**The dial selects; the press commits.** Two reasons. A dial gets spun, so applying per detent
would fire five slash commands to get from `low` to `max`. And the selection needs to be
*visible* before it is acted on:

```
effort_keys:  [ low ][ med ][ high ][ xhigh ][ max ]
reported=high, pending=xhigh:
              [solid][solid][solid][ PULSE ][ off ]
                blue   blue   blue   yellow
```

Rungs at or below the level sit solid blue. A pending-but-unapplied rung pulses yellow, and it
**keeps pulsing until the session reports that level back** — so if the slash command didn't
land (wrong window, non-empty prompt), the drift stays visible instead of the pad pretending it
worked.

### Push-to-dictate

Verified present on this machine, so this is a real integration rather than a design sketch:

```
/opt/homebrew/bin/whisper-cli                  (brew whisper-cpp)
~/.cache/whisper-cpp/ggml-small.en.bin  465 MB
~/.cache/whisper-cpp/ggml-base.en.bin   141 MB
/opt/homebrew/bin/ffmpeg                       (with avfoundation)
```

Hold to record → release to transcribe → text is inserted at the cursor. `ffmpeg -f
avfoundation` records 16 kHz mono WAV (what whisper.cpp wants), then `whisper-cli -nt -otxt`
transcribes it, then `keys.send_text` types the result. `ggml-small.en.bin` is preferred over
`base` when both exist.

Bind it to **`hold` and `release`**, not `press`: the recogniser fires `press` on key *release*,
so `hold` is the only trigger that fires while the key is still down. A useful side effect is
that a brief accidental tap cannot start a recording. `agent_dictate` is a single-key toggle
for anyone who prefers that.

Details that matter:

- **`ffmpeg` is stopped with `SIGINT`, not `SIGKILL`** — that makes it finalise the WAV header.
  A killed ffmpeg leaves a truncated file that whisper reads as silence.
- **`max_seconds` (default 60) caps a runaway recording**, enforced on the render tick. A key
  that got stuck down must not fill the disk.
- **Nothing blocks.** `stop()` returns as soon as the recorder is asked to finish; whisper and
  the insertion run on the background worker.
- **First run will trigger the macOS microphone permission prompt** for whichever process hosts
  the daemon. If it is denied, ffmpeg fails and the surface reports it.
- **Every missing piece is named, not lumped together.** `preflight()` reports
  `whisper-cli not found (brew install whisper-cpp)` or `no whisper model in
  ~/.cache/whisper-cpp` — never a bare "dictation unavailable" that sends someone hunting.
  With anything missing, the key flashes red and the reason reaches the log once.

---

## 5. The ingest payload

`POST /api/agent/status`. The straightforward case is **the hook payload, unmodified**:

```json
{ "session_id": "abc123", "hook_event_name": "Notification",
  "notification_type": "permission_prompt", "cwd": "/Users/you/AI/libremicro",
  "effort": { "level": "high" }, "tool_name": "Bash" }
```

Recognised fields: `session_id` (**required**), `hook_event_name` or `event`, `cwd`,
`transcript_path`, `permission_mode`, `effort` (object or bare string), `tool_name`,
`notification_type`, `message`, `source`, `reason`, `error_type`, `last_assistant_message`,
`agent_type`, plus two LibreMicro extras: `label` (overrides the display name, which otherwise
comes from the `cwd` basename) and `terminal` (a flat object of focus hints).

For a non-Claude-Code harness, say the state outright and skip the mapping table:

```json
{ "session_id": "codex-1", "status": "waiting", "detail": "approve patch?" }
```

Response: `{"ok": true, "session_id": …, "status": …, "selected": bool, "sessions": n}`.
A payload that is not an object, or has no `session_id`, returns `ok: false` with a reason. An
event name we don't recognise returns `ok: true` and changes no status.

---

## 6. What is real, and what is waiting on something

**Real and tested, no session required:** the status model and its full mapping table; all
three expiry clocks; the LED mapping and how it reaches the renderer; the session registry,
auto-follow ordering, waiting-priority and pinning; the approve/deny guard; the effort ladder
including drift display; dictation end-to-end with fakes; and honest degradation everywhere —
no status source, no `agent` config at all, no key-synthesis helper, no whisper, no ffmpeg, no
focus hook, a sender that raises, a recorder that won't start, a transcriber that explodes.
115 tests.

**Real but only exercisable with hardware or a live session:** the hook → curl → `ingest` path
(the payload contract is verified against the docs, but nothing has POSTed to a running daemon
yet); the actual `ffmpeg`/`whisper-cli` invocations, which run against fakes in tests; and
every keystroke that leaves `keys.py`, which needs the `lmkey` helper built and Accessibility
granted. And of course the pad cannot yet *send* key presses at all — that is Phase 2.

**Scaffolding, deliberately:** `agent_session_focus` is a dispatcher for a hook that does not
exist until the user writes it. The `terminal` hints it depends on are only populated if the
user upgrades the status hook. This is the honest shape of the problem, not an unfinished
feature.

**Concluded not feasible:** querying a live session's state on demand; switching the *active*
session from outside; setting a live session's model or effort programmatically; and deriving
status from the transcript on disk (see §1).

**One caveat specific to this machine.** `~/.claude/settings.json` currently has
`permissions.defaultMode: "auto"` and `skipAutoPermissionPrompt: true`. Those suppress most
permission prompts — which means `waiting`, the headline state, will fire rarely. Worth knowing
before concluding the hook is broken.

---

## 7. Schema surface required

`host/config/schema.json` has `additionalProperties: false` at the root, so this needs folding
in before the config validates. All of it is optional with working defaults.

New top-level property `agent` → `#/$defs/agent`:

```
agent (object, additionalProperties: false)
  enabled            boolean, default true
  harness            string, default "claude-code"
  status_key         integer 0-12          which key shows the selected session's status
  alert_key          integer 0-12          pulses when a NON-selected session is waiting
  approve_key        integer 0-12          lit while waiting (display only; bind separately)
  deny_key           integer 0-12          lit while waiting (display only; bind separately)
  dictate_key        integer 0-12          shows recording / transcribing
  effort_keys        array of integer 0-12, maxItems 5   the effort ladder as a bar
  stale_after_s      number, min 1,  default 90     working/waiting -> unknown
  session_ttl_s      number, min 1,  default 1800   forget the session entirely
  done_hold_s        number, min 0,  default 10     done -> idle
  follow_active      boolean, default true
  prioritise_waiting boolean, default true          a waiting session wins the display
  require_waiting    boolean, default true          approve/deny only while status == waiting
  focus_first        boolean, default false         run the focus hook before sending keys
  focus_delay_ms     integer, min 0, default 250
  approve            { shortcut: string, text: string }   default shortcut "return"
  deny               { shortcut: string, text: string }   default shortcut "escape"
  effort             { levels: array of string,
                       apply: enum ["slash_command","none"], default "slash_command" }
  colors             { unknown|idle|working|waiting|error|done: $ref #/$defs/color }
  dictation          { enabled: boolean default true,
                       whisper_bin: string default "auto",
                       ffmpeg_bin: string default "ffmpeg",
                       model: string default "auto",
                       audio_device: string default ":0",
                       language: string default "en",
                       threads: integer min 1 default 4,
                       max_seconds: number min 0 default 60 }
```

And eleven tokens appended to the `binding.action` enum:

```
agent_approve, agent_deny,
agent_session_next, agent_session_prev, agent_session_focus,
agent_effort_up, agent_effort_down, agent_effort_apply,
agent_dictate_start, agent_dictate_stop, agent_dictate
```

Note `approve_key`/`deny_key` are **display** roles — they say which keys to illuminate while
waiting. Binding the action is separate, under `profiles.*.keys[].on`, so a user can light one
key and act from another if they want.
