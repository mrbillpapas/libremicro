#!/usr/bin/env python3
"""
Read-only RPC probe for the Work Louder Creator Micro 2.

Sends ONLY the read-only methods Input's own bundled mock handler implements
(sys.version, device.status, fs.list). Deliberately does NOT touch fs.writebin
or any method that mutates device state.

Transport, decoded from the device's USB HID report descriptor:
    vendor usage page 0xFF00, usage 0x01
    Report ID 6 : Input  63 bytes
    Report ID 6 : Output 63 bytes
"""
import json
import sys
import time

import hid

VID, PID = 0x303A, 0x8298
REPORT_ID = 6
PAYLOAD = 63

READ_ONLY_METHODS = ["sys.version", "device.status", "fs.list"]


def frames(obj, style):
    """Build a 64-byte output report for one of several candidate framings."""
    body = json.dumps(obj, separators=(",", ":")).encode()
    if style == "raw":
        pkt = body
    elif style == "len8":
        pkt = bytes([len(body)]) + body
    elif style == "len16le":
        pkt = len(body).to_bytes(2, "little") + body
    else:
        raise ValueError(style)
    if len(pkt) > PAYLOAD:
        raise ValueError(f"{len(pkt)}B exceeds {PAYLOAD}B report")
    return bytes([REPORT_ID]) + pkt + b"\x00" * (PAYLOAD - len(pkt))


def drain(dev, timeout_ms=600):
    """Collect report-ID-6 input reports for a short window."""
    out, deadline = [], time.time() + timeout_ms / 1000
    while time.time() < deadline:
        data = dev.read(PAYLOAD + 1, timeout_ms=120)
        if not data:
            continue
        if data[0] == REPORT_ID or len(data) >= PAYLOAD:
            out.append(bytes(data))
    return out


def show(tag, reports):
    if not reports:
        print(f"      {tag}: no response")
        return False
    for r in reports:
        body = bytes(r[1:]) if r[0] == REPORT_ID else bytes(r)
        txt = body.rstrip(b"\x00")
        print(f"      {tag}: {len(r)}B raw={r[:16].hex()}...")
        # try to surface JSON no matter where it starts
        for start in range(0, min(4, len(txt))):
            chunk = txt[start:]
            try:
                print(f"      {tag}: JSON -> {json.loads(chunk.decode())}")
                return True
            except Exception:
                pass
        printable = "".join(chr(c) if 32 <= c < 127 else "." for c in txt[:80])
        print(f"      {tag}: ascii='{printable}'")
    return True


def main():
    devs = [d for d in hid.enumerate(VID, PID)
            if d.get("usage_page") == 0xFF00 and d.get("usage") == 0x01]
    if not devs:
        sys.exit("vendor collection 0xFF00/0x01 not found - is it on USB?")

    path = devs[0]["path"]
    print(f"opening {path.decode(errors='replace')}\n")

    dev = hid.device()
    try:
        dev.open_path(path)
    except Exception as e:
        print(f"OPEN FAILED: {e}")
        print("\nOn macOS this usually means Input Monitoring permission is missing.")
        print("System Settings > Privacy & Security > Input Monitoring -> add your terminal.")
        sys.exit(1)

    dev.set_nonblocking(0)
    print(f"opened: {dev.get_manufacturer_string()} {dev.get_product_string()}\n")

    for method in READ_ONLY_METHODS:
        print(f"--- {method}")
        for style in ("raw", "len8", "len16le"):
            req = {"id": 1, "method": method, "params": {}}
            pkt = frames(req, style)
            try:
                n = dev.write(pkt)
            except Exception as e:
                print(f"      {style}: write failed: {e}")
                continue
            print(f"      {style}: wrote {n}B")
            show(style, drain(dev))
        print()

    dev.close()


if __name__ == "__main__":
    main()
