# LibreMicro

Open custom firmware and host tooling for the **Work Louder Creator Micro 2** (CM2)
macropad — turning it into a fully programmable, AI-native control surface with individually
addressable per-key RGB.

> **Not affiliated with, endorsed by, or supported by Work Louder.** "Work Louder" and
> "Creator Micro" are their trademarks, used here only to describe device compatibility.
> This is an independent interoperability project for hardware you own. Flashing custom
> firmware will void your warranty. See [docs/RECOVERY.md](docs/RECOVERY.md) to restore stock.

![The LibreMicro web UI: a layout-accurate view of the pad with the underglow drawn as eight
segments tiling the perimeter, alongside the binding editor](docs/images/webui.png)

*The local web UI. The pad is drawn to its real geometry — 13 switches under 12 keycaps on a 4×4
grid, eight underglow LEDs tiling the whole perimeter, and the encoder, joystick and touch pad
shown for orientation. Everything edits one JSON document.*

## Why

The vendor firmware exposes only two whole-zone colours over its RPC. The CM2's hardware
actually has **13 individually addressable per-key LEDs + 8 underglow LEDs** — the vendor
just keeps per-pixel control internal. LibreMicro unlocks it, and adds a host-side layer so
the pad can launch apps, fire keyboard shortcuts, run scripts, switch modes, mirror
notifications, and drive an agent-coding workflow — configured as JSON or through a local web
UI. The goal is a full open-source alternative to Work Louder's Input software. See
[VISION.md](VISION.md) for the product and [docs/ROADMAP.md](docs/ROADMAP.md) for the plan.

## Status

**Working on hardware.** Custom firmware v2 is flashed and every input reports:

- ✅ **Per-key + underglow RGB**, with the strip mapping confirmed by identify sweep (a
  serpentine from the bottom-right) and shipped as a default, so spatial effects are correct
  out of the box.
- ✅ **13 keys**, reporting stable logical indices.
- ✅ **Rotary encoder**, quadrature matched to the vendor's own decoder — reversal-reset and
  hardware glitch filtering included, which is what stopped it feeling erratic.
- ✅ **Capacitive touch pad** and the **analog joystick** (two ADC axes, eight bindable
  directions, calibrated against the pad's real rest point rather than stock's assumed one).
- ✅ **Battery reporting** from the MAX77972 over I²C — which incidentally proved this repo's
  own note wrong: SCL is GPIO 18, not 9.
- ✅ **Bindings**: app launch, keyboard chords, typed text, shell/script/AppleScript with
  trigger context, built-in actions, press/release/hold/double, modes with encoder rebinding,
  and profiles.
- ✅ **Local web UI**: layout-accurate device view, palette and effect designer with live
  preview, binding editor with a shortcut recorder, per-trigger testing, and an event feed.
- ✅ **Feedback on the pad**: volume shows as a bar across the underglow, because macOS gives
  no overlay for a programmatic volume change.
- 🟡 **Rear button** — pin confirmed, deliberately off: stock arms a ULP watcher that resets
  the device when that pin goes low. See [docs/HARDWARE.md](docs/HARDWARE.md).
- 🟡 **Notification watchers** and the **Claude Code control surface** — built and tested, not
  yet proven against a live session or a real unread count.
- 📋 Idle sleep, power-off, BLE HID standalone mode.

Phased plan and the calibration constants that had to be measured on hardware:
[docs/ROADMAP.md](docs/ROADMAP.md).

## Layout

| Path | What |
|---|---|
| `firmware/` | ESP-IDF custom firmware (per-key RGB + status LEDs + serial command API) |
| `host/cli/lmctl.py` | Low-level LED/serial CLI |
| `host/daemon/` | Lighting engine, config, HTTP API — see its [README](host/daemon/README.md) |
| `host/webui/` | Local config editor + palette designer with live LED preview |
| `host/config/` | JSON config example + schema (the AI-native customization surface) |
| `host/swift/` | IOKit HID tools that talk to the **stock** vendor RPC (RE + fallback) |
| `tools/` | Xtensa disassembly / firmware-analysis toolkit used for the RE |
| `scripts/` | Bootloader-entry + flash-backup helpers |
| `docs/` | Design, hardware, protocol, recovery, and reverse-engineering notes |

## Quickstart

```bash
# 1. Build the firmware (needs PlatformIO; it fetches ESP-IDF + the Xtensa toolchain)
cd firmware && pio run

# 2. Flash app-only at 0x10000 (preserves the vendor nvs + littlefs). Device on /dev/cu.usbmodem*
P=$(ls /dev/cu.usbmodem*)
esptool --port $P write-flash 0x10000 .pio/build/cm2/firmware.bin

# 3. Drive the LEDs directly (low-level escape hatch)
python3 ../host/cli/lmctl.py demo
python3 ../host/cli/lmctl.py key 3 ff0000     # key 3 red
python3 ../host/cli/lmctl.py rainbow
```

Then run the daemon, which gives you palettes, animated effects, and the web UI:

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e host/daemon
./.venv/bin/libremicro                        # web UI on http://127.0.0.1:8777
```

It runs fine with no device attached, so you can design lighting before you flash anything.

To go back to stock, see [docs/RECOVERY.md](docs/RECOVERY.md).

## Hardware

ESP32-S3-WROOM-1 (N16R8). All eFuses ship unlocked — no Secure Boot, no flash encryption —
so custom firmware boots without circumventing any protection. Full pin map and the LED
power-rail details are in [docs/HARDWARE.md](docs/HARDWARE.md).

## License

MIT (our code only — see `LICENSE`). This repo contains **no** vendor firmware, binaries, or
SDK source; obtain those from Work Louder directly if you need to restore stock.
