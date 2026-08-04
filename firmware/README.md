# LibreMicro firmware

ESP-IDF custom firmware for the Creator Micro 2 (ESP32-S3-WROOM-1 N16R8). Drives the 13
per-key LEDs, 8 underglow LEDs, and 3 status LEDs, and exposes a serial command API
(`docs/PROTOCOL.md`).

## v2 — "thin transport"

v2 makes the pad an *input* device as well as a light: it emits one event line per key
transition on the same serial link that carries LED commands. Every v1 command still works
unchanged, so a host that doesn't know about v2 sees no difference.

**What v2 adds**

| | |
|---|---|
| Key matrix scanning | 4×4 scan on the verified pin map, time-based debounce, emits `key <i> down` / `key <i> up` |
| Batched frame writes | `kf <rrggbb>×13` and `uf <rrggbb>×8` — a whole zone per line, **one** strip refresh |
| `mscan` | prints the raw matrix bitmap — the tool for confirming the index mapping |
| `ver` | reports feature support so the host can detect batching instead of guessing |
| Guarded inputs | encoder / touch / rear, compiled **out** by default (see below) |

Events and acks share one link and are told apart by line prefix: acks always begin `ok` or
`err`, events always begin `key` / `enc` / `touch` / `rear`, and diagnostics begin `#` (which
the host ignores). All output goes through one mutex so a line is never cut in half by the
other writer.

Scanning lives in its own task pinned to core 1, woken by an any-edge interrupt on the
column inputs, so it neither starves nor is starved by the command loop on core 0. While
idle the task sleeps with all rows driven high, so a keypress raises a column and wakes it;
a 25 ms fallback poll covers the one race an edge can't (a key pressed in the instant
between the last scan and re-arming, whose column is already high).

`CONFIG_FREERTOS_HZ=1000` is now required: at the default 100 Hz tick the smallest
`vTaskDelay` is 10 ms, which would turn the 2 ms scan period into a busy-loop.

### Logical key indices, and the one assumption in them

The matrix is 4×4 = 16 raw slots, but only **13 are keys** — the other three hold the rotary
encoder, the joystick and the touch pad. Config, the web UI and the host daemon all speak
**logical** index 0–12, assigned row-major over populated slots only, so logical index is a
*lookup*, not arithmetic on the matrix index. `docs/PROTOCOL.md` requires firmware to do that
translation on-device, and it does:

```
MTX_TO_LOGICAL[16] = { -1,  0,  1, -1,     // row 0: encoder, key, key, joystick
                        2,  3,  4,  5,     // row 1
                        6,  7,  8,  9,     // row 2
                       -1, 10, 11, 12 }    // row 3: touch pad, key, key, key
```

This is verified to agree with `host/daemon/libremicro/layout.py` (`rowcol_to_logical` +
`grid_col`): 13 populated slots, logicals 0–12 each appearing exactly once. Logical 10 and 11
are the two switches under the single wide bottom keycap (`SHARED_KEYCAPS`).

**The assumption:** the *contents* of that table are fact only if the matrix scan order
matches physical order — that column order (GPIO 13, 5, 21, 1) runs left-to-right and row
order (GPIO 46, 17, 40, 47) runs top-to-bottom. `docs/HARDWARE.md` flags the column order as
still open; it cannot be determined from the LEDs, only by pressing real keys. The *pin map*
and the *shape* of the table (which slots are empty, and that logical is row-major over the
rest) are solid — what's unproven is the orientation.

That is why the table is one flat lookup at the top of `src/main.c`: if the orientation turns
out reversed, **that table is the only thing that changes**, and every event the firmware emits
flows through it. Use `mscan` to read the raw bitmap while holding a known key and rebuild it
from what you see. If a slot the table calls unpopulated ever goes active, the firmware says so
once on a `#` line rather than inventing a key index.

### Guarded inputs — encoder, touch pad, rear button

These are behind a compile-time flag that is **off by default**:

```c
#define LM_ENABLE_UNVERIFIED_INPUTS 0
```

`docs/HARDWARE.md` marks all three pin maps provisional — the analysis pass that would have
confirmed them crashed first — and there is an unresolved conflict where **GPIO 2 is cited both
as the touch interrupt and as the ext0 wake pin**. The asymmetry that decides the default: the
matrix pin map is verified, so driving those rows as outputs is known-safe, whereas a wrong map
here could point an output at something that must not be driven and do real physical harm. So
the default build never configures or reads those pins at all, and the safe half of v2 ships
without waiting on the vendor-firmware analysis.

Everything for them is one self-contained block in `src/main.c` with the pin numbers as named
constants in one place. `LM_PIN_ENC_B` and `LM_PIN_ENC_SW` are `-1` — genuinely unknown, rather
than a guessed pin number — and any input whose pins aren't all filled in is skipped at runtime.
Enabling the flag as-is compiles and warns about exactly that. Invariant kept throughout the
block: every pin is `GPIO_MODE_INPUT` and never driven.

To build it once the pins are confirmed:

```bash
PLATFORMIO_BUILD_FLAGS=-DLM_ENABLE_UNVERIFIED_INPUTS=1 pio run
```

### First flash — what a human must check

1. **LEDs still light.** The v1 boot path is unchanged, but v2 adds the eight matrix pads to the
   list of pad holds released at boot. If the pad boots dark, that regressed.
2. **Every key reports, once.** Press each of the 13 keys: exactly one `key <i> down` and one
   `key <i> up` per press, no repeats (debounce) and no missing keys (wake path).
3. **The index orientation.** This is the real unknown. Press the *top-left* key — the leftmost
   of the two in the short top row — and confirm it reports `key 0`. Then the bottom-right key
   should report `key 12`. If they're swapped or transposed, run `mscan` while holding a known
   key and fix `MTX_TO_LOGICAL`.
4. **No `#` warnings.** A `# warn raw matrix slot ...` line means the table's empty slots are
   wrong for this board.
5. **`kf` / `uf` paint the whole zone** and are visibly smoother than per-pixel writes.
6. Confirm nothing regressed in `k`, `u`, `t`, `bright`, `clear`, `demo`, `dump`.

## Build

Needs [PlatformIO](https://platformio.org/) (it fetches ESP-IDF 5.5.x and the Xtensa
toolchain automatically):

```bash
pio run
```

Key config (`sdkconfig.defaults`):
- `CONFIG_SPIRAM=n` — **required**; octal PSRAM would claim GPIO 36/37, the LED power rail.
- `CONFIG_FREERTOS_HZ=1000` — required by the v2 scan task; see above.
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
   v2 adds the eight key-matrix pads to this list: a latched hold on a row pad would make
   `gpio_set_level` silently do nothing and the matrix would read as permanently idle — the
   same failure mode that kept the LED rail dark, on a different pin.
3. Drive the LED power rail (GPIO 36/37/38 high).
4. Init both addressable strips + the 3 PWM status LEDs.
5. Run a startup rainbow.
6. Bring up the serial link, *then* start the matrix scan task — so no event line can be
   emitted before there's a host-visible stream to put it on.

Steps 1–3 are load-bearing and unchanged from v1; that ordering is what makes the LEDs work
at all.

> Note: the current revision blinks the 3 status LEDs at boot and has been observed to
> boot-loop on-device (suspected LEDC/GPIO35 init). See `docs/RECOVERY.md`.

## Source

Single translation unit: `src/main.c`. `src/idf_component.yml` pulls Espressif's `led_strip`.
