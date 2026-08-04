# Architecture

LibreMicro is split into a **thin firmware** on the device and a **host daemon** on the
computer. The firmware knows nothing about apps, modes, or notifications — it only drives
LEDs and reports raw input. All product logic lives on the host, where it's easy to change
and AI-editable.

```
Creator Micro 2 (thin firmware)          Host daemon (the brains)
  LED sink:                                reads host/config/*.json
    k <i> <rgb>  key LED           ◄────   key press  → launch app / run command
    u <i> <rgb>  underglow LED             modes      → flash key + rebind the encoder
    t <i> <duty> status LED                watchers   → e.g. Slack unread → pulse a key
  Input source (planned v2):               agent mode → Claude Code status → LEDs
    key <i> down|up               ────►    (all latency-tolerant; a few ms is fine)
    enc cw|ccw|press
    touch, rear
```

## Firmware (device)

Current firmware (`firmware/src/main.c`) is the **LED sink** half plus a serial command loop:
per-key + underglow addressable RGB, three PWM status LEDs, brightness, and a boot self-test.
It exposes a newline-delimited ASCII protocol over USB-Serial-JTAG (see `docs/PROTOCOL.md`).

**Planned v2** adds the **input source** half: scan the key matrix and watch the encoder /
touch / rear button, and emit one line per event (`key 3 down`, `enc cw`, `touch`, …) on the
same serial link. That single addition is what every host-side use case rides on. It needs
the touch/encoder/rear pins re-verified first (the key-matrix pins are already confirmed —
see `docs/HARDWARE.md`).

Transport today is **USB serial**, which is also the lowest-latency option and is required
anyway because the daemon runs on the attached computer. A **BLE HID** standalone mode (so the
pad works as a plain keyboard with no daemon) is a later, independent addition.

## Host daemon (computer)

Not built yet — `host/daemon/` holds the design placeholder. Responsibilities:

- Open the serial port, stream input events in, stream LED commands out (via the same
  primitives `host/cli/lmctl.py` already implements).
- Load `host/config/*.json`; map each key to a launch target / command / mode.
- Run **modes**: on a mode key press, flash the key and switch the encoder's binding.
- Run **watchers**: background pollers (e.g. Slack unread count) that pulse a key's LED.
- Optional **agent mode**: subscribe to a Claude Code session's status and reflect it.

The daemon is deliberately thin glue over (a) the serial protocol and (b) OS actions
(`open -a`, media keys, AppleScript, MCP calls). Keeping the config as JSON + JSON Schema is
what makes the whole thing AI-native: an assistant can read the schema and generate a config.

## Why this split

- **Instant enough:** host round-trip is single-digit milliseconds — imperceptible for
  launching, mode switches, and notifications.
- **Changeable:** new behaviour is a host code/config change, not a reflash.
- **AI-friendly:** the interesting surface (config) is plain JSON with a schema.
- **Safe firmware:** the on-device code stays tiny and rarely needs to change, which matters
  because reflashing has real failure modes (see the boot-loop note in `docs/RECOVERY.md`).
