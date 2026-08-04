# Host daemon

The "brains" described in [`docs/DESIGN.md`](../../docs/DESIGN.md). The device is a thin
transport; everything interesting — bindings, lighting, config, the web UI — lives here.

## Status

**Phase 0 is built and tested** (see [`docs/ROADMAP.md`](../../docs/ROADMAP.md)): the whole
LED-out path works against today's firmware, with no firmware change needed.

| Built | Not yet |
|---|---|
| Config load / schema validation / v1 migration | Binding dispatch (launch, shortcut, script) — Phase 3 |
| Palette + effect engine, 10 effects, 16 palettes | Modes and encoder rebinding — Phase 4 |
| Frame compositing and serial streaming | Notification watchers — Phase 6 |
| Export / import bundles | Battery reads and idle enforcement on-device — Phase 8 |
| HTTP API + static host for the web UI | |

Input events are **not** wired to actions yet, because the firmware doesn't emit them yet.
`Daemon.handle_event` has the plumbing and currently only registers activity and flashes the
pressed key; Phase 3 fills in dispatch.

## Install and run

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e host/daemon
./.venv/bin/libremicro                      # searches the standard config paths
./.venv/bin/libremicro -c path/to/config.json
./.venv/bin/libremicro --validate            # check a config and exit
./.venv/bin/libremicro --no-ui               # don't serve the web UI
```

Config search order: `~/.config/libremicro/config.json`, then `host/config/config.json`, then
the shipped `host/config/example.json`. The example is treated as a read-only template —
saving a config loaded from it writes to `~/.config/libremicro/config.json` instead, so the
repo's example never gets clobbered.

Runs fine with no device attached: the link reports disconnected, retries every couple of
seconds, and the web UI stays fully usable for editing.

## Tests

```bash
./.venv/bin/python -m unittest discover -s host/daemon/tests
```

56 tests, no device required — colour maths, palette sampling, layout mappings, every effect,
frame compositing, serial frame diffing, and config migration/validation/export.

## Layout

| Module | What |
|---|---|
| `color.py` | sRGB ↔ OKLab/OKLCh, perceptual mixing, `Palette` sampling |
| `palettes.py` | 16 built-in palettes + WLED custom-palette import |
| `layout.py` | Physical geometry and the three index numberings (strip / matrix / logical) |
| `frame.py` | One frame of LED state; layer compositing |
| `effects.py` | The 10 animated effects |
| `transport.py` | Serial link, frame diffing, event reading |
| `renderer.py` | Layer stack + render loop + idle dimming |
| `config.py` | Load, validate, migrate v1→v2, export/import |
| `server.py` | Local HTTP API, static host for `host/webui/` |
| `daemon.py` | Wiring and CLI entry point |

## Two things worth knowing

**Colour goes through OKLab, not RGB.** Interpolating in sRGB passes through a desaturated
middle and HSV lightness steps don't look even. Across only 13 keys both artefacts are
obvious, so gradients, dimming, and breathing all work perceptually. See `color.py`.

**The link is bandwidth-constrained.** 115200 baud carries ~11.5 KB/s. `transport.py` diffs
each frame against the last and collapses uniform zones to a single `k all` / `u all`, but a
full-pad animated gradient at 30 fps still measures ~7.0 KB/s — 61% of the link, with no room
for a second animated layer. A batched frame command would fix this properly; the proposal is
in [`docs/PROTOCOL.md`](../../docs/PROTOCOL.md).

## Layout mapping is unverified

The strip-index → physical-position mapping for both LED chains is **not confirmed on
hardware** (see [`docs/HARDWARE.md`](../../docs/HARDWARE.md)). It lives in config
(`layout.key_positions` / `layout.underglow_positions`) rather than in source, so correcting it
is a data change. A partial mapping is completed into a bijection deterministically, so
spatial effects always address every LED exactly once. Run the web UI's identify sweep to
confirm it, then set `layout.verified: true`.
