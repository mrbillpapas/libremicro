# LibreMicro — Vision

What the Creator Micro 2 should be able to do once it's fully programmable, and the product
this project is building toward. Captured from the design conversation so it lives outside
anyone's chat history.

## The core idea

The CM2 is a beautiful 13-key macropad with a rotary encoder, a joystick, a capacitive touch
pad, per-key RGB, and underglow. The vendor software treats it as a static macro pad with
two-colour lighting. LibreMicro treats it as a **programmable control surface** whose
behaviour and lighting are driven by software you (and your AI) fully control.

**Latency principle:** LED colour/pattern changes are driven from the host, and a few
milliseconds of latency is completely fine. So the firmware stays a thin, dumb transport and
*all* the intelligence lives on the computer — where it's easy to change, script, and let an
AI edit. Only genuinely reflex-speed effects (if any) would ever move on-device.

## Use cases (in priority order)

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

## AI-native configuration

Everything above is expressed as **lightweight JSON config** — keys, colours, launch targets,
modes, encoder bindings, and notification watchers. The point is that a user can hand the
config (and its JSON Schema) to an AI and say "make the Slack key purple and pulse it on
unread, and add a desk mode on key 5," and get a working setup back. No proprietary GUI
required. A local web UI (served from a local process) is a possible later addition for
visual editing and live LED preview — but JSON-first keeps it scriptable and AI-friendly.

## Open source

The goal is to publish this for other Creator Micro owners. See `README.md` for the
trademark/compatibility disclaimer and `docs/RECOVERY.md` for how users restore stock
firmware (which we do not redistribute). Our code is MIT; no vendor binaries or SDK source
ship in this repo.
