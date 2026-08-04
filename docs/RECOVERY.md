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

## Known issue: status-LED build boot-loops

The firmware revision that added the three PWM status LEDs (LEDC on GPIO 35/45/48) boot-loops
on-device; the prior LED-only build runs fine. Suspected LEDC/GPIO35 init conflict — diagnose
before re-flashing that revision. Recovery is just: restore stock (above), or flash the
known-good LED-only build.
