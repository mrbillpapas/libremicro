#!/usr/bin/env python3
"""Disassemble a virtual-address range of the CM2 app image with Xtensa objdump.

usage: disasm.py <va_hex> <length> [--raw]
"""
import struct
import subprocess
import sys
import tempfile

MERGED = "/Users/billpapas/AI/worklouder/firmware/firmware_v0.6.1_merged.bin"
OBJDUMP = "/Users/billpapas/AI/worklouder/tools/xtensa-esp-elf/bin/xtensa-esp-elf-objdump"
APP_OFF = 0x10000

data = open(MERGED, "rb").read()
app = data[APP_OFF:]
seg_count = app[1]

segs = []
off = 24
for _ in range(seg_count):
    load, ln = struct.unpack("<II", app[off:off + 8])
    off += 8
    segs.append((load, APP_OFF + off, ln))
    off += ln


def va_to_off(va):
    for load, fo, ln in segs:
        if load <= va < load + ln:
            return fo + (va - load)
    return None


def main():
    va = int(sys.argv[1], 16)
    length = int(sys.argv[2], 0)
    fo = va_to_off(va)
    if fo is None:
        sys.exit(f"VA {va:#x} not in any segment")

    blob = data[fo:fo + length]
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(blob)
        path = f.name

    cmd = [OBJDUMP, "-D", "-b", "binary", "-m", "xtensa",
           f"--adjust-vma={va:#x}", path]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(out.stderr)
    print(out.stdout)


if __name__ == "__main__":
    main()
