#!/usr/bin/env python3
"""Dump the string-pool neighbourhood around the v.oai.* RPC methods."""
import os
import re

FW = os.environ.get("LM_VENDOR_FW", "")
if not FW or not os.path.isfile(FW):
    raise SystemExit(
        "Set LM_VENDOR_FW to a vendor firmware image, e.g.\n"
        "  LM_VENDOR_FW=~/path/firmware_v0.6.1_merged.bin python3 " + os.path.basename(__file__) + "\n"
        "This repo deliberately ships no vendor firmware — see tools/README.md.")
data = open(FW, "rb").read()

STR = re.compile(rb"[\x20-\x7e]{3,}")


def strings_in(lo, hi):
    return [(m.start(), m.group(0).decode()) for m in STR.finditer(data[lo:hi])]


targets = [b"v.oai.rgbcfg", b"v.oai.thstatus", b"v.oai.hid", b"v.oai.rad",
           b"lights.preview", b"lights.cpp"]

for t in targets:
    for m in re.finditer(re.escape(t), data):
        off = m.start()
        lo, hi = max(0, off - 1400), min(len(data), off + 1400)
        print(f"\n{'=' * 78}\n{t.decode()} @ 0x{off:X}\n{'=' * 78}")
        for rel, s in strings_in(lo, hi):
            mark = "  <<<<" if t.decode() in s else ""
            print(f"  0x{lo + rel:07X}  {s}{mark}")
