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
the pad can launch apps, switch modes, mirror notifications, and drive an agent-coding
workflow. See [docs/VISION.md](docs/VISION.md).

## Status

- ✅ **Per-key + underglow RGB working** under custom firmware (the long-standing "LEDs never
  light" blocker is solved — it was an undriven GPIO36 power rail + battery-backed pad holds;
  see [docs/HARDWARE.md](docs/HARDWARE.md)).
- ✅ Three PWM status/"touch" LEDs controllable.
- ✅ Key-matrix pin map fully reverse-engineered and verified.
- 🚧 Host daemon (launcher / modes / notifications) — designed, not yet built.
- 🚧 v2 "thin-transport" firmware that emits input events over serial — pending touch/encoder
  pin re-verification.

## Layout

| Path | What |
|---|---|
| `firmware/` | ESP-IDF custom firmware (per-key RGB + status LEDs + serial command API) |
| `host/cli/lmctl.py` | Low-level LED/serial CLI |
| `host/daemon/` | (planned) launcher / modes / notifications runtime |
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

# 3. Drive the LEDs
python3 ../host/cli/lmctl.py demo
python3 ../host/cli/lmctl.py key 3 ff0000     # key 3 red
python3 ../host/cli/lmctl.py rainbow
```

To go back to stock, see [docs/RECOVERY.md](docs/RECOVERY.md).

## Hardware

ESP32-S3-WROOM-1 (N16R8). All eFuses ship unlocked — no Secure Boot, no flash encryption —
so custom firmware boots without circumventing any protection. Full pin map and the LED
power-rail details are in [docs/HARDWARE.md](docs/HARDWARE.md).

## License

MIT (our code only — see `LICENSE`). This repo contains **no** vendor firmware, binaries, or
SDK source; obtain those from Work Louder directly if you need to restore stock.
