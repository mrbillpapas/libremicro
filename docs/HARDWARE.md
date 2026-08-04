# Hardware & pin map

**MCU:** ESP32-S3-WROOM-1, **N16R8** (16 MB flash, 8 MB octal PSRAM), Xtensa LX7 dual-core.
All eFuses ship **unlocked**: no Secure Boot, no flash encryption, USB-Serial-JTAG enabled,
JTAG available. Custom firmware boots without circumventing any protection.

**Inputs:** 13 keys in a `[2,4,4,3]` layout, rotary encoder (rotate + press), radial joystick,
capacitive touch pad, rear button.

## LEDs

| Zone | Data GPIO | Count | SPI bus | Type |
|---|---|---|---|---|
| Per-key | **GPIO 7** | 13 | SPI2_HOST | WS2812, GRB |
| Underglow | **GPIO 6** | 8 | SPI3_HOST | WS2812, GRB |
| Status ("touch") LEDs | **GPIO 35, 45, 48** | 3 | — | single-colour, LEDC PWM (8-bit, 5 kHz) |

Driven with Espressif's `led_strip` component, SPI backend. Stock is byte-identical upstream
`led_strip` v3.0.x; a v2.5.5 build emits the same WS2812 waveform.

### Physical geometry

Needed for spatial lighting effects (gradients, ripples, ring chases) and for the web UI's
layout view.

- **Key caps:** 4 rows of **2, 4, 4, 3** = 13. This matches the 4×4 scanned matrix with 3 of 16
  slots unpopulated (2 missing in the 2-key row, 1 in the 3-key row).
- **Underglow:** a **3×3 grid with no centre LED** = 8, i.e. a ring of positions
  `(0,0) (1,0) (2,0) / (0,1) — (2,1) / (0,2) (1,2) (2,2)`.

### Open question: strip index → physical position (UNVERIFIED)

Three separate mappings are **not yet confirmed** and must not be guessed at in code:

1. Which of the 4 matrix columns are populated in the 2-key row and the 3-key row.
2. Which physical cap each of the 13 per-key strip indices (GPIO 7, order 0→12) lights — strip
   wiring order need not match matrix index order.
3. Which of the 8 ring positions each underglow strip index (GPIO 6, order 0→7) lights, and
   which direction the ring runs.

All three are cheap to settle empirically: light one LED at a time and look at the pad. The
Phase 1 "identify sweep" in [`ROADMAP.md`](ROADMAP.md) exists for exactly this. Keep the
resulting mapping in **config, not source**, so correcting it is a data change rather than a
code change — and so a future hardware revision with different wiring is a config swap.

## The LED power rail (the thing that made custom firmware stay dark)

The addressable LEDs are powered through a switched top-board rail on **GPIO 36 / 37 / 38**.
`GPIO 36` is the addressable-LED VDD enable (active-high). Stock drives `37=1, 36=0, 38=1` at
boot and only raises `36` to 1 when it actually lights pixels. **Every earlier custom attempt
never drove GPIO 36, so the LEDs had correct data but no power.**

Two more gotchas, both required for custom firmware to light anything:

1. **Battery-backed pad holds.** Stock's power-off latches all digital pads via
   `gpio_deep_sleep_hold_en()` into the RTC domain (`RTC_CNTL_DIG_ISO_REG` = `0x60008094`
   bit 11 autohold; `RTC_CNTL_DIG_PAD_HOLD_REG` = `0x600080dc`). Those survive a flash/reset,
   so `gpio_set_level` is silently ignored until released. Firmware must release holds at boot
   (`gpio_deep_sleep_hold_dis()`, `gpio_hold_dis()` on the relevant pins) — exactly what stock's
   own `app_main` does first thing.
2. **Octal PSRAM claims GPIO 33–37.** On the N16R8, enabling octal PSRAM maps GPIO 36/37 to
   SPIIO/DQS and they become undrivable. Build with **`CONFIG_SPIRAM=n`** (stock runs PSRAM-off
   for this reason). See `firmware/sdkconfig.defaults`.

The firmware's boot sequence is therefore: release holds → drive 36/37/38 high → init strips →
refresh. It also prints the inherited hold/GPIO registers over serial (`dump` command) so the
state is visible on-device.

## Key matrix (reverse-engineered, verified)

Scanned **4×4 matrix**, 13 of 16 slots populated.

- **Rows** (push-pull outputs, active-high strobes, one high at a time): **GPIO 46, 17, 40, 47**
- **Cols** (inputs, internal pull-down, ANYEDGE interrupt, read HIGH when pressed): **GPIO 13, 5, 21, 1**
- **Key index = 4·row + col** (row-major, 0..15; 3 slots unused).

Scan: drive all rows low, then strobe each row high (~10 µs settle) and read the 4 columns;
time-based debounce. An any-column edge wakes the scan task.

## Other inputs — **provisional, NOT yet re-verified**

These came from a side decode and one analysis pass crashed before confirming them. Treat as
TODO and re-verify against the disassembly before relying on them in firmware:

| Signal | Provisional GPIO | Notes |
|---|---|---|
| Touch pad (`PIN_TOUCH_OUT_L`) | GPIO 2 | Digital active-low interrupt from an external touch IC (not the ESP32 touch peripheral) |
| Encoder A / B / switch | GPIO 12 (+ B, switch TBD) | ANYEDGE; quadrature |
| Rear button | GPIO 14 | glitch-filtered; **conflicts** with GPIO 2 also being cited as the ext0 wake pin — resolve this |

## Other known pins

| Function | GPIO |
|---|---|
| I²C SDA / SCL (MAX77972 charger/fuel-gauge) | 8 / 9 |
| Charge-enable | 44 |
| USB D- / D+ | 19 / 20 |
| Boot strapping | 0 |

## Flash layout (vendor partition table, kept intact)

```
factory  0x010000  8192K  app   <- custom app flashes here (app-only)
nvs      0x810000   128K  data  <- BLE pairing + settings (preserved)
fs       0x830000  2048K  data  <- littlefs: keymap.json etc. (preserved)
coredump 0xA30000    64K  data
```

Flashing only `0x10000` preserves `nvs` and `fs`. See `docs/RECOVERY.md`.
