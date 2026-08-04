#!/usr/bin/env python3
"""lmctl — LibreMicro host driver for the Creator Micro 2 custom firmware.

The custom firmware exposes a line-based command protocol over its
USB-Serial-JTAG console (the same /dev/cu.usbmodem* port esptool uses). This is
NOT the vendor HID RPC — custom firmware replaces the vendor stack entirely.

Firmware command grammar (newline-terminated, ASCII):
    k <i> <rrggbb>       set key LED i (0..12) to a colour
    k all <rrggbb>       set every key LED
    u <i> <rrggbb>       set underglow LED i (0..7)
    u all <rrggbb>       set every underglow LED
    t <i> <0-255>        set status/"touch" LED i (0..2) brightness (PWM, single colour)
    t all <0-255>        set all three status LEDs
    tflash [count]       blink the three status LEDs
    clear                all addressable LEDs off
    demo                 run the built-in per-key rainbow sweep
    dump                 print inherited hold/GPIO register state
    bright <0-255>       global brightness scale
Every command is acked with a line beginning "ok" or "err".

Host usage:
    ./lmctl.py demo                        # trigger firmware demo
    ./lmctl.py key 3 ff0000                # key 3 red
    ./lmctl.py under 0 0000ff              # underglow 0 blue
    ./lmctl.py touch all 180               # status LEDs ~70%
    ./lmctl.py rainbow                     # host-driven per-key rainbow animation
    ./lmctl.py chase 00ff88                # host-driven chase across keys+underglow
    ./lmctl.py raw "u all 0000ff"          # send an arbitrary firmware line
    ./lmctl.py dump                        # ask fw to print register state
Port is auto-detected; override with --port.
"""
import argparse, glob, sys, time, colorsys

try:
    import serial
except ImportError:
    sys.exit("pyserial missing: pip install pyserial")

KEYS, UNDER = 13, 8

def find_port(explicit):
    if explicit:
        return explicit
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        sys.exit("no /dev/cu.usbmodem* found — is the device connected and running custom fw?")
    return ports[0]

class Dev:
    def __init__(self, port, quiet=False):
        self.s = serial.Serial(port, 115200, timeout=0.3)
        self.quiet = quiet
        time.sleep(0.2)
        self.s.reset_input_buffer()
    def cmd(self, line, wait=0.05):
        self.s.write((line.strip() + "\n").encode())
        self.s.flush()
        time.sleep(wait)
        out = self.s.read(4096).decode("utf-8", "replace")
        if not self.quiet and out.strip():
            for ln in out.splitlines():
                if ln.strip():
                    print(f"  <fw> {ln.rstrip()}")
        return out
    def close(self):
        self.s.close()

def hexc(c):
    c = c.lstrip("#")
    return c if len(c) == 6 else "ffffff"

def rainbow(dev, cycles=6, delay=0.03):
    print("host-driven per-key rainbow (Ctrl-C to stop)")
    n = KEYS + UNDER
    try:
        for f in range(cycles * n):
            for i in range(KEYS):
                h = ((i + f) % n) / n
                r, g, b = (int(x * 255) for x in colorsys.hsv_to_rgb(h, 1, 1))
                dev.cmd(f"k {i} {r:02x}{g:02x}{b:02x}", wait=0)
            for j in range(UNDER):
                h = ((j + f + KEYS) % n) / n
                r, g, b = (int(x * 255) for x in colorsys.hsv_to_rgb(h, 1, 1))
                dev.cmd(f"u {j} {r:02x}{g:02x}{b:02x}", wait=0)
            time.sleep(delay)
    except KeyboardInterrupt:
        pass
    dev.cmd("clear")

def chase(dev, color, laps=8, delay=0.06):
    print("host-driven chase (Ctrl-C to stop)")
    seq = [("k", i) for i in range(KEYS)] + [("u", j) for j in range(UNDER)]
    try:
        for lap in range(laps * len(seq)):
            dev.cmd("clear", wait=0)
            zone, idx = seq[lap % len(seq)]
            dev.cmd(f"{zone} {idx} {color}", wait=0)
            time.sleep(delay)
    except KeyboardInterrupt:
        pass
    dev.cmd("clear")

def main():
    ap = argparse.ArgumentParser(description="Creator Micro 2 custom-firmware LED driver")
    ap.add_argument("--port")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo"); sub.add_parser("clear"); sub.add_parser("dump")
    sub.add_parser("rainbow");
    p = sub.add_parser("chase"); p.add_argument("color", nargs="?", default="00ff88")
    p = sub.add_parser("key");  p.add_argument("i"); p.add_argument("color")
    p = sub.add_parser("under");p.add_argument("i"); p.add_argument("color")
    p = sub.add_parser("touch");p.add_argument("i"); p.add_argument("duty")   # status LED(s)
    p = sub.add_parser("tflash");p.add_argument("count", nargs="?", default="6")
    p = sub.add_parser("bright");p.add_argument("v")
    p = sub.add_parser("raw");  p.add_argument("line")
    a = ap.parse_args()
    dev = Dev(find_port(a.port))
    try:
        if a.cmd == "rainbow": rainbow(dev)
        elif a.cmd == "chase": chase(dev, hexc(a.color))
        elif a.cmd == "key":   dev.cmd(f"k {a.i} {hexc(a.color)}")
        elif a.cmd == "under": dev.cmd(f"u {a.i} {hexc(a.color)}")
        elif a.cmd == "touch": dev.cmd(f"t {a.i} {a.duty}")
        elif a.cmd == "tflash":dev.cmd(f"tflash {a.count}", wait=int(a.count)*0.3+0.5)
        elif a.cmd == "bright":dev.cmd(f"bright {a.v}")
        elif a.cmd == "raw":   dev.cmd(a.line)
        else:                  dev.cmd(a.cmd)
    finally:
        dev.close()

if __name__ == "__main__":
    main()
