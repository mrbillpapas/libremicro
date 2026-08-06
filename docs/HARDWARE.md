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
| I²C SDA / SCL (MAX77972 charger/fuel-gauge) | 8 / **18** — see below, 9 was probably wrong |
| Charge-enable | 44 |
| USB detect | 42 (active low) |
| USB D- / D+ | 19 / 20 |
| Boot strapping | 0 |

## MAX77972 charger / fuel gauge

### The SCL pin was probably recorded wrong

This doc previously said I²C was GPIO **8 / 9**. Stock's `wl_io::init` calls
`Wire.begin(8, 18, 100000)` — SDA 8, **SCL 18**, 100 kHz. GPIO 8/9 happens to be the
arduino-esp32 *default* I²C pair for the ESP32-S3, i.e. exactly what you'd write down if you
assumed rather than measured, and stock explicitly overrides it. Neither 9 nor 18 is configured
for anything else anywhere in the vendor image.

SCL is an **output**, and the doc and the firmware disagree, so the firmware doesn't pick: it
probes address `0x36` with SCL=18, and on no-ACK returns both pads to inputs and retries with
SCL=9. An address-only probe cannot alter a register, which is what makes trying a disputed
output pin safe. On first flash the boot log says which one answered — that settles it.

### Registers

Two addresses, selected by register number: **`0x36`** for registers ≤ `0xFF` (live values) and
**`0x37`** above (nonvolatile config). Values are 16-bit **LSB-first**. Everything LibreMicro
does is a read from bank 0; nothing writes a register, and nothing touches charge-enable
(GPIO 44) — mis-driving a charger is a real hazard.

| Reg | Meaning | Scale |
|---|---|---|
| `0x07` | RepSOC — reported state of charge | 1/256 % |
| `0x1A` | VCell | 78.125 µV |
| `0x0D7` | Charger details; bits 11:8 are `chg_dtls` | — |
| `0x0FF` | VFSOC | `>> 8` = integer % |
| `0x00` | Status (bit 1 = POR) | — |

Percentage is `(RepSOC + 128) >> 8`. Charging is `chg_dtls <= 2`, which stock's own
`is_charging()` agrees with exactly — those three values are prequal/trickle, fast-charge CC, and
fast-charge CV/top-off. Note `chg_dtls == 8` is *charge done*, so a full battery on the cable
correctly reports **not** charging.

The scale factors are what identify these registers rather than any guess: 0.5 mAh and
0.15625 mA per LSB (both implying a 10 mΩ sense resistor), 1/256 °C, 78.125 µV. That's textbook
ModelGauge m5, and the surrounding map matches it. It is **not** MAX17055's map — RepCap/RepSOC
sit at `0x06`/`0x07` and VCell/Temp/Current at `0x1A`/`0x1B`/`0x1C`, one address up, in the
MAX1733x family style.

Also recovered from stock's 42-register bulk read, unused so far but worth not re-deriving:
`0x06` RepCap and `0x10`/`0x23` FullCapRep/Nom (0.5 mAh), `0x17` Cycles (÷100), `0x19` AvgVCell,
`0x1B` Temp and `0x34` DieTemp (int16, 1/256 °C), `0x1C`/`0x1D` Current/AvgCurrent (int16,
0.15625 mA), `0x29` IChgTerm, `0x2A` charge-voltage target, and flag registers `0x26`–`0x28`,
`0x3A`, `0x3C`–`0x3F`, `0x4D`, `0x51`, `0x52`, `0xB0`, `0xD1`, `0xD3`, `0xD4`, `0xD6`–`0xD8`.
Bank 1 holds nonvolatile charge/JEITA config at `0x1C4`, `0x1C5`, `0x1CA`, `0x1CC`–`0x1CF`,
`0x1D1`, `0x1D5` — deliberately untouched.

## Flash layout (vendor partition table, kept intact)

```
factory  0x010000  8192K  app   <- custom app flashes here (app-only)
nvs      0x810000   128K  data  <- BLE pairing + settings (preserved)
fs       0x830000  2048K  data  <- littlefs: keymap.json etc. (preserved)
coredump 0xA30000    64K  data
```

Flashing only `0x10000` preserves `nvs` and `fs`. See `docs/RECOVERY.md`.

## eFuses — everything ships unlocked

Read off a retail pad with `espefuse summary` (the full dump is kept locally as
`firmware-vendor/efuse_summary.txt`, git-ignored). The headline is that nothing has to be
circumvented to run custom firmware:

```
SECURE_BOOT_EN                    False      <- arbitrary firmware boots
SPI_BOOT_CRYPT_CNT                Disable    <- flash is plaintext
DIS_DOWNLOAD_MODE                 False
DIS_USB_JTAG                      False
DIS_USB_SERIAL_JTAG               False
DIS_USB_OTG_DOWNLOAD_MODE         False
DIS_PAD_JTAG                      False      <- hardware JTAG available
WR_DIS / RD_DIS                   0          <- nothing locked
BLOCK_USR_DATA (BLOCK3)  ...(24 zero bytes)... 03 02 00 00 00 00 01 00
```

Other identity facts from the same dump:

| item | value |
|---|---|
| MAC / serial | `10:20:ba:73:4d:c8` / `1020BA734DC8` (esptool and the USB descriptor agree) |
| Flash | 16 MB, mfr `0x46`, dev `0x4018`, quad, 3.3 V |
| PSRAM | 8 MB, `PSRAM_VENDOR=AP_3v3`, octal — but see `CONFIG_SPIRAM=n` above |
| Chip revision | ESP32-S3 (QFN56) rev v0.2 |

**`BLOCK_USR_DATA` bytes 24/25 and 30** (`03 02` … `01`) are very likely the `vendor`/`variant`
pair that stock's `read_board_info_from_efuse` reads — which matters because the vendor RPC's
`v.oai.*` registration is variant-gated off exactly that (see
[`VENDOR-RPC.md`](VENDOR-RPC.md)). LibreMicro never reads or depends on it.

> **Never run `espefuse burn_*` on this device.** Every burn is one-way, and nothing LibreMicro
> does needs a single fuse changed. The device is already fully open.

## USB identity and the PID family

The pad uses Espressif's vendor ID with Work Louder's own product IDs, all publicly registered
in `espressif/usb-pids` (`allocated-pids.txt`) and cross-checked against the vendor SDK's
`DEVICE_REGISTRY`:

| VID:PID | Device |
|---|---|
| `0x303A:0x8297` | Creator Micro v2, **wired** (BASE) |
| `0x303A:0x8298` | Creator Micro v2 **BLE** — this device |
| `0x303A:0x8360` | "Project 2077" → `DeviceType.CodexMicro` |

Worth knowing that **none of these is the identity you see when LibreMicro is running.** Custom
firmware uses the ESP32-S3's USB-Serial-JTAG console, whose USB identity is fixed in ROM at
`0x303A:0x1001`, vendor string `Espressif` — which is exactly why the vendor app still offers to
reflash a converted pad (see [`RECOVERY.md`](RECOVERY.md)).

## Unidentified: IC1 / IC2 / IC3 beside the LED FFC

Board designators, for anyone with the case open: the key PCB carries `SD1`–`SD13` (per-key
LEDs) and `UD1`–`UD8` (underglow) plus the `J2` FFC labelled **`LED+`**. The control board
(silkscreen **`Wireless V0.3`**) carries the WROOM-1 module, `BOOT` and `RESET` buttons, `S1`,
the MAX77972 charger with `L1` (5R2 inductor), USB-C `J1`, and the battery lead.

Three small ICs — **`IC1`, `IC2`, `IC3`** — sit immediately next to that `J2` LED ribbon
connector and have **never been identified**. No close-up photograph of their markings exists.

They were for a long time the prime suspect for why custom firmware couldn't light an LED, on
the theory that they were level shifters or buffers with an `OE` that nothing was asserting.
**That turned out not to be the cause** — the real answer was the GPIO 36 power rail plus the
battery-backed pad holds, both documented above. So this is no longer a blocker, just an
unclosed question.

It still matters in one place: a pin-compatible clone board has to decide whether anything sits
in the LED data path between the ESP32-S3 and the strips. The clone design currently assumes a
straight-through connection plus a high-side switch on the rail, which is consistent with
everything observed but not *proven* against these three parts. See the clone-board notes in
`docs/OPEN-PCB.md` (kept local, not published). One good macro photo would settle it.
