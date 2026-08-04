#!/usr/bin/env python3
"""
Parse the ESP32-S3 app image out of the merged flash image and build a
file-offset <-> virtual-address map, so we can find the code that references a
given string and disassemble it.

Merged flash layout (from the on-device partition table):
  0x000000 bootloader | 0x008000 partition table | 0x010000 factory app
"""
import struct
import sys

MERGED = "/Users/billpapas/AI/worklouder/firmware/firmware_v0.6.1_merged.bin"
APP_OFF = 0x10000

data = open(MERGED, "rb").read()
app = data[APP_OFF:]

magic, seg_count, spi_mode, spi_ss = app[0], app[1], app[2], app[3]
entry = struct.unpack("<I", app[4:8])[0]
chip_id = struct.unpack("<H", app[12:14])[0]
assert magic == 0xE9, f"bad app magic {magic:#x}"

print(f"app image: segments={seg_count} entry={entry:#010x} chip_id={chip_id} "
      f"(0x9 = ESP32-S3)")

segs = []          # (load_addr, file_off_in_merged, length)
off = 24
for i in range(seg_count):
    load, ln = struct.unpack("<II", app[off:off + 8])
    off += 8
    segs.append((load, APP_OFF + off, ln))
    off += ln

print(f"\n{'#':<3}{'load addr':>12}{'size':>10}{'merged file off':>17}  region")
for i, (load, fo, ln) in enumerate(segs):
    if 0x3C000000 <= load < 0x3E000000:
        r = "DROM (rodata/strings)"
    elif 0x42000000 <= load < 0x44000000:
        r = "IROM (code)"
    elif 0x3FC80000 <= load < 0x3FD00000:
        r = "DRAM"
    elif 0x40370000 <= load < 0x403E0000:
        r = "IRAM"
    else:
        r = "?"
    print(f"{i:<3}{load:>12x}{ln:>10}{fo:>17x}  {r}")


def off_to_va(file_off):
    for load, fo, ln in segs:
        if fo <= file_off < fo + ln:
            return load + (file_off - fo)
    return None


def va_to_off(va):
    for load, fo, ln in segs:
        if load <= va < load + ln:
            return fo + (va - load)
    return None


if __name__ == "__main__":
    needle = (sys.argv[1] if len(sys.argv) > 1
              else "led_strip_new_spi_device(&strip_cfg, &spi_cfg, &out_handle)")
    nb = needle.encode()
    so = data.find(nb)
    if so < 0:
        sys.exit(f"string not found: {needle!r}")
    sva = off_to_va(so)
    print(f"\nstring {needle!r}\n  file off {so:#x}  VA {sva:#x}"
          if sva else f"\nstring at {so:#x} is outside mapped segments")
    if sva is None:
        sys.exit(1)

    # Xtensa loads constants from literal pools: find 4-byte LE refs to the VA
    pat = struct.pack("<I", sva)
    refs = []
    start = 0
    while True:
        k = data.find(pat, start)
        if k < 0:
            break
        refs.append(k)
        start = k + 1
    print(f"\nliteral-pool references to that VA: {len(refs)}")
    for k in refs:
        print(f"  file off {k:#x}  VA {off_to_va(k)}"
              + (f" ({off_to_va(k):#x})" if off_to_va(k) else ""))
