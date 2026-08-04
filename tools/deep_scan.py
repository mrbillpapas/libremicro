#!/usr/bin/env python3
"""Deeper hunt for per-key LED control: module map, lighting strings, BLE GATT, LED tokens."""
import os
import re

FW = os.environ.get("LM_VENDOR_FW", "")
if not FW or not os.path.isfile(FW):
    raise SystemExit(
        "Set LM_VENDOR_FW to a vendor firmware image, e.g.\n"
        "  LM_VENDOR_FW=~/path/firmware_v0.6.1_merged.bin python3 " + os.path.basename(__file__) + "\n"
        "This repo deliberately ships no vendor firmware — see tools/README.md.")
data = open(FW, "rb").read()
STR = re.compile(rb"[\x20-\x7e]{4,}")

print("=" * 78)
print("SOURCE FILE MAP (reveals module structure)")
print("=" * 78)
files = sorted(set(m.group(0).decode() for m in re.finditer(
    rb"src/[A-Za-z0-9_/]+\.(?:cpp|c|h|hpp)", data)))
for f in files:
    print("  ", f)

print("\n" + "=" * 78)
print("BLE / GATT UUIDs and service strings")
print("=" * 78)
uuids = sorted(set(m.group(0).decode() for m in re.finditer(
    rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", data)))
for u in uuids:
    print("  ", u)
for pat in (rb"NimBLE[A-Za-z]*", rb"[Gg][Aa][Tt][Tt][A-Za-z_]*", rb"characteristic",
            rb"notify", rb"BLE[A-Z][A-Za-z]*"):
    hits = sorted(set(m.group(0).decode() for m in re.finditer(pat, data)))
    if hits:
        print(f"  {pat.decode():<22} {hits[:10]}")

print("\n" + "=" * 78)
print("LED / pixel / frame / matrix tokens")
print("=" * 78)
for pat in [rb"\bled[a-z_]{0,14}", rb"\bLED[A-Za-z_]{0,14}", rb"pixel[a-z_]{0,10}",
            rb"\bpx[a-z_]{0,10}", rb"frame[a-z_]{0,12}", rb"matrix[a-z_]{0,10}",
            rb"\bidx\b", rb"\bindex\b", rb"colors", rb"\brgb[a-z_]{0,12}",
            rb"num_keys", rb"key_count", rb"nkeys", rb"\bws2812[a-z0-9_]*",
            rb"\bsk68[0-9a-z]*", rb"strip[a-z_]{0,10}", rb"zone[a-z_]{0,10}"]:
    hits = sorted(set(m.group(0).decode() for m in re.finditer(pat, data, re.I)))
    if hits:
        print(f"  {pat.decode():<24} -> {hits[:14]}")

print("\n" + "=" * 78)
print("ALL strings around every lights/oai source module")
print("=" * 78)
for mod in (b"src/lights/", b"src/oai/"):
    for m in re.finditer(re.escape(mod), data):
        off = m.start()
        name = STR.match(data, off)
        lo, hi = max(0, off - 2200), min(len(data), off + 600)
        print(f"\n--- near {name.group(0).decode() if name else mod.decode()} @ 0x{off:X}")
        for s in STR.finditer(data[lo:hi]):
            t = s.group(0).decode()
            if len(t) < 60:            # skip long assert/log noise
                print(f"    0x{lo + s.start():07X}  {t}")
