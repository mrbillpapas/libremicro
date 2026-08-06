# Flashing & recovery

## Getting stock firmware (we do not ship it)

This repo contains **no vendor firmware**. To restore stock, obtain the official image from
Work Louder's public firmware releases (repo `worklouder/cm-v2-fw-releases`) and keep it
locally, e.g. in a `firmware-vendor/` directory (git-ignored). You want the merged/factory
image for the Creator Micro 2 that matches your device.

## Flashing the LibreMicro app (app-only, non-destructive)

Writing only the app region at `0x10000` keeps the vendor bootloader, your BLE pairing
(`nvs`), and the vendor `keymap.json` (`fs`) intact:

```bash
cd firmware && pio run
P=$(ls /dev/cu.usbmodem*)
esptool --port $P --before default-reset --after hard-reset \
        write-flash 0x10000 .pio/build/cm2/firmware.bin
```

esptool can force the ESP32-S3 into download mode over USB-Serial-JTAG, so this works even if
the currently-running firmware is misbehaving.

## Restoring stock through Work Louder's Input app

**No bootloader priming is needed. The app already sees the pad as flashable.** Its device kit
(`@worklouder/wl-device-kit`, bundled unminified with source maps inside `input.app`) finds a
flashable board in `WLDeviceDiscovery.findWLBootloaderDevices()` by listing *serial* ports and
keeping those with VID `0x303A` and, on macOS, manufacturer `"Espressif"`. Our firmware talks
over the ESP32-S3's USB-Serial-JTAG console, whose USB identity is fixed in ROM — `ioreg` shows
`idVendor 12346` (0x303A), `idProduct 4097` (0x1001), vendor name `Espressif`. That matches the
filter exactly, so a pad running LibreMicro is indistinguishable from one sitting in the ROM
bootloader as far as discovery is concerned.

Nor does download mode have to be arranged. `WLDeviceProgrammer` builds an esptool-js
`ESPLoader` over that port, passing `usbProductId: 0x1001`; esptool-js's `constructResetSequence`
matches that against `USB_JTAG_SERIAL_PID` and picks `UsbJtagSerialReset`, which walks DTR/RTS
to drive the chip into download mode in hardware regardless of what firmware is running. Same
mechanism plain `esptool` uses.

### …but out of the box its flash cannot succeed on macOS

**Confirmed on hardware: the app opens the wrong device node.** `findWLBootloaderDevices` passes
`port.path` from node-serialport's `SerialPort.list()` straight through, and on darwin that is the
**dial-in** node. The app's own log says so:

```
|wl_device_programmer| |node_web_serial_port| port is open on path:/dev/tty.usbmodem1301
|wl_device_programmer| Error connecting to device Error: Failed to connect with the device
    at ESPLoader.connect …
```

macOS dial-in (`/dev/tty.*`) nodes block on carrier detect and hang up when DTR drops — and
`setDTR(false)` is the *first* step of `UsbJtagSerialReset`. Everything that flashes ESP32s on
macOS uses the **call-out** node (`/dev/cu.*`) for exactly this reason. Measured side by side on
this pad:

| Port node | `esptool --before default-reset chip-id` |
|---|---|
| `/dev/cu.usbmodem1301` | connects, `USB mode: USB-Serial/JTAG`, reads MAC |
| `/dev/tty.usbmodem1301` | blocks forever in `open()`; the process cannot be killed even with `SIGKILL`, and it wedges the device node until the pad is physically unplugged |

Two consequences. First, the app's failure is silent-but-loud: `flashFiles` returns `false`
without ever reaching `writeFlash`, yet the UI still shows *100% / “Your device has been
updated!”* — so **a reported success there is not evidence anything was written.** Check
`~/Library/Logs/input/main.log`. Second, the app leaves the port open after the failed connect,
which is why the next screen says *No device found*.

Workaround if you want to flash through the app: in
`/Applications/input.app/Contents/Resources/app.asar.unpacked/node_modules/@worklouder/wl-device-kit/dist/index.js`,
inside `filterBootloaderDevices`, change `portPath: port.path` to

```js
portPath: process.platform === "darwin" ? port.path.replace("/dev/tty.", "/dev/cu.") : port.path,
```

That file is marked `unpacked` in the asar, so it is the copy Node actually loads. It breaks the
bundle's code signature, which in practice does not stop the app launching (the seal is already
invalid — `Contents/Resources/scripts/window-info-retriever.scpt` is modified in a stock install),
and an app update reverts it. Otherwise use the plain-`esptool` route below, which is unaffected.

**The other thing that blocks the app is the daemon holding the port** and re-opening it every two
seconds. So the revert flow is *release, then flash*:

```bash
curl -X POST http://127.0.0.1:8777/api/release     # or the Config panel's "Release device…"
```

That blanks the pad (the firmware has no host-disconnect timeout, so the last frame would
otherwise stay latched through the whole flash), drops the port, and stops reconnecting. Then in
Input, use **“Found device in bootloader mode, click here to reflash”**. It writes at offset `0`
with `eraseAll: false`, so `nvs` (BLE pairing) and `fs` (vendor `keymap.json`) survive, then
resets via `RTC_CNTL_OPTIONS0_REG` + DTR.

`POST /api/reclaim` takes the device back without restarting the daemon — useful if you change
your mind, since release writes nothing to the device. The same thing is bindable to a key as
the built-in action `release_device`; releasing is deliberately one-way from the pad, because
after it fires the daemon is no longer listening to the pad.

Quitting the daemon works too (it blanks the pad and closes the port on `SIGINT`/`SIGTERM`), but
releasing keeps the web UI up so the config stays editable while you flash.

## Restoring stock (full image)

```bash
P=$(ls /dev/cu.usbmodem*)
esptool --port $P write-flash 0x0 firmware-vendor/<the_stock_merged_image>.bin
```

This is proven and reversible; `keymap.json` and BLE pairing survive.

## Recovery paths if a flash goes bad

The device has three independent ways back to the ROM bootloader, so a bad app can't brick it:

1. **USB-Serial-JTAG** is fuse-enabled and cannot be disabled by a bad flash — esptool can
   always reconnect.
2. **BOOT + RESET buttons** on the control board.
3. The vendor's rear-button + ULP rescue (only present while stock is running).

`scripts/enter_bootloader.sh` reboots a **stock**-running device into the ROM bootloader via
the vendor RPC and prints the serial port.

## ⚠️ Do not flash `firmware_v0.9.0-sdk.1_merged.bin`

If you go looking for stock images you will find one that looks newer and better than v0.6.1.
**`firmware_v0.9.0-sdk.1_merged.bin` is not for this device.** It comes from Work Louder's
internal "Experiments" releases and is a **Nomad/XYZ build — MicroPython + LVGL, with no
`wl_lumiere`** (the CM2's LED engine) in it at all. Flashing it to a Creator Micro 2 would brick
the pad: it is a full merged image, so it overwrites the bootloader and partition table with a
layout for different hardware, and the result does not present a vendor RPC to talk back to.

Recovery would then depend entirely on ROM-level access (USB-Serial-JTAG or the BOOT/RESET
buttons, both listed below) rather than anything the running firmware offers. Survivable, but
there is no reason to go there.

Match the image to the device: for a Creator Micro 2 you want the **v0.6.x CM2 merged image**
(sha256 `c0d288d5e709cbd7c3f5e4e11e57e26dd1e07e6d83c513e84a9f19d08039794b` for v0.6.1, the one
all the analysis in [`REVERSE-ENGINEERING.md`](REVERSE-ENGINEERING.md) was done against).

## What has never been backed up: the `nvs` partition — a live risk

`scripts/dump_flash.sh` does a chunked full-flash backup, and **it has never completed.** It
fails reproducibly at `0x100000` with `Serial data stream stopped`, so the only regions ever
captured are the first 1 MB (which does at least include the partition table at `0x8000`) and two
512 KB chunks. That means:

- **`nvs` (`0x810000`, 128 KB) has no backup at all.** It holds BLE pairing and vendor settings.
- **`fs` (`0x830000`, 2 MB, littlefs) has no image-level backup.** Its *contents* are readable
  individually over the stock RPC's `fs.read` / `fs.chksm` — that's how `keymap.json` was
  verified intact across flash cycles — but that is a file-by-file copy, not a partition image.

Every flashing procedure in this document is app-only (`0x10000`) or a vendor full image that
leaves `nvs`/`fs` alone precisely *because* they can't be restored if lost. The exposure is real
but bounded: nothing here writes them, and the worst realistic outcome is re-pairing over BLE and
re-creating a vendor keymap.

Fixing `dump_flash.sh` (smaller chunks, a lower baud rate, or retrying the failing read rather
than aborting) would close this properly, and is worth doing before anyone flashes a full image
from a build tree.

## Known issue: status-LED build boot-loops

The firmware revision that added the three PWM status LEDs (LEDC on GPIO 35/45/48) boot-loops
on-device; the prior LED-only build runs fine. Suspected LEDC/GPIO35 init conflict — diagnose
before re-flashing that revision. Recovery is just: restore stock (above), or flash the
known-good LED-only build.
