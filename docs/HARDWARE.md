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

### Physical geometry — CONFIRMED from the faceplate

Everything sits on one **4×4 grid**. 13 of the 16 slots are key switches; the other three are
taken by non-key controls, which is exactly why the short rows sit where they do:

```
row 0:  [encoder]   key        key        [joystick]
row 1:   key        key        key         key
row 2:   key        key        key         key
row 3:  [touch]    (----- wide cap -----)  key
                    two switches, one cap
```

- **Key switches:** 4 rows of **2, 4, 4, 3** = 13, occupying grid columns `(1,2)`,
  `(0,1,2,3)`, `(0,1,2,3)`, `(1,2,3)`. So the 2-key row is centred and the 3-key row is
  *right*-aligned — not a symmetric layout.
- **13 switches but only 12 keycaps.** The wide cap in the bottom row covers **two
  independent switches with two independent LEDs**. A user can't reliably choose which half
  they press, so binding the two halves to different actions is a footgun — treat them as one
  control that happens to have two LEDs (a two-pixel gradient across one cap is the nice use
  for it).
- **Non-key controls:** rotary encoder at `(0,0)`, radial joystick at `(0,3)`, capacitive
  touch pad at `(3,0)`. The three PWM status LEDs sit beside the touch pad at bottom-left.
- **Underglow:** a **3×3 grid with no centre LED** = 8, all **the same physical size**, evenly
  spaced around the square — three across the top, one at each side midpoint, three across the
  bottom.

### Strip index → physical position — CONFIRMED by identify sweep

Both LED chains are wired as a **serpentine starting at the bottom-right**. Confirmed by
lighting one pixel at a time on real hardware; every consecutive index turned out to be
physically adjacent, which is exactly what real strip wiring looks like and is strong
evidence the reading is right.

**Per-key chain (GPIO 7)** — strip index by physical position, left to right:

| Row | Strip indices (left → right) |
|---|---|
| 0 (top)    | `11 12` (grid cols 1–2) |
| 1          | `10 9 8 7` |
| 2          | `6 5 4 3`  → i.e. `3 4 5 6` left-to-right |
| 3 (bottom) | `2 1 0` (grid cols 1–3) |

So it snakes upward: row 3 right-to-left, row 2 left-to-right, row 1 right-to-left, row 0
left-to-right. **Strip index 0 is the bottom-right key, not the top-left.**

**Underglow chain (GPIO 6)** — also starts bottom-right, consistent with a single wiring
entry point, and runs continuously around the ring:

```
0:(2,2) → 1:(1,2) → 2:(0,2) → 3:(0,1) → 4:(0,0) → 5:(1,0) → 6:(2,0) → 7:(2,1)
   bottom-right → bottom-mid → bottom-left → mid-left → top-left → top-mid → top-right → mid-right
```

This is a property of the board, so **every Creator Micro 2 is wired identically**. It
therefore ships as a **source default** (`DEFAULT_KEY_POSITIONS` /
`DEFAULT_UNDERGLOW_POSITIONS` in `host/daemon/libremicro/layout.py`) rather than something
each owner rediscovers — a new user gets correct spatial effects with an empty config.
`layout.key_positions` / `layout.underglow_positions` still override it, for a future
hardware revision or a unit that turns out to differ; setting `layout.verified: false`
alongside an override is what re-enables the "unverified" warning.

### Still open: matrix column order

One mapping remains, and it only matters once input events exist (Phase 2): whether the
matrix column order (GPIO 13, 5, 21, 1) runs left-to-right physically. The grid above is
*physical* geometry; the matrix→physical column mapping is independent of it and cannot be
determined from the LEDs.

The canonical machine-readable form of all of the above is
`host/daemon/libremicro/layout.py` (`KEY_GRID_COLS`, `SHARED_KEYCAPS`, `FEATURES`,
`UNDERGLOW_RING`, `DEFAULT_KEY_POSITIONS`, `DEFAULT_UNDERGLOW_POSITIONS`).

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

## Other inputs — resolved from the vendor firmware

Full evidence per signal in [`PIN-VERIFICATION.md`](PIN-VERIFICATION.md); each pin is attested
3–5 independent ways within stock v0.6.1.

| Signal | GPIO | Polarity | Confidence |
|---|---|---|---|
| Touch pad (`PIN_TOUCH_OUT_L`) | **14** | active **high** | pin: very high; polarity: medium-high |
| Encoder A | **12** | quadrature | very high |
| Encoder B | **11** | quadrature | very high |
| Encoder switch | **4** | active low | very high |
| Rear button | **2** | active low | very high |
| USB detect | **42** | active low | high (newly found) |

**The old provisional table had touch and rear swapped**, and that swap was the entire
"GPIO 2 is cited as both the touch input and the ext0 wake pin" conflict. There was never a
conflict: GPIO 2 is the rear button, and stock does use it as the ext0 wake pin.

All six inputs are `INPUT` + `ANYEDGE` with **both internal pulls disabled** (the board has
external pulls), and stock puts a fixed-width glitch filter on every one of them, not just the
rear button.

Two things still unproven, both needing one hardware read rather than more analysis:

- **Touch polarity.** The vendor ISR passes the raw level with no inversion, unlike the rear
  button and encoder switch in the same file which both compute `(level == 0)`. So active-high
  is the reading, and the `_L` suffix is probably *Left* (the pad sits at grid `(3,0)`) rather
  than *active Low*. If touch fires when nothing is touching, flip `LM_TOUCH_ACTIVE_HIGH`.
- **Which rotation is clockwise.** Not determinable from firmware — it depends on PCB wiring.
  If the dial feels backwards, swap `LM_PIN_ENC_A` and `LM_PIN_ENC_B`.

### Two hazards on GPIO 2

The `{"rescue":"rear_button_via_ulp"}` string stock reports is real and decoded: armed by the
`sys.bootloader` RPC, it does `rtc_gpio_hold_en(2)` and starts a 446-byte ULP-RISCV watcher at
250 ms period that forces `SW_SYS_RST` on a rear-button press. Consequences for custom firmware:

1. **GPIO 2 can arrive under an RTC hold**, so reads are meaningless until
   `rtc_gpio_hold_dis()` — the same failure class as the LED rail, on a different pin.
2. **The ULP watcher may still be running**, in which case a rear-button press resets the
   device. If you used `scripts/enter_bootloader.sh` (which calls that RPC) it is probably
   armed right now. Custom firmware does not yet halt it.

Stock also waits ~2 s for the rear button to be released before arming ext0, logging
`rear stuck LOW, ext0 SKIPPED` otherwise. Power management must replicate that guard or the
device will bounce straight back out of deep sleep.

## Other known pins

| Function | GPIO |
|---|---|
| I²C SDA / SCL (MAX77972 charger/fuel-gauge) | 8 / 9 |
| Charge-enable | 44 |
| USB detect | 42 (active low) |
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
