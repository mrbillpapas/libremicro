# Roadmap

Sequenced delivery plan for the scope in [`VISION.md`](../VISION.md). Phases are ordered so that
each one ends with something demonstrable, and so the hardware-blocked work sits behind work that
isn't blocked.

## Where we actually are

The LED-out half of the system **works today**: the current firmware drives 13 per-key LEDs,
8 underglow LEDs, and 3 PWM status LEDs over a newline-delimited serial protocol, and
`host/cli/lmctl.py` can already talk to it.

Nothing reads *input* from the pad yet. Every behavioural use case — launcher, shortcuts,
scripts, modes, notifications, agent control — needs key presses and encoder turns to reach the
host, so all of them sit behind one firmware change (Phase 2), which itself sits behind
re-verifying three provisional pins.

That's why Phases 0 and 1 are lighting and tooling: they're **fully unblocked**, they exercise
the config and effect engine that everything later depends on, and they produce a pad that
visibly does something new.

## Phase 0 — Foundations ✅ **built**

Host-side skeleton and the config contract everything else is written against.
Lives in `host/daemon/` — 56 tests, no device required. See its README for what's in each module.

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

## Phase 1 — Lighting studio: web UI v1 (unblocked)

The first thing a user can actually *use*, and it needs no firmware change.

- Layout-accurate device view: key rows of **2/4/4/3** and the **3×3-minus-centre** underglow.
- Click a key or underglow cell to set its colour; palette and effect designer with **live
  preview on the physical pad** while dragging.
- **Identify sweep** that lights each LED in turn so the strip-index-to-physical-position mapping
  can be confirmed by eye and written back into the config (see the open question in
  [`HARDWARE.md`](HARDWARE.md)).
- Export and import a full configuration bundle.

**Done when:** someone can open a local URL, design a lighting setup by clicking, watch it apply
to the device in real time, export it, and re-import it on a fresh machine.

## Phase 2 — Input events: firmware v2 (the critical path)

**This is the gate.** Two steps, in order:

1. **Re-verify the provisional pins** — touch pad, encoder A/B/switch, rear button — against the
   disassembly. `HARDWARE.md` currently flags these as unconfirmed, including a direct conflict
   between the rear button and the ext0 wake pin that has to be resolved. The key matrix is
   already verified and needs no further work.
2. **Emit input events** on the existing serial link: `key <i> down|up`, `enc cw|ccw`,
   `enc press|release`, `touch`, `rear` — per the grammar already drafted in
   [`PROTOCOL.md`](PROTOCOL.md).

**Done when:** every physical input produces exactly one correct event line, with no ghosting from
matrix scanning and no missed encoder detents during fast rotation, while LED commands continue to
work on the same connection.

## Phase 3 — Bindings: launcher, shortcuts, scripts

Use case 1, plus the two binding types added to the vision.

- **Launch** bindings (`open -a`, profile-specific `open -na … --args`).
- **Keyboard shortcut** synthesis: chords, media keys, and text insertion.
- **Script triggers**: shell / script file / AppleScript, with trigger context passed as
  environment variables, run without blocking the event loop, able to write back to the pad's LEDs.
- Press vs. release, `hold`, and `double` as distinct trigger kinds.

**Done when:** pressing a key launches its app, fires its chord in a real application, or runs its
script with correct context — and a slow script cannot stall the pad.

## Phase 4 — Modes and the encoder

Use case 2. Mode key press → confirmation flash → encoder rebinds; media mode drives volume,
desk mode drives desk height. Optional whole-pad recolour per mode.

**Done when:** the same dial does different jobs depending on the active mode, the active mode is
visible on the LEDs, and mode state survives a daemon restart.

## Phase 5 — Web UI v2: bindings, profiles, battery

Now that bindings exist, the editor covers them.

- Binding editor per trigger, with a **shortcut recorder** (press the chord instead of typing it).
- Profile management: create, switch, cycle, and optional auto-switch on frontmost app.
- Battery level and charge state display (needs Phase 8 for the device-side read; until then the
  UI can ship the panel disabled).

**Done when:** the whole config — bindings included — is editable without touching JSON, and
profiles can be switched from both the UI and the pad.

## Phase 6 — Notification watchers

Use case 3. Background pollers that pulse a key on external state; Slack unread is the headline.
Watchers are pluggable so new sources don't touch the core.

**Done when:** the Slack key pulses on unread and stops when read, without the poller affecting
input latency.

## Phase 7 — Agentic-coding control surface

Use case 4. Agent status on the LEDs, session switching, push-to-dictate, approve/deny keys,
encoder as an effort knob, targeting Claude Code as the first harness.

**Done when:** a live coding session's state is legible on the pad and the pad can act on it.

## Phase 8 — Power, sleep and battery (firmware)

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
- **LED index mapping is unverified.** Effects that are spatial (gradients, ripples, ring chases)
  are only correct once index-to-position is confirmed. The Phase 1 identify sweep is the fix, and
  keeping the mapping in config rather than in code means correcting it is a data change.
- **Reflashing has real failure modes.** Firmware phases (2, 8, 9) each carry boot-loop risk; keep
  [`RECOVERY.md`](RECOVERY.md) current and take a flash backup before each firmware milestone.
- **Don't build the feature matrix.** Matching Work Louder Input feature-for-feature is the
  outcome, not the method. Ship the four original use cases well first.
