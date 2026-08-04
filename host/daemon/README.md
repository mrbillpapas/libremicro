# Host daemon (planned)

Not built yet. This is the design placeholder for the "brains" described in `docs/DESIGN.md`
and `VISION.md`.

## Responsibilities

- Open the CM2 serial port; stream input events in and LED commands out (reusing the
  primitives in `host/cli/lmctl.py`).
- Load `host/config/*.json` (validated against `host/config/schema.json`).
- **Launcher:** map each key to an app / command; colour keys per config.
- **Modes:** on a mode-key press, flash the key and rebind the encoder (media→volume,
  desk→height, …).
- **Watchers:** background pollers that pulse a key's LED on external state (e.g. Slack unread).
- **Agent mode (later):** reflect a Claude Code session's status on the LEDs; keys for
  approve/deny/switch; encoder as an effort knob.

## Prerequisite

The daemon needs the firmware to **emit input events** over serial (`key <i> down|up`,
`enc cw|ccw|press`, `touch`, `rear`). That's the v2 "thin-transport" firmware change — see
`docs/DESIGN.md`. The LED-out direction already works today via the current firmware.

## Likely shape

A small Python service (pyserial for the link) with:
- a serial reader thread emitting parsed events,
- a config-driven dispatcher (launch via `open -a`, media keys, AppleScript, or MCP calls),
- pluggable watcher tasks,
- an LED renderer that owns the device's lighting state.

Kept as thin glue over the serial protocol + OS actions so behaviour stays in the JSON config.
