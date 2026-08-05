# Roadmap

Sequenced delivery plan for the scope in [`VISION.md`](../VISION.md). Phases are ordered so that
each one ends with something demonstrable, and so the hardware-blocked work sits behind work that
isn't blocked.

> **Continuing this work?** [`PLAN.md`](PLAN.md) is the handoff: open items in priority
> order, the operational traps that cost real debugging time, the calibration constants
> that could only be measured on hardware, and decisions still open.

## Where we actually are

**The pad works.** Custom firmware v2 is flashed and running on hardware. Keys, the rotary
encoder, the touch pad, the joystick, per-key and underglow RGB, and battery reporting are all
live and confirmed on the device, driven by a host daemon with a local web UI.

Confirmed on hardware, not just written:

| | |
|---|---|
| LED strip mapping | serpentine from the bottom-right; shipped as a source default |
| Key matrix | 13 keys reporting logical indices |
| Encoder | quadrature matched to the vendor's own decoder, direction calibrated |
| Touch pad | GPIO 14, both edges |
| Joystick | analog on ADC1, eight bindable directions, orientation calibrated |
| Battery | MAX77972 over I²C — and it settled that SCL is 18, not 9 |
| Bindings | app launch, keyboard chords, text, scripts, modes, profiles |

What is **not** yet confirmed on hardware: the rear button (deliberately off — see the GPIO 2
hazard below), notification watchers, and the agent control surface. Those are built and tested
but have not been exercised against a live session or a real unread count.

### The rear button, and why it stays off

Its pin is confirmed (GPIO 2). The problem is that stock arms a ULP-RISCV watcher on that pin
which forces a hardware reset when it goes low — and `scripts/enter_bootloader.sh` is what arms
it, so it is probably live on any pad that has just been flashed. With it running, pressing rear
reboots the device. `LM_ENABLE_REAR` is therefore opt-in, and enabling it means either clearing
`RTC_CNTL_ULP_CP_SLP_TIMER_EN` or power-cycling without going through that RPC first.

### Calibration constants that had to be measured

Four things could only be settled by testing on the physical device, and each is a single
constant so a different unit can be corrected in one place:

- `LM_ENC_INVERT` — which rotation is clockwise depends on PCB wiring.
- `LM_JOY_INVERT_X` — pushing up read as `n` correctly but right read as `w`, so the X axis is
  mirrored. North being right is what ruled out a rotation.
- `LM_JOY_REST_X/Y` = 1928, not the 2047.5 stock hard-codes, with asymmetric travel either side.
- `LM_TOUCH_ACTIVE_HIGH` — the vendor ISR passed the level uninverted, unlike its siblings.

## Phase 0 — Foundations ✅ **built**

Host-side skeleton and the config contract everything else is written against.
Lives in `host/daemon/` — 417 tests across the whole daemon now, none needing a device. See its
README for what's in each module.

- Config **schema v2**: bindings (launch / shortcut / script / mode / built-in), layout geometry,
  palettes, effects, profiles, power settings, export/import bundle shape.
- Daemon core: serial transport, config load + schema validation, LED frame renderer with
  compositing (base layer → effect layer → transient flashes), palette/effect engine.
- Palette corpus in a **WLED-compatible JSON stop format** so external palettes import cleanly.
- Keep `lmctl.py` working as the low-level escape hatch.

**Done when:** the daemon starts, validates a config, and renders a palette-driven animated
effect across keys and underglow at a stable frame rate, with no input events required. ✅

One finding worth carrying forward: even with frame diffing, a full-pad animated gradient at
30 fps measures 61% of the 115200-baud link. Add the batched `kf`/`uf` frame commands
(`PROTOCOL.md`) alongside the Phase 2 firmware work rather than after it.

## Phase 1 — Lighting studio: web UI v1 ✅ **built**

The first thing a user can actually *use*, and it needs no firmware change.

- Layout-accurate device view: key rows of **2/4/4/3** and the **3×3-minus-centre** underglow.
- Click a key or underglow cell to set its colour; palette and effect designer with **live
  preview on the physical pad** while dragging.
- **Identify sweep** that lights each LED in turn so the mapping can be confirmed by eye. This
  did its job: the result is now the shipped default (see [`HARDWARE.md`](HARDWARE.md)), so the
  sweep is for confirming or correcting a unit rather than a required setup step.
- Export and import a full configuration bundle.

**Done when:** someone can open a local URL, design a lighting setup by clicking, watch it apply
to the device in real time, export it, and re-import it on a fresh machine. ✅

## Phase 2 — Input events: firmware v2 ✅ **flashed and confirmed on hardware**

The code exists and compiles clean (`firmware/src/main.c`): key matrix scanning, `key <i>
down`/`up` events carrying **logical** indices, and the batched `kf`/`uf` frame commands.
Encoder, touch pad and rear button are behind `LM_ENABLE_UNVERIFIED_INPUTS`, off by default —
their pins *are* now confirmed, but stock's ULP rescue watcher on GPIO 2 can reset the device on
a rear press and our own flashing procedure is what arms it, so enabling that block is a
deliberate second step rather than the default.

Flashed. LEDs still light, keys report correctly, and the matrix orientation came out right
first time — so `MTX_TO_LOGICAL`, generated from `layout.py` rather than hand-derived, needed no
correction. The encoder, touch pad and joystick are live too; only the rear button is held back.

This was the gate, and both of its prerequisites are now done:

1. ~~**Re-verify the provisional pins.**~~ ✅ Resolved by static analysis of stock v0.6.1 — see
   [`PIN-VERIFICATION.md`](PIN-VERIFICATION.md). The headline: touch and rear were *swapped* in
   the old table, which is the whole reason GPIO 2 appeared to be cited twice. There was no
   conflict.
2. ~~**Emit input events**~~ ✅ Implemented, per the grammar in
   [`PROTOCOL.md`](PROTOCOL.md).

**Done when:** every physical input produces exactly one correct event line, with no ghosting from
matrix scanning and no missed encoder detents during fast rotation, while LED commands continue to
work on the same connection. ✅

## Phase 3 — Bindings: launcher, shortcuts, scripts ✅ **built**

Use case 1, plus the two binding types added to the vision.
Lives in `events.py` (trigger recognition), `actions.py` (execution) and `dispatch.py`
(resolution). Verified end-to-end against the live daemon via `POST /api/simulate`, which
injects an event as though the pad had sent it — necessary because Phase 2 isn't flashed, and
useful permanently for testing and demos.

- **Launch** bindings (`open -a`, profile-specific `open -na … --args`).
- **Keyboard shortcut** synthesis: chords, media keys, and text insertion.
- **Script triggers**: shell / script file / AppleScript, with trigger context passed as
  environment variables, run without blocking the event loop, able to write back to the pad's LEDs.
- Press vs. release, `hold`, and `double` as distinct trigger kinds.

**Done when:** pressing a key launches its app, fires its chord in a real application, or runs its
script with correct context — and a slow script cannot stall the pad.

## Phase 4 — Modes and the encoder ✅ **built**

Use case 2. Mode key press → confirmation flash → encoder rebinds; media mode drives volume,
desk mode drives desk height. Optional whole-pad recolour per mode.

**Done when:** the same dial does different jobs depending on the active mode, the active mode is
visible on the LEDs, and mode state survives a daemon restart. ✅

Two behaviours worth knowing, both decided while building: a mode key **toggles** (pressing it
again leaves the mode, so a disabled timeout can't strand you), and encoder activity **extends**
a timed mode rather than letting it expire mid-adjustment.

## Phase 5 — Web UI v2: bindings, profiles, battery

Now that bindings exist, the editor covers them.

- Binding editor per trigger, with a **shortcut recorder** (press the chord instead of typing it).
- Profile management: create, switch, cycle, and optional auto-switch on frontmost app.
- Battery level and charge state display (needs Phase 8 for the device-side read; until then the
  UI can ship the panel disabled).

**Done when:** the whole config — bindings included — is editable without touching JSON, and
profiles can be switched from both the UI and the pad.

## Phase 6 — Notification watchers ✅ **built**

Use case 3. Background pollers that pulse a key on external state; Slack unread is the headline.
Watchers are pluggable so new sources don't touch the core.

**Done when:** the Slack key pulses on unread and stops when read, without the poller affecting
input latency. ✅

One design point that outranks the feature: a reading is either a value or explicitly *unknown*,
and unknown never collapses to zero. A pulsing key is a claim about unread mail, and making that
claim while we don't know is worse than a dark key.

## Phase 7 — Agentic-coding control surface ✅ **built** (see [`AGENT-SURFACE.md`](AGENT-SURFACE.md))

Use case 4. Agent status on the LEDs, session switching, push-to-dictate, approve/deny keys,
encoder as an effort knob, targeting Claude Code as the first harness.

**Done when:** a live coding session's state is legible on the pad and the pad can act on it. ✅

Driven by Claude Code hooks rather than polling, which is what makes *waiting-for-approval*
observable at all. Watching the session transcript was investigated and rejected: a session
sitting on a permission prompt writes nothing, so the state that matters most is invisible to it.

## Phase 8 — Power, sleep and battery (firmware) 🟡 **battery confirmed on hardware; sleep still to do**

- Deliberate **power off** via long-press that survives unplugging: latch pads into the RTC
  domain, deep-sleep, wake on the same button — and **release holds at boot**, the exact step whose
  absence made early custom firmware boot dark.
- **Staged idle saver**: dim → LEDs off → deep sleep, any input wakes; user-configurable timeouts;
  different thresholds on battery vs. USB.
- **Battery reporting** from the MAX77972 over I²C, surfaced to the host.

Enforcement is on-device so it works untethered. Pull this earlier if running the pad on battery
becomes annoying in daily use — it's independent of Phases 3–7.

**Done when:** the pad powers off and back on cleanly, sleeps on idle and wakes on input, and
reports battery state to the host.

Battery reporting is live on hardware — the gauge acked on the first flash and reports a
plausible level, voltage and charge state. Implementation: `batt <pct> <0|1>` on change, plus a `batt` command
that dumps the raw gauge registers. Read-only, never writes a register, never touches
charge-enable. The MAX77972 has no public datasheet, so the register map was decoded from stock
(see [`HARDWARE.md`](HARDWARE.md)) — and that turned up a likely error in this repo's own notes:
I²C SCL is 18, not 9. Since SCL is an output and the two disagree, the firmware probes both
rather than picking, and the boot log says which answered.

Power off, staged idle sleep and wake are still to do, and they are gated on the GPIO 2 hazards
in `HARDWARE.md` — the ULP rescue watcher and stock's wait-for-release guard before arming ext0.

## Phase 9 — Bluetooth: pairing and BLE HID standalone

Pairing flow with clear LED feedback, and a static on-device keymap so the pad works as a plain
keyboard against a phone or a machine with no daemon. The largest firmware lift in the project,
deliberately last: the tethered USB experience is where the interesting product lives.

**Done when:** the pad pairs to a phone and sends keystrokes with no host software involved.

## Sequencing notes and risks

- **Phase 2 is the only true bottleneck.** If pin re-verification stalls, Phases 0, 1, and 8 all
  still progress; Phases 3–7 do not.
- **The rear-button / ext0-wake pin conflict** blocks both Phase 2 and Phase 8. Resolve it once,
  early — it's cheap now and expensive later.
- ~~**LED index mapping is unverified.**~~ **Resolved.** Both chains were confirmed on hardware
  by identify sweep: a serpentine starting bottom-right (see `HARDWARE.md`). Because the wiring
  is identical on every Creator Micro 2, it now ships as a source default rather than per-user
  config, so spatial effects are correct out of the box. Config override remains for a future
  hardware revision.
- **Reflashing has real failure modes.** Firmware phases (2, 8, 9) each carry boot-loop risk; keep
  [`RECOVERY.md`](RECOVERY.md) current and take a flash backup before each firmware milestone.
- **Don't build the feature matrix.** Matching Work Louder Input feature-for-feature is the
  outcome, not the method. Ship the four original use cases well first.

## Phase 10 — Feedback on the pad itself

Started, and worth naming as its own phase because it changes what the device is for.

macOS shows no on-screen overlay when volume is set programmatically, and the media key that
does show one only moves in ~6.25% jumps — so smooth volume and visible volume looked mutually
exclusive. They are not: the pad is under the user's hand and the underglow ring reads as a
scale. `Renderer.bar()` now shows volume there, with a partially-lit leading segment for
resolution finer than eight steps.

The general lesson is that this hardware is a *display*, not only an input. Obvious extensions:
battery level on demand, agent status (already mapped), mode indication, and a watcher summary.

**Done when:** any level or state the daemon knows about can be shown on the pad without a
screen, and the pad is the preferred place to show it.

## Phase 11 — On-screen cheat sheet ✅ **built**

Thirteen unlabelled keycaps is the pad's one real ergonomic problem, and it gets worse the more
you bind. `host/swift/lmhud.swift` draws a translucent panel showing the live binding for every
control; `cheatsheet.py` builds the labels. Bindable as `cheat_sheet` (toggle) or
`cheat_sheet_show` / `cheat_sheet_hide` for peek-while-held, and re-renders on mode and profile
changes.

Entirely host-side, so it needs no device and no firmware — it works while the link is parked or
the pad is running stock. Draggable, closable with its own ×, and its position is remembered in
`~/.cache/libremicro/hud-position.json` across the respawn a re-render costs.

**Done when:** you never have to open the web UI to remember what a key does. ✅

## Phase 12 — Per-key agent status: the "agent keys" idea 🔵 **filed, not started**

Lifted from OpenAI's Codex companion app for the Creator Micro, which splits the pad into two
banks and is worth stealing from wholesale because it solves a problem Phase 7 only half-solved.

**What they do.** The top two rows are *agent keys*: one key per agent session, each showing that
session's live state as a colour — white idle, blue thinking, green complete, amber requires
input, red error, unlit for no assigned agent. Single-tap focuses that session in the background;
double-tap brings its window front and centre. The bottom two rows are *command keys*: the
frequently-used actions, drawn with icons rather than text. Long-pressing the dial opens the
configuration page.

**Why it is better than what Phase 7 shipped.** The agent surface already maps agent status onto
the LEDs and can switch sessions, but it treats the pad as one status light for one session at a
time. Sessions get a *place* here: key 3 is always that session, so "which of my five agents
wants me" is answered by looking, not by cycling. That is the whole difference between a status
indicator and a dashboard, and this hardware has 13 addressable pixels sitting idle.

**What to build, in rough order.**

- A **session-to-key assignment** in config — sticky, so a session keeps its key across restarts,
  with the unassigned keys unlit rather than dark-because-broken.
- The **status palette** as a named palette in `palettes.py`, so the five states are one edit
  rather than five hex literals, and legible against the existing effect layers.
- **Single-tap focus without raising** versus **double-tap raise** — the recogniser already
  distinguishes `press` from `double`, and `AGENT-SURFACE.md` already notes what focusing costs.
- **Harness-agnostic status**, since Phase 7 deliberately targets Claude Code hooks first. Codex,
  and anything else with a hook or an event stream, should land as another source behind the same
  per-key state rather than a second code path.
- The **cheat sheet already knows how to draw a 4×4 grid with labels** (Phase 11). An agent-status
  mode for it — session names against their key positions, coloured by state — is close to free
  and covers the case where the pad is out of eyeline.

**Open questions.** Whether the split is fixed (two rows each) or a profile choice; whether an
amber *requires input* key should also pulse, given `Renderer.pulse` exists for watchers and a
solid colour in peripheral vision is easy to miss; and whether icons are worth it on 52 px tiles
in the cheat sheet when the pad's own keycaps are blank anyway.

**Done when:** five concurrent agents are legible at a glance, and the pad is where you go to see
which one is waiting on you.

## Phase 13 — Bindable skills, and the dial as a navigator 🔵 **filed, not started**

Also from the Codex companion app, and separate from Phase 12 because it is about *what you can
bind* and *how a control behaves*, not about status. Two screens' worth of ideas.

### 13a — A searchable action picker, with the harness's own skills in it

Their analog-stick sheet is one row per direction with its assigned action on the right, and the
assign field underneath is **"Search actions and skills"** — with `Skills` as its own section.
The actions are semantic rather than mechanical: *Toggle plan mode*, *Toggle sidebar*,
*Forward*, *Back*.

Two distinct things worth taking:

- **Skills as first-class bindable targets.** `agent_surface.py` can already *type* a slash
  command — that is exactly how `/effort` is applied (`effort.apply: "slash_command"`), so the
  mechanism exists and is proven. What does not exist is **discovery**: there is no way to ask a
  harness what skills it has and pick one from a list. Bindings today are hand-written
  `shortcut` / `text` / `action` tokens, which means binding a skill requires knowing its exact
  name and spelling it into a text field. A `skill` binding kind, populated from whatever the
  harness can enumerate, is the gap.
- **Named semantic actions rather than raw chords.** *Toggle plan mode* is more durable than
  `shift+tab`: the config says what it means, and the keystroke behind it can change with the
  harness. `agent.approve` / `agent.deny` already work this way (`$defs/agent_keystroke`, a
  `shortcut` or `text` pair), so this is extending an established pattern to plan mode, sidebar,
  and history navigation rather than inventing one.

The **web UI needs search before either lands**: there is currently no search or filter over
actions anywhere in the binding editor, and the `action` enum is already 25+ tokens. Adding a
skills list to a picker you can only scroll would make it worse, not better.

Note LibreMicro is *ahead* here on hardware: their sheet exposes four cardinal directions, and
`JOYSTICK.md` established eight bindable ones, all already wired. The gap is picker UX, not
capability.

### 13b — The encoder as a selection ring, and hold-to-configure

Their knob is not a value knob. It **moves a highlight through the composer's controls**, click
**opens or selects the highlighted one**, and **press-and-hold opens the configurator**. That is
a discrete spatial navigator, where every LibreMicro encoder binding so far is a continuous
scalar — volume, brightness, effort.

- A **`ring` binding kind** for the encoder: an ordered list of targets, with cw/ccw moving the
  selection and `press` firing it. Distinct from `mode`, which rebinds every control; this
  rebinds only the encoder and carries a *cursor*, which nothing in `dispatch.py` models today.
  It also wants somewhere to show the cursor — the underglow already does volume via
  `Renderer.bar()`, and the cheat sheet (Phase 11) could highlight the selected target.
- **Hold-the-dial-opens-settings** is worth stealing outright and is nearly free: bind
  `hold` on the encoder to a built-in that opens `http://127.0.0.1:8777`. A hardware route into
  the configurator means the pad is self-documenting for someone who has forgotten where the
  web UI lives.

**One thing to decide, not copy:** their mapping is *turn right → previous*, *turn left → next*.
That reads backwards against every list on the platform, and the encoder's rotation sense on this
board is already a known unknown (`PIN-VERIFICATION.md`: which direction is clockwise depends on
PCB wiring and is not determinable from firmware). Pick a direction deliberately, and make it
configurable rather than baked in.

**Done when:** a skill can be bound by searching for it instead of spelling it, and the dial can
step through a list and pick from it.

## Phase 14 — Multiaction keys: one key, four gestures 🔵 **filed, not started**

Their per-key editor, screenshotted at [`images/idea-multiaction-editor.png`](images/idea-multiaction-editor.png).
It is a spec for the panel Phase 5 still owes, and it names one gesture LibreMicro cannot express.

**What they do.** One key is a named unit — an icon tile, a name field ("My Multiaction"), a
delete link — and below it exactly four gesture rows: **On Tap**, **Double Tap**, **On Hold**,
**Tap + Hold**. Each row is a *Click to assign* dropdown with an × to clear it. The footer states
the timing as plain text: *tapping term is set to 250 ms*, *hold 1 sec to perform the assigned
function*.

**Where we already are.** `$defs/triggers` gives every control `press` / `release` / `hold` /
`double`, `events.py` recognises them from raw down/up, and `device.hold_ms` (450) and
`device.double_ms` (280) tune the terms — for the joystick's eight directions too. So three of
their four rows are already modelled end to end. Four things are missing:

- **`tap_hold` as a fifth trigger kind.** Tap, release inside the double window, press again and
  hold. `Recognizer` already keeps `_last_up`, `_down_at` and a `_double_fired` set, so the state
  it needs is nearly all present: the addition is that a hold which *begins* within `double_s` of
  a release resolves to `tap_hold` rather than `hold`, and that it must suppress the `double` it
  would otherwise have become as well as the deferred `press`. Worth writing the suppression
  table out before the code — five kinds off two edges is where a recogniser goes subtly wrong.
- **The editor itself.** Phase 5 is the one unbuilt UI phase; the web UI is still the Phase 1
  lighting studio, and there is no binding editor to add a gesture row to. This screen is the
  layout to build: gesture rows down one side, an assign picker per row, a clear ×, and the
  Phase 13a searchable action list behind the picker rather than a raw 25-token enum.
- **A key as a named, icon-bearing thing.** Config has `key.label` and `key.color` but no icon,
  and their tile makes the case for one: the cheat sheet (Phase 11) already draws per-key labels
  in a grid, so an `icon` field would serve the HUD and the editor from a single edit.
- **Per-control timing.** Theirs is global and merely *displayed*; ours is global too. Per-key
  overrides of `hold_ms` / `double_ms` are the honest improvement, because the cost is already
  documented in the schema and is real: a key with `double` bound cannot fire `press` until the
  window elapses. The editor should say that where the choice is made — *binding Double Tap adds
  280 ms to this key's tap* — which is exactly what their footer does not do.

**What to decide, not copy.** Their 250 ms / 1 s against our 280 ms / 450 ms: a 1-second hold is
a long time to lean on a key, and our defaults were picked for a pad you drum on, so don't inherit
theirs without trying both. And weigh whether **Tap + Hold** earns its complexity at all — four
gestures on a blank keycap is a memory tax, and the mitigation (the cheat sheet) has to be open to
help. Two gestures per key that you remember beat four that you don't.

**Done when:** a key's whole gesture set is editable in one panel, and `tap_hold` is a bindable
kind that the recogniser gets right on the first press rather than the second.

## Phase 15 — The power ladder: five stages, measured not guessed 🔵 **filed, not started**

Detail for Phase 8's one-line "staged idle saver", written out because the interesting part is
that **the hardware gives us more stages than the schema currently models**, and one of them is
nearly free. Phase 8 stays the firmware home for the work; this is the design.

The rule throughout: **enforced on-device**, because a pad on battery with no daemon attached is
exactly the case that needs the saving.

### Why the LED rail is the headline

Sending black frames is not the same as turning the LEDs off. Twenty-one WS2812-class pixels each
draw roughly a milliamp just to keep their controller alive with all three channels at zero — call
it ~20 mA of nothing, which on a pad this size is likely the largest single idle term, plausibly
larger than the SoC. And we can delete it outright: **the strips' supply is gated by GPIO 36**
(see [`HARDWARE.md`](HARDWARE.md) — active-high enable, and the thing whose absence made early
custom firmware boot dark). `power_rail_on()` already exists in `firmware/src/main.c`; the
missing half is `power_rail_off()` plus a re-init on the way back up.

So "LEDs off" should mean *rail down*, not *black*. That is the cheapest large win in the whole
phase and it needs no sleep mode at all.

### The stages

| | Stage | Mechanism | Wakes on | Cost of waking |
|---|---|---|---|---|
| 0 | active | full brightness, effects streaming | — | — |
| 1 | **dim** | brightness scaled to `dim_brightness` (40) | any input | none |
| 2 | **dark** | black frames **and GPIO 36 low** | any input | strip re-init + full resend, ~ms |
| 3 | **light sleep** *(new)* | `esp_light_sleep_start()`, RAM and peripherals retained | GPIO edge on any matrix column, touch, rear, encoder | sub-millisecond — the keypress that wakes can still be delivered |
| 4 | **deep sleep** | RTC domain only, RAM gone, boot from reset | `ext1` any-high across the four columns, plus rear / touch / encoder switch | full boot, ~200–300 ms to first pixel, and USB re-enumerates |
| 5 | **off** | rail low, pads latched into RTC, deep sleep | rear button only (`ext0`, GPIO 2) | full boot |

Stage 3 is the one the schema has no field for, and it is the one that makes the pad feel alive
while costing almost nothing: light sleep on the S3 is hundreds of microamps against tens of
milliamps awake, and it wakes fast enough that **the press that wakes it is the press that acts**.
The catch is that USB-Serial-JTAG does not survive it, so **stage 3 is battery-only** — on the
cable, stop at stage 2. `power.on_battery` already exists to express exactly that split.

### What the pins say about wake-on-any-key

The vendor manual (image below) promises *press it once to put it to sleep, then press any key to
wake it up*, and the pinout says we can match that:

- The four matrix **columns are GPIO 13, 5, 21, 1 — all four inside the S3's RTC range (0–21)**,
  so they can serve as `ext1` wake sources from deep sleep. Touch (14), rear (2), encoder switch
  (4) and even both encoder phases (12, 11) are RTC-capable too.
- The **rows (46, 17, 40, 47) are mostly not**, so a press only pulls a column high if the rows
  stay driven while the SoC is down. That is what `gpio_deep_sleep_hold_en()` is for — and stock
  calls it at power-off, which is decent evidence the latch really does hold the matrix up rather
  than merely preserving pad state. Whether a held row sources enough current to be read is the
  one thing to measure. **Fallback if it doesn't:** deep sleep wakes on rear / touch / encoder
  only, and any-key wake lives at stage 3.

### Two things that must not be got wrong

- **The waking press must not fire its binding.** Same discipline as the `tap_hold` suppression
  table in Phase 14: whichever edge wakes the device is consumed, and the recogniser starts clean.
  A wake that also launches an app is worse than a wake that needs a second press.
- **Deep sleep on USB is an unplug event to the host.** The device re-enumerates, so
  `transport.py` has to treat it as the reconnect it already knows how to do, and the daemon must
  not spam a dead port meanwhile. This is the integration risk in the phase, not the firmware.

### Stop guessing: the gauge can measure it

Every milliamp above is an estimate, and we do not have to leave it that way. The MAX77972 map
already decoded in [`HARDWARE.md`](HARDWARE.md) includes **`0x1C` Current and `0x1D` AvgCurrent at
0.15625 mA/LSB**, plus `FullCapRep` and `Cycles`. So a `power measure` debug command that parks
the pad in each stage and logs AvgCurrent turns this table into real numbers, and the same reading
gives the user an honest **"about 14 hours left"** instead of a percentage. Do the measurement
first — it decides whether stage 3 is worth the complexity, and it is the difference between
tuning defaults and inventing them.

Smaller items in the same budget: read **USB detect on GPIO 42** (known pin, nothing reads it yet)
so the ladder picks itself; cap `fps` and prefer static effects on battery, since a full-pad 30 fps
gradient already measures 61% of the serial link and the radio is not even the expensive part
here; and honour `on_battery.brightness` as a hard ceiling rather than a default.

**Done when:** the pad idles for days on battery, wakes on a keypress fast enough that you don't
notice it was asleep, and `docs/` carries a measured current figure for every stage.

## Phase 16 — Light shows: boot, wake, sleep, and the hold-to-off ring 🔵 **filed, not started**

Phase 10's point was that this hardware is a *display*. The moment that matters most is the one
where it currently says nothing: switch-on. Twenty-one addressable pixels and three PWM status
LEDs, and the pad comes up silently.

**A boot show is free in the way that matters.** Boot already costs ~200–300 ms before the first
pixel could light; an animation over that window makes it read as *deliberate* rather than *slow*,
and it must live in **firmware**, not the daemon — on battery there is no host, and the show
should be the thing that proves the pad woke up at all. Sequencing is fixed by the hardware and
already in `main.c`: release pad holds → raise 36/37/38 → init strips → *then* the show.

What it can be, given what we know:

- A **spatial sweep, not an index sweep.** The strip order is a confirmed serpentine from the
  bottom-right, and `layout.py` carries the physical positions, so the light can travel across the
  pad geometrically — a wipe that actually looks like a wipe. Underglow ring first, blooming
  outward into the keys, settling into the active profile's base colours.
- **The three status LEDs as a ramp** while it happens (`set_status()` / LEDC already there).
- **Doubling as a self-test.** The show is the identify sweep in miniature: a dead pixel, a
  half-raised rail or a mis-wired chain shows up in the first half-second, every boot, for free.
- **Battery level in the show's tail** — `Renderer.bar()` already draws a level on the underglow
  ring, so a cold boot can tell you the charge without being asked.

The other three moments are the same machinery pointed at different events:

- **A sleep breath.** One slow fade-down of the underglow before the rail drops, so "asleep" is
  distinguishable from "died". Snapping to black looks like a fault; a fade looks like a decision.
- **A shorter wake show, scaled to how deep the sleep was.** A stage-3 wake should be effectively
  instant — no show at all, because the press that woke it is doing something. Only a cold boot or
  a stage-5 power-on earns the full performance, which also keeps the animation off the hot path
  of a pad that wakes forty times a day.
- **Hold-to-power-off as a progress ring.** The vendor's *hold 2 seconds to turn off* is a
  guessing game with no feedback; `power_off_hold_ms` is already configurable, so fill the
  underglow ring as the hold progresses, complete the ring at the threshold, and abort visibly if
  released early. Nearly free, and it turns the one destructive gesture on the device into one you
  can see coming.

Config shape: a `power.show` block — named show, duration, and an off switch, because a light show
on every boot is charming for a week and then it isn't. Skip it below some battery threshold, and
skip it on a stage-3 wake by construction.

**Done when:** switching the pad on is a thing you'd show someone, holding rear to off has a
visible countdown, and none of it costs measurable battery.

## Phase 17 — Vendor conventions worth keeping (from the manual) 🔵 **filed, not started**

Source: [`images/idea-vendor-manual-power-layers-ble.png`](images/idea-vendor-manual-power-layers-ble.png)
— the stock manual's power, layers and Bluetooth pages. Not aspiration; this is what the hardware
in hand already did before we reflashed it, which makes it both a spec and a baseline we should
not regress against.

- **Rear button, three gestures.** Click = on; click again = sleep; hold 2 s = off; any key wakes.
  `power.power_button` and `power_off_hold_ms` cover two of the three — **click-to-sleep is
  missing**, and it matters more than it looks: it's the manual entry into Phase 15's ladder, for
  the times you know you're done rather than waiting out `idle_dim_after_s`. There is already a
  `sleep` action token, but it's host-side; this one has to be on-device.
- **Layers on the three status LEDs.** Stock shows the current layer (up to six) as a pattern
  across the LED trio by the touch pad, and a touch tap cycles through them. LibreMicro has the
  richer primitives already — profiles, modes, `profile: next` bindable to touch, and 13 RGB keys
  that can state the layer far more legibly than three white dots — but **nothing currently maps
  the status trio to anything**; `lighting.status_leds` is a raw duty array. A profile/mode index
  on that trio is a small, obviously-correct win, and it works when the pad is out of eyeline of
  the cheat sheet.
- **The BLE channel UX, as Phase 9's spec.** Hold touch 3 s to enter comms mode (underglow turns
  blue), tap to cycle channels 1/2/3, a fourth tap selects wired (underglow white), fast blink
  means pairing and solid means paired, and it exits on 5 s of inactivity. That is a complete,
  proven, screen-free flow for the hardest phase in the project — worth adopting rather than
  designing from scratch. Note how much of it is Phase 16's vocabulary: colour for mode, blink
  rate for state, timeout for exit.

**Done when:** a pad running LibreMicro loses nothing a pad running stock could do, and the things
it keeps are the ones that were actually good.

## Sequencing notes and risks — current

- **Rear button** is the one input still dark, and the blocker is the ULP reset watcher rather
  than anything unknown about the pin.
- **Watchers and the agent surface** are built and unit-tested but unproven against a live Slack
  unread count or a running Claude Code session. Both need macOS Accessibility and Automation
  granted to whatever launches the daemon.
- **Idle sleep and power-off** remain, and they inherit the same GPIO 2 hazard as the rear button
  plus stock's wait-for-release guard before arming ext0.
- **A daemon launched from a sandboxed shell cannot change system volume.** Reads succeed and
  writes are silently swallowed, which is indistinguishable from broken hardware and cost real
  debugging time. Launch it from a normal terminal.
