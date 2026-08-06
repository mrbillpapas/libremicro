# Reverse-engineering toolkit

Python helpers used to analyze the stock Creator Micro 2 firmware. See
`docs/REVERSE-ENGINEERING.md` for the full story and per-script descriptions.

## Prerequisite: Xtensa toolchain (not committed)

These scripts shell out to Espressif's `xtensa-esp-elf-objdump`. Install the Xtensa ESP
toolchain (rizin/capstone lack a working Xtensa disassembler) — e.g. it comes with
PlatformIO/ESP-IDF — and make `xtensa-esp-elf-objdump` available on `PATH` or point the
scripts at it. The toolchain (~600 MB) is deliberately not part of this repo.

## Vendor firmware (not committed)

The analysis operates on the stock vendor firmware image, which is not shipped here — obtain
it from `worklouder/cm-v2-fw-releases` (see `docs/RECOVERY.md`) and keep it locally.

## Quick reference

```bash
python3 map_image.py                        # segment/VA map of the app image
python3 disasm.py 0x42000020 0x1000         # disassemble a VA range
python3 find_l32r.py 0x3c0e8918             # find code loading a literal (e.g. a string VA)
```

## Vendor SDK sources (not committed)

`extract_sources.py` recovers the vendor SDK's original TypeScript from the source maps that ship
inside Work Louder's Input app. Point `LM_INPUT_APP` at the bundle; output lands in
`extracted-src/` at the repo root, which is git-ignored because that material is vendor IP.

```bash
LM_INPUT_APP=/Applications/input.app python3 extract_sources.py
```

