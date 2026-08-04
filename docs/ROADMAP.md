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
