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

## Device → host: input events (planned, v2)

The v2 "thin-transport" firmware will emit one line per input event on the same link, so the
host daemon can react. Proposed grammar (subject to change once implemented):

| Event | Meaning |
|---|---|
| `key <i> down` / `key <i> up` | key `i` (0–12) pressed / released |
| `enc cw` / `enc ccw` | encoder rotated one detent |
| `enc press` / `enc release` | encoder button |
| `touch` | touch pad activated |
| `rear` | rear button pressed |

Events and commands share the port, so the daemon both reads events and writes LED commands
on one serial connection. Lines are prefixed unambiguously (`ok`/`err` for command replies vs.
`key`/`enc`/`touch`/`rear` for events) so the two streams don't collide.
