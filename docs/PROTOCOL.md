# Serial protocol

The custom firmware talks over its **USB-Serial-JTAG console** (the same `/dev/cu.usbmodem*`
port esptool uses), 115200 baud, newline-delimited ASCII. This replaces the vendor HID RPC
entirely — custom firmware does not implement the vendor stack.

## Host → device: commands

Every command is one line; the device replies with a line starting `ok` or `err`.

| Command | Meaning |
|---|---|
| `k <i> <rrggbb>` | set key LED `i` (0–12) to a hex colour |
| `k all <rrggbb>` | set every key LED |
| `u <i> <rrggbb>` | set underglow LED `i` (0–7) |
| `u all <rrggbb>` | set every underglow LED |
| `t <i> <0-255>` | set status/"touch" LED `i` (0–2) brightness (single-colour PWM) |
| `t all <0-255>` | set all three status LEDs |
| `tflash [count]` | blink the three status LEDs |
| `bright <0-255>` | global brightness scale applied to the addressable LEDs |
| `clear` | all addressable LEDs off |
| `demo` | run the built-in per-key rainbow sweep |
| `dump` | print the inherited hold/GPIO register state (diagnostics) |

Colours are `rrggbb` hex. `bright` scales all addressable pixels; the status LEDs take a raw
0–255 PWM duty.

`host/cli/lmctl.py` wraps these, and adds host-side animations (`rainbow`, `chase`) built out
of the primitives above.

### Indices here are *strip* indices

`k <i>` addresses per-key LED `i` along the GPIO 7 chain (0–12) and `u <i>` addresses the
GPIO 6 chain (0–7). These are **not** the same numbering as the key matrix, which is
`4·row + col` over a 4×4 scan and therefore runs 0–15 with three unpopulated slots. Nor are
they necessarily the same as physical position — strip wiring order is unverified
(`docs/HARDWARE.md`). The host keeps a third, stable numbering (logical 0–12, row-major over
populated slots) for config and translates at the edges; see `host/daemon/libremicro/layout.py`.

**Consequence for v2:** input events must report **logical** key index 0–12, not raw matrix
index, or the daemon has to know the matrix's unpopulated slots to interpret them. Firmware
should do that mapping once, on-device.

### Planned: batched frame writes

At 115200 baud the link carries roughly 11.5 KB/s. A full 21-pixel frame written as
individual commands is ~210 bytes, so 30 fps costs over half the link before acks, and it
forces one firmware refresh per pixel. The host works around this by diffing frames and
sending only changed pixels — **measured at ~7.0 KB/s (61% of the link)** for a full-pad
animated gradient at 30 fps, which is the realistic worst case.

That leaves little headroom, and none for a second animated layer.

Two commands would remove the problem and are worth adding alongside the v2 input work:

| Command | Meaning |
|---|---|
| `kf <rrggbb>×13` | set all 13 key LEDs in one line, single refresh |
| `uf <rrggbb>×8` | set all 8 underglow LEDs in one line, single refresh |

That's 92 bytes for a whole frame instead of ~210, with two refreshes instead of 21.

## Device → host: input events (planned, v2)

The v2 "thin-transport" firmware will emit one line per input event on the same link, so the
host daemon can react. Proposed grammar (subject to change once implemented):

| Event | Meaning |
|---|---|
| `key <i> down` / `key <i> up` | key `i` (**logical** 0–12, see above) pressed / released |
| `enc cw` / `enc ccw` | encoder rotated one detent |
| `enc press` / `enc release` | encoder button |
| `touch` | touch pad activated |
| `rear` | rear button pressed |
| `batt <percent> <0\|1>` | battery state of charge and whether it's charging (Phase 8) |

Events and commands share the port, so the daemon both reads events and writes LED commands
on one serial connection. Lines are prefixed unambiguously (`ok`/`err` for command replies vs.
`key`/`enc`/`touch`/`rear` for events) so the two streams don't collide.
