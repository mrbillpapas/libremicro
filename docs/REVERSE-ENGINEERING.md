# Reverse-engineering notes

How the hardware facts in `docs/HARDWARE.md` and the protocol in `docs/PROTOCOL.md` were
recovered, and how to reproduce the analysis. None of the vendor material referenced here is
committed to this repo.

## Sources

- **Vendor firmware images** (from `worklouder/cm-v2-fw-releases`, public). Disassembled to
  recover the LED driver config, power-rail GPIOs, pad-hold logic, and the input pin map.
- **`@worklouder/wl-device-kit`** — the vendor SDK ships *unpacked with source maps* inside the
  Input desktop app, so the TypeScript RPC client was recoverable. This documented the HID
  transport framing and RPC method surface. (Those recovered files are vendor IP and are kept
  private, not in this repo.)

## Toolkit (`tools/`)

Python helpers over Espressif's `xtensa-esp-elf-objdump` (rizin and capstone both lack a
working Xtensa disassembler — use the ESP toolchain):

| Script | Purpose |
|---|---|
| `map_image.py` | Parse the ESP32-S3 app image out of a merged flash dump; build the VA→file map |
| `disasm.py <va> <len>` | Disassemble a virtual-address range |
| `find_l32r.py <literal_va>` | Find the `l32r` instructions that load a given literal (how you find code that references a string/constant) |
| `analyze_fw.py`, `deep_scan.py`, `dump_strings.py` | String/structure scans for RPC methods, LED tokens, module map |
| `find_transport.py`, `extract_sources.py` | Locate the HID transport in the app; recover TS from the source map |
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

## Method surface of the stock RPC (for the Swift tools)

The `host/swift/` tools talk to the **stock** firmware's vendor HID RPC (usage page `0xFF00`,
report ID 6, channel 2 = RPC). Useful for probing a stock device and for restore workflows.
Methods include `sys.*`, `fs.*`, `lights.preview`, `kb.*`, `host.focused_app`. `lights.preview`
is the only vendor colour path and only does whole-zone colours — which is why custom firmware
exists.
