# Roadmap

Sequenced delivery plan for the scope in [`VISION.md`](../VISION.md). Phases are ordered so that
each one ends with something demonstrable, and so the hardware-blocked work sits behind work that
isn't blocked.

## Start here

Three things need a human, in this order.

**1. Flash firmware v2.** It's written and compiles; nothing else can proceed without it.
Until it's flashed, bindings only fire from `POST /api/simulate`, never from the pad.

```bash
P=$(./scripts/enter_bootloader.sh)
./.venv/bin/esptool --port "$P" --chip esp32s3 write-flash 0x10000 firmware/.pio/build/cm2/firmware.bin
```

Then verify in this order: LEDs still light → each key gives exactly one `down` and one `up`
→ **the orientation** (top-left should report `key 0`, bottom-right `key 12`). The web UI's
event feed shows what arrived. If it's transposed, fix `MTX_TO_LOGICAL` in `firmware/src/main.c`
— that's the one assumption left in the key path. Keep [`RECOVERY.md`](RECOVERY.md) open;
`firmware/README.md` records a boot-loop on an earlier revision.

The encoder, touch pad and rear button are behind `LM_ENABLE_UNVERIFIED_INPUTS`, off by
default. Their pins are now confirmed ([`PIN-VERIFICATION.md`](PIN-VERIFICATION.md)), but read
the GPIO 2 hazards in [`HARDWARE.md`](HARDWARE.md) first — stock's rear-button rescue arms a ULP
watcher that resets the device on a rear press, and our own flashing procedure is what arms it.

**2. Grant two macOS permissions**, both to whatever launches the daemon (Terminal/iTerm, or the
launchd job) rather than to the daemon or its helpers — macOS attributes them to the responsible
process. Without these, shortcut bindings and notification watchers silently do nothing:

- **Accessibility** — needed for keyboard synthesis and for reading Dock badges.
- **Automation → System Events** — needed for the watchers' Dock query.

`GET /api/status` reports both under `keys`, and the web UI warns up front.

**3. Confirm the matrix orientation and bind your keys.** The LED mapping is already confirmed
and shipped; the input side is the remaining unknown, and pressing each key with the event feed
open is how it gets settled.

## Where we actually are

The LED-out half **works on hardware**: custom firmware is flashed and driving 13 per-key LEDs,
8 underglow LEDs and 3 PWM status LEDs, with the strip-index mapping confirmed by identify
sweep. The lighting engine, config layer, web UI and full binding dispatch are all built.

What the pad still can't do is tell the host that you pressed something. Firmware v2 fixes that
and is written, but not yet flashed — so bindings currently fire from injected events rather
than real presses. Everything downstream of a keypress (notifications, agent control) is being
built against that same injected path, which means it will work the moment v2 lands.

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
- **Identify sweep** that lights each LED in turn so the strip-index-to-physical-position mapping
  can be confirmed by eye and written back into the config (see the open question in
  [`HARDWARE.md`](HARDWARE.md)).
- Export and import a full configuration bundle.

**Done when:** someone can open a local URL, design a lighting setup by clicking, watch it apply
to the device in real time, export it, and re-import it on a fresh machine.

## Phase 2 — Input events: firmware v2 🟡 **written, not flashed**

The code exists and compiles clean (`firmware/src/main.c`): key matrix scanning, `key <i>
down`/`up` events carrying **logical** indices, and the batched `kf`/`uf` frame commands.
Encoder, touch pad and rear button are behind `LM_ENABLE_UNVERIFIED_INPUTS`, off by default,
until their pins are confirmed.

**What's left is a human flashing it**, then verifying in this order: LEDs still light; each
key gives exactly one `down` and one `up`; and the orientation — top-left should report
`key 0` and bottom-right `key 12`. If it's transposed, the fix is one lookup table
(`MTX_TO_LOGICAL`). Keep [`RECOVERY.md`](RECOVERY.md) open: `firmware/README.md` records a
boot-loop on an earlier revision, so step one is a real risk rather than a formality.

This was the gate, and both of its prerequisites are now done:

1. ~~**Re-verify the provisional pins.**~~ ✅ Resolved by static analysis of stock v0.6.1 — see
   [`PIN-VERIFICATION.md`](PIN-VERIFICATION.md). The headline: touch and rear were *swapped* in
   the old table, which is the whole reason GPIO 2 appeared to be cited twice. There was no
   conflict.
2. ~~**Emit input events**~~ ✅ Implemented, per the grammar in
   [`PROTOCOL.md`](PROTOCOL.md).

**Done when:** every physical input produces exactly one correct event line, with no ghosting from
matrix scanning and no missed encoder detents during fast rotation, while LED commands continue to
work on the same connection. — *pending a flash; none of this is confirmed on hardware yet.*

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
- ~~**LED index mapping is unverified.**~~ **Resolved.** Both chains were confirmed on hardware
  by identify sweep: a serpentine starting bottom-right (see `HARDWARE.md`). Because the wiring
  is identical on every Creator Micro 2, it now ships as a source default rather than per-user
  config, so spatial effects are correct out of the box. Config override remains for a future
  hardware revision.
- **Reflashing has real failure modes.** Firmware phases (2, 8, 9) each carry boot-loop risk; keep
  [`RECOVERY.md`](RECOVERY.md) current and take a flash backup before each firmware milestone.
- **Don't build the feature matrix.** Matching Work Louder Input feature-for-feature is the
  outcome, not the method. Ship the four original use cases well first.
