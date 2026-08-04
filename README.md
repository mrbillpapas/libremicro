# LibreMicro

Open custom firmware and host tooling for the **Work Louder Creator Micro 2** (CM2)
macropad — turning it into a fully programmable, AI-native control surface with individually
addressable per-key RGB.

> **Not affiliated with, endorsed by, or supported by Work Louder.** "Work Louder" and
> "Creator Micro" are their trademarks, used here only to describe device compatibility.
> This is an independent interoperability project for hardware you own. Flashing custom
> firmware will void your warranty. See [docs/RECOVERY.md](docs/RECOVERY.md) to restore stock.

## Why

The vendor firmware exposes only two whole-zone colours over its RPC. The CM2's hardware
actually has **13 individually addressable per-key LEDs + 8 underglow LEDs** — the vendor
just keeps per-pixel control internal. LibreMicro unlocks it, and adds a host-side layer so
the pad can launch apps, fire keyboard shortcuts, run scripts, switch modes, mirror
notifications, and drive an agent-coding workflow — configured as JSON or through a local web
UI. The goal is a full open-source alternative to Work Louder's Input software. See
[VISION.md](VISION.md) for the product and [docs/ROADMAP.md](docs/ROADMAP.md) for the plan.

## Status

- ✅ **Per-key + underglow RGB working** under custom firmware (the long-standing "LEDs never
  light" blocker is solved — it was an undriven GPIO36 power rail + battery-backed pad holds;
  see [docs/HARDWARE.md](docs/HARDWARE.md)).
- ✅ Three PWM status/"touch" LEDs controllable.
- ✅ Key-matrix pin map fully reverse-engineered and verified.
- ✅ **Host daemon core** — config schema v2 with validation and v1 migration, 16 palettes,
  10 animated effects, perceptual (OKLab) colour, frame compositing and streaming,
  export/import, and a local HTTP API. 56 tests, no device required.
- ✅ **Local web UI** — layout-accurate device view, palette and effect designer with live
  on-device preview, identify sweep, export/import.
- ✅ **LED index mapping confirmed** on hardware (a serpentine starting bottom-right), shipped
  as a default so spatial effects are correct out of the box.
- ✅ **Bindings** — app launch, keyboard chords, typed text, shell/script/AppleScript with
  trigger context, built-in actions, press/release/hold/double, modes with encoder rebinding,
  and profiles.
- 🟡 **Firmware v2** (input events + batched frame writes) — written and compiling, **not yet
  flashed**. Until it is, bindings fire from injected events (`POST /api/simulate`) rather than
  real presses. **This is the critical path.**
- 📋 Notification watchers, agent control surface.
- 📋 Power on/off, staged idle sleep + battery reporting, BLE HID standalone mode.

Phased plan with acceptance criteria: [docs/ROADMAP.md](docs/ROADMAP.md).

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
