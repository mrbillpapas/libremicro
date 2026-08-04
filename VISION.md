# LibreMicro — Vision

What the Creator Micro 2 should be able to do once it's fully programmable, and the product
this project is building toward. Captured from the design conversations so it lives outside
anyone's chat history.

## The core idea

The CM2 is a beautiful 13-key macropad with a rotary encoder, a joystick, a capacitive touch
pad, per-key RGB, and underglow. The vendor software treats it as a static macro pad with
two-colour lighting. LibreMicro treats it as a **programmable control surface** whose
behaviour and lighting are driven by software you (and your AI) fully control.

The end goal is a **full open-source alternative to Work Louder's Input software** — a real
replacement, not a toy: bindings, keyboard shortcuts, scripts, per-key lighting, effects,
profiles, power management, Bluetooth, and a visual editor. We get there by shipping the use
cases below in order, not by trying to match a feature matrix up front.

## Architecture principle (and its one exception)

**Latency principle:** LED colour/pattern changes are driven from the host, and a few
milliseconds of latency is completely fine. So the firmware stays a thin, dumb transport and
*all* the product intelligence lives on the computer — where it's easy to change, script, and
let an AI edit.

**The exception — untethered operation.** Power on/off, deep sleep, battery monitoring, and
BLE HID must work when no host is connected, so they *cannot* live on the host. The firmware
therefore owns a small, well-bounded set of device concerns:

| Lives on device (firmware) | Lives on host (daemon) |
|---|---|
| Power on/off, deep sleep + wake | Launcher, shortcuts, scripts, modes |
| Idle timeout / battery saver | Notification watchers, agent integration |
| Battery gauge reads (MAX77972) | Palettes, effects, animation frames |
| BLE HID standalone keymap | Config, profiles, export/import, web UI |
| LED sink + input event source | Everything a user would want to change |

The dividing line: **the device owns staying alive; the host owns being clever.** Anything a
user might want to tweak weekly belongs on the host. Reflashing has real failure modes (see
`docs/RECOVERY.md`), so firmware changes should be rare.

## Use cases (in priority order)

These first four are the original scope and stay the priority — everything after is additive.

1. **App launcher (primary).** Each key launches an app or profile — Slack, Zoom, Chrome
   profiles, WhatsApp, Messages — with a per-key colour so the pad is a glanceable launcher.

2. **Modes paired with the dial.** Press a "mode" key → it flashes to confirm → that mode
   activates and the rotary encoder rebinds to match:
   - Media key → flashes → **media mode** → rotary = volume, press = play/pause.
   - Desk key → flashes → **desk mode** → rotary = desk height up/down, press = sit/stand.
   The same physical dial does different jobs depending on the active mode.

3. **Notifications → key flash.** A key that launches an app also *reflects that app's state*.
   The headline example: the Slack key pulses when you have unread Slack messages, so the pad
   is an ambient notifier, not just a launcher.

4. **Agentic-coding control surface.** Recreate what the "Codex Micro" does, but in a harness
   of your choice (e.g. Claude Code): agent status shown on the LEDs, session switching,
   push-to-dictate, approve/deny keys, and the rotary dial as an "effort" knob. The pad
   becomes a hardware controller for an AI coding session.

## Binding types

Every key, encoder direction, encoder press, touch pad, and rear button is a **trigger**. Each
trigger binds to one of:

- **Launch** — open an app or a specific app profile (`open -a`, `open -na … --args`).
- **Keyboard shortcut** — synthesise a keystroke or chord (`cmd+shift+4`, `ctrl+opt+space`),
  including media keys and arbitrary Unicode text insertion. This is what makes the pad useful
  in apps that have no other integration.
- **Script trigger** — run a shell command, script file, or AppleScript, with the trigger
  context (which key, which mode, press vs. release, encoder delta) passed in as environment
  variables so one script can serve several bindings. Long-running scripts must not block the
  event loop, and a script can write back to the pad's LEDs to report its own status.
- **Mode switch** — activate a named mode, rebinding the encoder and optionally recolouring
  the whole pad.
- **Built-in action** — volume, brightness, play/pause, profile cycling, sleep, and similar
  actions the daemon implements natively.

Chords and long-press should be expressible too (`hold`, `double`, `key+key`), since 13 keys
runs out fast once the pad is genuinely useful.

## Lighting: palettes and effects

Per-key and underglow lighting deserves to be more than 21 static hex values. The pad has real
spatial structure worth exploiting:

- **Key caps:** 4 rows of **2, 4, 4, 3** = 13 LEDs.
- **Underglow:** a **3×3 grid with no centre LED** = 8 LEDs.

That means effects can be genuinely *spatial* — gradients across the pad, a chase that circles
the underglow ring, a ripple that radiates from the pressed key outward into the underglow —
rather than just per-index colour assignment.

**Don't reinvent colour science or the effect corpus.** Two established bodies of work to build
on rather than rewrite:

- **Colour maths and palette generation:** the modern answer is perceptual colour spaces
  (OKLCH/OKLab), where equal steps look equal to the eye and interpolation doesn't pass through
  muddy grey. [`culori`](https://culorijs.org/) is the strongest fit for the web UI — ESM-native,
  full CSS Color 4 support, gradients and interpolation, and the library Tailwind v4 uses for
  its own colour system. Python-side, [`coloraide`](https://facelessuser.github.io/coloraide/) is
  the closest equivalent for the daemon, and `chroma.js` remains excellent if we want its
  data-viz-style scale API.
- **Effects and palettes:** [WLED](https://github.com/wled/WLED) is the reference open-source
  addressable-LED effect engine — 100+ effects and ~50 palettes, with the palette corpus
  inherited from FastLED and cpt-city. Its custom-palette format is simply JSON colour stops.
  Adopting a **WLED-compatible palette JSON format** means we inherit a large, well-loved
  gradient library for free and users can import palettes they already have.

The pitch to the user is: pick a palette, pick an effect, pick a speed, and it looks good —
then hand-tune individual keys on top if you want. Effects render on the host at a modest frame
rate and stream over serial, so adding an effect is a host-side change, never a reflash.

## Web UI

JSON-first stays the foundation, but a **local web UI** (served by the daemon, no cloud, no
account) is how this becomes usable by people who don't want to hand-edit JSON. It should show
a **layout-accurate picture of the pad** — the 2/4/4/3 key rows and the 8-LED underglow ring in
their true positions — and let you:

- click a key to set its colour, label, and binding (app, shortcut, or script),
- design palettes and effects with **live preview on the physical device** as you drag,
- see and edit modes, and watch the active mode change live,
- record a keyboard shortcut by pressing it, rather than typing its name,
- run an **identify** sweep that lights each LED in turn, so the strip-index-to-physical-position
  mapping can be confirmed by eye (this mapping is not yet verified — see `docs/HARDWARE.md`),
- read battery level and charge state,
- import and export the whole configuration.

The web UI is a *view onto the same JSON config* the daemon reads — never a separate source of
truth, and never a required dependency. Everything remains fully usable from the CLI and by
editing files.

## Profiles, export and import

Configuration must be **portable and shareable**: a single self-contained bundle carrying keys,
bindings, colours, palettes, effects, modes, and settings, that another CM2 owner can import and
have work immediately. This is what lets people publish setups ("here's my Blender pad", "here's
my Claude Code pad") and what makes the AI-native workflow safe to experiment with, since you
can always export first and restore after.

**Profiles** are multiple named configurations on one machine — per-app or per-context — with a
key or a built-in action to cycle between them, and optional automatic switching based on the
frontmost application.

## Power, battery and Bluetooth

The pad has a battery, a MAX77972 charger/fuel-gauge, and a BLE stack in stock firmware. Custom
firmware needs to be a good citizen of that hardware:

- **Power on/off.** A deliberate off state that survives being unplugged, entered via a
  long-press (the rear button is the likely candidate) and exited by the same button. Stock does
  this by latching every digital pad into the RTC domain and deep-sleeping; our firmware must do
  the same and, critically, *release those holds at boot* — the exact mechanism that made early
  custom firmware boot dark (see `docs/HARDWARE.md`).
- **Idle sleep / battery saver.** Staged, not binary: after a period of no input, dim the LEDs;
  later, turn them off entirely; later still, deep-sleep the device. Any input wakes it. The
  timeouts are user config, the enforcement is on-device so it works untethered, and the
  thresholds should differ on battery vs. USB power.
- **Battery reporting.** Read state of charge and charging status over I²C and report it to the
  host, so the web UI can display it and a key can optionally show it as a colour.
- **Bluetooth pairing.** A pairing flow with clear LED feedback (pairing / paired / connected),
  and **BLE HID standalone mode** so the pad works as a plain keyboard against a phone, an iPad,
  or a machine with no daemon installed. Standalone mode necessarily uses a static on-device
  keymap rather than host logic — it is the fallback, not the main experience.

BLE HID is the largest firmware lift in the project and deliberately sits late in the plan; the
tethered USB experience is where the interesting product is.

## AI-native configuration

Everything above is expressed as **lightweight JSON with a published JSON Schema** — keys,
colours, launch targets, shortcuts, scripts, modes, encoder bindings, palettes, effects,
watchers, and power settings. The point is that a user can hand the config and its schema to an
AI and say *"make the Slack key purple and pulse it on unread, add a desk mode on key 5, and
give me a warm sunset gradient across the underglow"* — and get a working setup back. No
proprietary GUI required.

The web UI is for people who'd rather click; the schema is for people (and agents) who'd rather
generate. Both edit the same file, and neither is privileged.

## Open source

The goal is to publish this for other Creator Micro owners. See `README.md` for the
trademark/compatibility disclaimer and `docs/RECOVERY.md` for how users restore stock firmware
(which we do not redistribute). Our code is MIT; no vendor binaries or SDK source ship in this
repo.

Sequenced delivery plan: [`docs/ROADMAP.md`](docs/ROADMAP.md).
Architecture: [`docs/DESIGN.md`](docs/DESIGN.md).
