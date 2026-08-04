# Host daemon

The "brains" described in [`docs/DESIGN.md`](../../docs/DESIGN.md). The device is a thin
transport; everything interesting — bindings, lighting, config, the web UI — lives here.

## Status

Phases 0, 1, 3, 4 and 6 are built (see [`docs/ROADMAP.md`](../../docs/ROADMAP.md)).

| Built | Not yet |
|---|---|
| Config load / schema validation / v1 migration | Battery reads and idle enforcement on-device — Phase 8 |
| Palette + effect engine, 10 effects, 16 palettes | BLE HID standalone mode — Phase 9 |
| Frame compositing, serial streaming, batched frames | |
| Bindings: launch, chord, text, shell, script, AppleScript, built-ins | |
| Trigger kinds: press / release / hold / double | |
| Modes with encoder rebinding, profiles | |
| Notification watchers (Dock badge, Slack) | |
| Export / import bundles, HTTP API, web UI host | |

**The one real gap is upstream.** Firmware v2 emits input events but hasn't been flashed, so
bindings currently fire from `POST /api/simulate` rather than from the pad. Both paths are
identical from the dispatcher down, which is what makes the simulated one worth keeping even
after v2 lands — it's how bindings get tested.

Two macOS permissions are needed, and both attach to **whatever launched the daemon**
(Terminal/iTerm, or the launchd job) rather than to the daemon or its helpers, because macOS
attributes them to the responsible process. Without them, shortcut bindings and watchers fail
*silently*: Accessibility (keyboard synthesis, Dock badge reads) and Automation → System Events
(the watchers' Dock query). `GET /api/status` reports both under `keys`.

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

No device required. Covers colour maths, palette sampling, layout mappings, every effect, frame
compositing, frame diffing and batching, config migration/validation/export, trigger-kind
timing (with an injected clock, so hold and double-tap edge cases are deterministic rather than
sleep-based), binding resolution, modes and profiles, and the watcher framework.

`test_link_pty.py` additionally drives the real serial code against a pseudo-terminal, which is
what caught the write deadlock and the unbounded `tcdrain` — neither was findable from
pure-function tests.

## Layout

| Module | What |
|---|---|
| `color.py` | sRGB ↔ OKLab/OKLCh, perceptual mixing, `Palette` sampling |
| `palettes.py` | 16 built-in palettes + WLED custom-palette import |
| `layout.py` | Physical geometry and the three index numberings (strip / matrix / logical) |
| `frame.py` | One frame of LED state; layer compositing |
| `effects.py` | The 10 animated effects |
| `transport.py` | Serial link, frame diffing, batched frames, capability detection |
| `renderer.py` | Layer stack + render loop + idle dimming |
| `events.py` | Raw device edges → bound trigger kinds (press/release/hold/double) |
| `actions.py` | Executing a binding; `Result` reporting |
| `keys.py` | Keyboard chords, text and media keys via the Swift CGEvent helper |
| `dispatch.py` | Trigger → binding resolution, modes, profiles |
| `watchers.py` | Notification pollers that pulse a key |
| `config.py` | Load, validate, migrate v1→v2, export/import |
| `server.py` | Local HTTP API, static host for `host/webui/` |
| `daemon.py` | Wiring and CLI entry point |

## Three things worth knowing

**Colour goes through OKLab, not RGB.** Interpolating in sRGB passes through a desaturated
middle and HSV lightness steps don't look even. Across only 13 keys both artefacts are
obvious, so gradients, dimming, and breathing all work perceptually. See `color.py`.

**The link is bandwidth-constrained.** 115200 baud carries ~11.5 KB/s. `transport.py` diffs
each frame against the last, collapses uniform zones to `k all` / `u all`, and — against v2
firmware — batches large changes into one `kf`/`uf` line. That took a full-pad animated gradient
at 30 fps from 61% of the link down to roughly 35%. Batching is conditional, because one `kf`
line costs ~82 bytes against ~12 for a single pixel write, so small diffs stay per-pixel.
Capability comes from the firmware's `ver` reply, not an assumption; v1 keeps the old path.

**Trigger kinds interact, and the UI has to say so.** Binding `double` on a key means its
`press` cannot fire until the double-tap window elapses, so that key stops feeling instant —
which is why keys with no `double` binding fire immediately. And a `hold` that fires suppresses
the `press` that release would otherwise produce, or every long press would do two things. The
recogniser therefore needs to know what's bound, which is why `dispatch.resolve` and
`dispatch.is_bound` are built on the same lookup: if they ever disagree, the symptom is a key
that feels laggy or double-fires.

## Index mappings

The LED strip-index → physical-position mapping is **confirmed on hardware** — a serpentine
starting at the bottom-right — and ships as a source default (`DEFAULT_KEY_POSITIONS` /
`DEFAULT_UNDERGLOW_POSITIONS` in `layout.py`), because every Creator Micro 2 is wired the same
way. An empty config is already correct; nobody needs to run a sweep to get working effects.
`layout.key_positions` / `layout.underglow_positions` override it for a unit that differs, and
pairing an override with `layout.verified: false` is what re-enables the UI's warning.

The one mapping still unproven is on the **input** side: whether firmware v2's matrix column
order runs physically left-to-right. Settle it by pressing each key with the web UI's event feed
open — `GET /api/events` shows which logical index actually arrived.

Note `Frame.keys` is indexed by **logical** key and `Frame.under` by **ring** position;
`transport._frame_lines` is the only place that translates to strip index. Anything above it
working in strip indices is a bug, and it's an invisible one until the mapping isn't identity.
