#!/usr/bin/env python3
"""
Find Xtensa L32R instructions that load a given literal address.

L32R is a 3-byte RI16 instruction:
    byte0 = (at << 4) | 0x1
    byte1..2 = imm16 (little endian)
Target literal address:
    target = ((PC + 3) & ~3) + sign_extend18(imm16 << 2)
The offset is always negative (literals precede the instruction).

usage: find_l32r.py <literal_va_hex>
"""
import os
import struct
import sys

MERGED = os.environ.get("LM_VENDOR_FW", "")
if not MERGED or not os.path.isfile(MERGED):
    raise SystemExit(
        "Set LM_VENDOR_FW to a vendor firmware image, e.g.\n"
        "  LM_VENDOR_FW=~/path/firmware_v0.6.1_merged.bin python3 " + os.path.basename(__file__) + "\n"
        "This repo deliberately ships no vendor firmware — see tools/README.md.")
APP_OFF = 0x10000

data = open(MERGED, "rb").read()
app = data[APP_OFF:]
segs = []
off = 24
for _ in range(app[1]):
    load, ln = struct.unpack("<II", app[off:off + 8])
    off += 8
    segs.append((load, APP_OFF + off, ln))
    off += ln

CODE = [(l, f, n) for l, f, n in segs
        if 0x42000000 <= l < 0x44000000 or 0x40370000 <= l < 0x403E0000]


def sign18(v):
    return v - (1 << 18) if v & (1 << 17) else v


def scan(target):
    hits = []
    for load, fo, ln in CODE:
        blob = data[fo:fo + ln]
        for i in range(len(blob) - 3):
            if (blob[i] & 0x0F) != 0x01:
                continue
            at = blob[i] >> 4
            imm16 = blob[i + 1] | (blob[i + 2] << 8)
            pc = load + i
            off18 = sign18((imm16 << 2) & 0x3FFFF)
            # literals are at lower addresses: the encoded offset is negative
            tgt = ((pc + 3) & ~3) + (off18 if off18 < 0 else off18 - (1 << 18))
            if tgt == target:
                hits.append((pc, at, imm16))
    return hits


if __name__ == "__main__":
    tgt = int(sys.argv[1], 16)
    hits = scan(tgt)
    print(f"L32R instructions loading {tgt:#x}: {len(hits)}")
    for pc, at, imm in hits:
        print(f"  PC {pc:#010x}   l32r a{at}, {tgt:#x}   (imm16={imm:#06x})")
