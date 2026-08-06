# Reverse-engineering notes

How the hardware facts in `docs/HARDWARE.md` and the protocol in `docs/PROTOCOL.md` were
recovered, and how to reproduce the analysis. None of the vendor material referenced here is
committed to this repo.

## Sources

- **Vendor firmware images** (from `worklouder/cm-v2-fw-releases`, public). Disassembled to
  recover the LED driver config, power-rail GPIOs, pad-hold logic, and the input pin map.
- **`@worklouder/wl-device-kit`** — the vendor SDK ships *unpacked with source maps* inside the
  Input desktop app, so the TypeScript RPC client was recoverable. This documented the HID
  transport framing and RPC method surface — see [`VENDOR-RPC.md`](VENDOR-RPC.md). (Those
  recovered files are vendor IP and are kept private, not in this repo; regenerate them with
  `tools/extract_sources.py`.)

The original working notes from the reverse-engineering phase, before any of it was reorganised
into these docs, are preserved verbatim at
[`archive/CM2-HANDOFF-2026-08-03.md`](archive/CM2-HANDOFF-2026-08-03.md). It is a snapshot, not a
maintained document — where it disagrees with the rest of `docs/`, the rest of `docs/` is right.
It is kept because it records the *order* things were discovered in and the dead ends in their
original form.

## Toolkit (`tools/`)

Python helpers over Espressif's `xtensa-esp-elf-objdump` (rizin and capstone both lack a
working Xtensa disassembler — use the ESP toolchain):

| Script | Purpose |
|---|---|
| `map_image.py` | Parse the ESP32-S3 app image out of a merged flash dump; build the VA→file map |
| `disasm.py <va> <len>` | Disassemble a virtual-address range |
| `find_l32r.py <literal_va>` | Find the `l32r` instructions that load a given literal (how you find code that references a string/constant) |
| `analyze_fw.py`, `deep_scan.py`, `dump_strings.py` | String/structure scans for RPC methods, LED tokens, module map |
| `find_transport.py` | Locate the HID transport in the vendor app |
| `extract_sources.py` | Recover the vendor SDK's original TypeScript from the source maps shipped inside `input.app` (`LM_INPUT_APP`) → `extracted-src/`, git-ignored |
| `enumerate.py`, `rpc_probe.py` | Read-only HID enumeration / RPC probing of a live device |

The **Xtensa toolchain is not committed** (~600 MB). Install Espressif's `xtensa-esp-elf`
(e.g. via PlatformIO/ESP-IDF) and point the scripts at its `xtensa-esp-elf-objdump`.

## How the key facts were found

- **LED topology / power rail:** decoded the two `led_strip_new_spi_device` call sites (keys
  GPIO7/13/SPI2, underglow GPIO6/8/SPI3) and `wl_io::init_top_board_power_gpio` (GPIO 36/37/38;
  GPIO 36 = the VDD enable raised only when pixels are lit). Confirmed on-device by reading the
  live GPIO/hold registers back over the firmware's `dump` command.
- **Pad holds:** the vendor OFF recipe calls `gpio_deep_sleep_hold_en()`; the corresponding
  register writes (`0x60008094`, `0x600080dc`) identify it as the battery-backed autohold that
  survives reflash. Stock's `app_main` releases them at boot; our firmware replicates that.
- **Key matrix:** `wl_keymatrix::setup_gpio()` configures rows as outputs and cols as
  pulled-down ANYEDGE inputs; the row/col pin arrays live in DROM. Index = 4·row + col.
  Adversarially re-verified.

## The v0.6.1 app image: VA → file-offset map

`map_image.py` prints this, but having it written down means you can sanity-check a disassembly
without re-running anything. Offsets are **into the merged flash image**, whose app partition
starts at `0x10000`:

| seg | load addr | size | file off (merged) | region |
|---|---|---|---|---|
| 0 | `3c0e0020` | 1,244,752 | `0x10020` | DROM (rodata / strings) |
| 1 | `3fca2d00` | 416 | `0x13fe78` | DRAM |
| 2 | `42000020` | 872,240 | `0x140020` | IROM (code) |
| 3 | `3fca2ea0` | 28,484 | `0x214f58` | DRAM |
| 4 | `40374000` | 126,076 | `0x21bea4` | IRAM |
| 5 | `600fe000` | 264 | `0x23ab28` | RTC |

The full IROM disassembly is 325,762 lines and is regenerated rather than stored:

```bash
LM_VENDOR_FW=<merged.bin> python3 tools/disasm.py 0x42000020 0xD4EF0 > /tmp/irom.asm
```

Practically all of the work happened in segments 0 and 2: find a string in DROM, find the
literal-pool reference to its VA (`find_l32r.py`), and that lands you in the IROM function that
uses it.

## Attempts that failed — do not repeat these

Before the real cause was found (the GPIO 36 LED rail plus the battery-backed pad holds, see
[`HARDWARE.md`](HARDWARE.md)), four serious attempts were made to light an LED from custom
firmware. All four built, flashed hash-verified, booted, and logged every code path executing.
**None of them lit a single LED.** They are recorded because each one looks like an obvious thing
to try:

| # | attempt | result |
|---|---|---|
| 1 | Arduino + `Adafruit_NeoPixel@1.12.3`, RMT backend, GPIO 7 (13 px) + GPIO 6 (8 px) | ran, logged all phases, **no light**. RMT NeoPixel silently no-ops here — it reports nothing wrong |
| 2 | 26-pin GPIO "power enable" sweep, HIGH then LOW (1,2,3,4,5,8–18,21,38–42,45–48) | **nothing, including the control stage** — and the sweep excluded 36/37 on the assumption they were octal-PSRAM pins, which is precisely why it missed |
| 3 | ESP-IDF `led_strip@2.5.5`, **SPI** backend, exact vendor config (SPI2/GPIO7/13, SPI3/GPIO6/8, WS2812, GRB, `with_dma=true`, default clk) | both strips init **without error**, **no light** |
| 4 | Matrix of SPI×WS2812, SPI×SK6812, RMT×WS2812, RMT×SK6812, plus an enable-pin sweep of 43/44/46/0 | all init OK, **nothing** |

The decisive control throughout: stock v0.6.1 lighting worked perfectly *after* a teardown and
reassembly. So the `J2` ribbon was seated, GPIO 7/6 were right, and the LED rail was reachable —
which is what eventually forced the search back onto power state rather than the driver.

The other lesson is about attempt 2's dark control stage, which was read at the time as proof
that *no* power-enable GPIO existed. It was actually proof of the **pad holds**: the sweep's
`gpio_set_level` calls were being silently ignored on held pads, so of course nothing changed.
A negative result from a sweep means nothing if the pads are held.

## Wrong assumptions made along the way

Corrected — don't re-derive any of these:

| assumption | reality |
|---|---|
| "The LEDs are on an I²C matrix driver" | Single-wire `led_strip` (SPI backend). The I²C bus is the MAX77972 charger/fuel gauge |
| "A switchable rail gates the LEDs" — over-read from `PM recipe: ENTER STANDBY -> cut current to top-board domains` | That's a *standby* optimisation, not a boot gate. Cost two rounds of testing. (There *is* a rail — GPIO 36 — but this string was not the evidence for it) |
| "Maybe only ~7 LEDs exist, so per-key is impossible" | 13 + 8, one per key. Per-key RGB was always physically real |
| "`v.oai.rgbcfg` returning `ok:1` means it worked" | It acks literally everything, including `params:null`. See [`VENDOR-RPC.md`](VENDOR-RPC.md) |
| "rizin has an Xtensa disassembler" | It does **not**. Use Espressif's `xtensa-esp-elf-objdump` |
| "capstone 5.0.7 has Xtensa" | It does not, either |

## External resources

| Resource | What's in it |
|---|---|
| `worklouder/cm-v2-fw-releases` | **Public.** 30 releases of CM2 firmware — this is where you get a stock image to restore from, and what all the disassembly above was done against |
| `worklouder/input-releases-internal` | "Experiments." Includes an `sdk-alpha-0.1` release carrying MicroPython firmware for *other* products — the source of the confusable v0.9.0 image warned about in [`RECOVERY.md`](RECOVERY.md) |
| `worklouder/input-linux` | Unofficial community port of the Input desktop app. **Never examined.** Plausibly the highest-value unexplored lead here: an independent implementation of the same HID RPC would either corroborate or correct [`VENDOR-RPC.md`](VENDOR-RPC.md) for free |
| `espressif/usb-pids` | `allocated-pids.txt` maps every Work Louder PID — see the PID table in [`HARDWARE.md`](HARDWARE.md) |

## Method surface of the stock RPC (for the Swift tools)

The `host/swift/` tools talk to the **stock** firmware's vendor HID RPC (usage page `0xFF00`,
report ID 6, channel 2 = RPC). Useful for probing a stock device and for restore workflows.
Methods include `sys.*`, `fs.*`, `lights.preview`, `kb.*`, `host.focused_app`. `lights.preview`
is the only vendor colour path and only does whole-zone colours — which is why custom firmware
exists.

The framing, the full method enumeration, the macOS/IOKit gotchas, and the falsification of
`v.oai.rgbcfg` as a per-key surface are all in [`VENDOR-RPC.md`](VENDOR-RPC.md).
