#!/usr/bin/env python3
"""Locate Input.app's HID transport / RPC framing implementation in app.asar."""
import re

ASAR = "/Applications/input.app/Contents/Resources/app.asar"
data = open(ASAR, "rb").read()

TERMS = [
    rb"sendRequest",
    rb"node-hid",
    rb"usagePage",
    rb"usage_page",
    rb"65280",
    rb"0xff00",
    rb"0xFF00",
    rb"reportId",
    rb"report_id",
    rb"sendReport",
    rb"sendFeatureReport",
    rb"getFeatureReport",
    rb"requestDevice",
    rb"HIDDevice",
    rb"inputreport",
    rb"oninputreport",
]

print("=== term counts ===")
locs = {}
for t in TERMS:
    hits = [m.start() for m in re.finditer(t, data)]
    locs[t] = hits
    print(f"{t.decode():<20} {len(hits)}")

# Show context around the most structurally interesting terms
for t in (rb"sendRequest", rb"oninputreport", rb"sendReport", rb"usagePage"):
    for off in locs.get(t, [])[:2]:
        s, e = max(0, off - 700), min(len(data), off + 900)
        print(f"\n=========== {t.decode()} @ {off} ===========")
        print(data[s:e].decode("utf-8", errors="replace"))
