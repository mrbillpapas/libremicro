#!/usr/bin/env python3
"""Search the stock CM2 firmware image for RPC method strings, esp. per-key lighting."""
import os
import re
import sys

FW = os.environ.get("LM_VENDOR_FW", "")
if not FW or not os.path.isfile(FW):
    raise SystemExit(
        "Set LM_VENDOR_FW to a vendor firmware image, e.g.\n"
        "  LM_VENDOR_FW=~/path/firmware_v0.6.1_merged.bin python3 " + os.path.basename(__file__) + "\n"
        "This repo deliberately ships no vendor firmware — see tools/README.md.")
data = open(FW, "rb").read()
print(f"image: {len(data)} bytes\n")

# ESP32 image magic / chip hints
print("=== chip / image hints ===")
for pat, label in [
    (rb"esp32s3", "esp32s3"), (rb"ESP32-S3", "ESP32-S3"),
    (rb"esp32c3", "esp32c3"), (rb"ESP32-C3", "ESP32-C3"),
    (rb"esp32c6", "esp32c6"), (rb"ESP32-C6", "ESP32-C6"),
    (rb"esp-idf", "esp-idf"), (rb"v5\.\d+(\.\d+)?", "idf version-ish"),
]:
    hits = sorted(set(m.group(0) for m in re.finditer(pat, data, re.I)))
    if hits:
        print(f"  {label}: {[h.decode(errors='replace') for h in hits][:6]}")

# Every dotted RPC-looking method name in the binary
print("\n=== dotted method-like strings (namespace.method) ===")
meth = sorted(set(m.group(1).decode() for m in re.finditer(
    rb"\b([a-z][a-z0-9_]{1,12}(?:\.[a-z][a-z0-9_]{1,20}){1,3})\b", data)))
NAMESPACES = ("sys", "device", "fs", "mp", "appmgr", "ui", "lights", "host", "v")
interesting = [m for m in meth if m.split(".")[0] in NAMESPACES]
for m in interesting:
    print("   ", m)

# Specific things we care about
print("\n=== targeted probes ===")
for pat in [rb"v\.oai\.[a-z_]+", rb"rgbcfg", rb"thstatus", rb"lights\.[a-z_]+",
            rb"oai", rb"codex", rb"Codex", rb"per_key", rb"perkey",
            rb"rgb_matrix", rb"set_key", rb"key_color", rb"keycolor"]:
    hits = sorted(set(m.group(0) for m in re.finditer(pat, data)))
    label = pat.decode()
    print(f"  {label:<20} -> {len(hits)}  {[h.decode(errors='replace') for h in hits][:8]}")

# Lighting effect names we know from the SDK enum — confirms the lighting model
print("\n=== SDK LightingEffect enum members present? ===")
for name in (b"off", b"solid", b"snake", b"rainbow", b"breath", b"gradient"):
    print(f"  {name.decode():<10} {len(re.findall(rb'\b' + name + rb'\b', data))}")
