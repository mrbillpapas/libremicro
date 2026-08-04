# LibreMicro firmware

ESP-IDF custom firmware for the Creator Micro 2 (ESP32-S3-WROOM-1 N16R8). Drives the 13
per-key LEDs, 8 underglow LEDs, and 3 status LEDs, and exposes a serial command API
(`docs/PROTOCOL.md`).

## Build

Needs [PlatformIO](https://platformio.org/) (it fetches ESP-IDF 5.5.x and the Xtensa
toolchain automatically):

```bash
pio run
```

Key config (`sdkconfig.defaults`):
- `CONFIG_SPIRAM=n` — **required**; octal PSRAM would claim GPIO 36/37, the LED power rail.
- `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y` — console + command port on USB-Serial-JTAG, always
  present so esptool can always reconnect.
- Custom partition table (`partitions.csv`) matching the vendor layout, so the app lands in the
  `factory` slot and `nvs`/`fs` are preserved.

## Flash (app-only, preserves vendor nvs + fs)

```bash
P=$(ls /dev/cu.usbmodem*)
esptool --port $P write-flash 0x10000 .pio/build/cm2/firmware.bin
```

See `docs/RECOVERY.md` for restore and recovery.

## What it does at boot

1. Print inherited hold/GPIO registers (`dump`).
2. Release battery-backed pad holds (else GPIO writes are ignored — see `docs/HARDWARE.md`).
3. Drive the LED power rail (GPIO 36/37/38 high).
4. Init both addressable strips + the 3 PWM status LEDs.
5. Run a startup rainbow, then listen for serial commands.

> Note: the current revision blinks the 3 status LEDs at boot and has been observed to
> boot-loop on-device (suspected LEDC/GPIO35 init). See `docs/RECOVERY.md`.

## Source

Single translation unit: `src/main.c`. `src/idf_component.yml` pulls Espressif's `led_strip`.
