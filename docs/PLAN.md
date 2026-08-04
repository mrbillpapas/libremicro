# Plan — pick up here

Written as a handoff. If you have no memory of building this, read this file and
[`ROADMAP.md`](ROADMAP.md) and you should be able to continue without re-deriving anything.

---

## 1. State of play

**The pad works.** Firmware v2 is flashed and every input reports. Keys, rotary encoder,
capacitive touch pad, analog joystick, per-key and underglow RGB, and battery are all confirmed
**on hardware**, driven by a Python daemon with a local web UI. 418 tests pass, none needing a
device. The repo is public at `github.com/mrbillpapas/libremicro`, MIT, `main` is current.

Read [`ROADMAP.md`](ROADMAP.md) for phase-by-phase status. The short version of what is *not*
done: rear button (deliberately), idle sleep and power-off, BLE HID, and proving the watchers
and agent surface against something live.

### The distinction that matters

Throughout this project the difference between **built** and **confirmed on hardware** is where
all the real risk lived. Several things compiled, passed tests, and were still wrong — the
encoder decode, the joystick orientation, the volume path. Treat "it builds" as the start of
verification, not the end.

---

## 2. Open work, most useful first

### 2.1 Confirm the joystick's east/west

One flip was made (`LM_JOY_INVERT_X = 1`) after measuring that pushing up reported `n` correctly
while pushing right reported `w`. **The flip was never verified.** Push right and check the
Events tab says `e`. If it doesn't, the axes are transposed rather than mirrored and
`LM_JOY_SWAP_XY` is the next thing to try.

### 2.2 Rear button — blocked, and not on its pin

Its pin is confirmed (GPIO 2). The blocker is that **stock arms a ULP-RISCV watcher on GPIO 2
that forces a hardware reset when it goes low**, and `scripts/enter_bootloader.sh` is what arms
it — so it is probably live on any pad that has just been flashed. `LM_ENABLE_REAR` is opt-in.
To enable it properly, either clear `RTC_CNTL_ULP_CP_SLP_TIMER_EN` or power-cycle without going
through that RPC. Do not just flip the flag and hope.

### 2.3 Watchers and the agent surface are unproven

Both are built and unit-tested but have never run against a real Slack unread count or a live
Claude Code session. Both need macOS **Accessibility** and **Automation → System Events** granted
to whatever launches the daemon. Note the owner's Claude Code settings suppress most permission
prompts, so `waiting` — the headline agent state — will fire rarely for them.

### 2.4 Power management

Idle sleep and power-off inherit the same GPIO 2 hazard, plus stock's ~2 s wait-for-release guard
before arming ext0 (`rear stuck LOW, ext0 SKIPPED`). Replicate that guard or the device bounces
straight back out of deep sleep.

### 2.5 Status LED live preview

They work — confirmed on hardware — but only apply on **Save**. Dragging the sliders should
preview live like the colour pickers do. Small, and it was the thing that made them look broken.

---

## 3. Next feature: agent control, informed by OpenMicro

[`stephenleo/OpenMicro`](https://github.com/stephenleo/OpenMicro) does "Codex Micro on any
gaming controller", **MIT licensed**, TypeScript/Node over HID gamepads. Its design converges
almost exactly with `docs/AGENT-SURFACE.md`, which is a good sign both are right:

| OpenMicro | Ours |
|---|---|
| DualSense lightbar: blue working / amber waiting / green done / red error | same five states, same colour logic |
| face buttons: submit, interrupt, dictate, new chat | `agent_approve`, `agent_deny`, `agent_dictate` |
| right-stick thinking-depth dial | `agent_effort_*` — and we have a real encoder |
| touchpad cycles sessions | `agent_session_next/prev` |
| six remappable combination layers | profiles and modes |
| **stick flicks launch review/debug/refactor prompts** | **we have nothing like this** |

There is no code to lift — different language, different device class. What is worth taking is
the design thinking, with attribution:

1. **Workflow launching on the joystick.** The strongest borrow. Our joystick has eight bound-able
   directions sitting idle; their stick-flick-to-canned-prompt idea maps onto it directly. Eight
   prompts (review, debug, refactor, test, explain…) on a stick we already read.
2. **Codex CLI as a second harness.** Ours is Claude Code only and `agent_surface.py` has a
   `harness` field already reserved for this.
3. **Their effort-dial setup notes.** We concluded effort cannot be set on a live session and made
   ours read-only-plus-slash-command. If they solved it, that changes our answer.

Two advantages we should not waste: **13 per-key RGB LEDs** rather than one lightbar (per-session
and per-state colour on distinct keys), and **a real rotary encoder** rather than a stick
pretending to be a dial.

---

## 4. Operational notes that cost real debugging time

- **A daemon launched from a sandboxed shell cannot change system volume.** Reads succeed and
  writes are silently swallowed, which is indistinguishable from broken hardware. Launch it from
  a normal terminal.
- **Flashing:** `P=$(./scripts/enter_bootloader.sh)` then
  `esptool --port "$P" --chip esp32s3 write-flash 0x10000 firmware/.pio/build/cm2/firmware.bin`.
  App-only at `0x10000` preserves the vendor `nvs` and `littlefs`. Always rebuild the **safe
  default** configuration last, since that is the binary that gets flashed.
- **`ver` can be lost in post-flash boot noise.** The host retries three times; if `firmware` is
  null in `/api/status` the host silently treats a v2 pad as v1 — no batched frames, no battery.
- **A latched synthesised media key** pins volume at a rail and refuses `set volume`.
  `host/swift/lmkey auxrelease` clears it; `lmkey mods [release]` does the same for modifiers.
- **Never put modifier flags on an NX aux event** — neither in its `0xA00` marker field nor on its
  CGEvent flags. Both break key-up pairing and latch the key. And holding real modifiers does
  *not* give quarter-step volume for a synthesised event; that idea is dead, don't retry it.
- **Don't `git add -A` while a subagent is editing.** It sweeps their in-progress work into your
  commit under the wrong message. This happened once and is now in the history.

### Calibration constants — measured, not derived

Each is a single value so a different unit is a one-line change:

| Constant | Value | Why |
|---|---|---|
| `LM_ENC_INVERT` | 1 | clockwise was reporting ccw; depends on PCB wiring |
| `LM_JOY_INVERT_X` | 1 | up read `n` but right read `w`, so X is mirrored |
| `LM_JOY_REST_X/Y` | 1928 | stock hard-codes 2047.5; travel is asymmetric either side |
| `LM_TOUCH_ACTIVE_HIGH` | 1 | vendor ISR passed the level uninverted, unlike its siblings |

---

## 5. Decisions still with the owner

- **`Co-Authored-By: Claude` trailers** on ~36 commits are public. Removable by history rewrite,
  same as the work email already removed — with the same caveat that anyone who already cloned
  keeps the old copy.
- **PCB work is local-only.** `docs/OPEN-PCB.md`, `docs/pcb/` and `hardware/` are gitignored by
  request. Nothing PCB-related has ever been committed.
- **Volume mode.** Default is `coarse` — the real media key, so macOS shows its slider, at the
  cost of 6.25% steps. `fine` gives any step size but no overlay. Both light the pad's underglow
  bar.
- **3D view** defaults off, 2D is the default instrument. Flat is easier to aim at; 3D is the
  better picture.

---

## 6. Things deliberately not done, with reasons

Recording these so they are not re-litigated:

- **No three.js / WebGL library.** The web UI has no build step, no npm, and makes no external
  requests. The 3D view is hand-written WebGL to keep that true.
- **No client-side re-implementation of effects.** The UI mirrors `GET /api/frame` — the frame the
  daemon is actually sending. Two implementations of one animation always drift; this was a real
  bug, not a theoretical one.
- **The source-map extractor was removed** before publishing. It existed only to reproduce Work
  Louder's own TypeScript, which is a different thing from analysing a firmware image you own,
  and it contradicted the README's own statement.
- **Quarter-step volume via synthesised media keys.** Provably does not work. See §4.
