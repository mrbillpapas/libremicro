# Local web UI — the studio (Phases 1 and 5)

The browser half of [`docs/ROADMAP.md`](../../docs/ROADMAP.md). Phase 1: a layout-accurate view of
the pad, a colour/palette/effect designer with live preview on the device, the LED identify sweep,
and config export/import. Phase 5: **what the keys do** — a binding editor per trigger, a shortcut
recorder, a "test this binding without the hardware" button on every trigger, a live input-event
feed, and profile/mode management.

Four static files, no build step, no npm, no CDN, no external requests of any kind — the daemon
may be run offline. Serve this directory as static files at the web UI root (`webui.host` /
`webui.port`, default `http://127.0.0.1:8777/`) and the `/api/*` endpoints below on the same
origin.

```
index.html   markup and static panel structure
app.js       one vanilla ES module: colour maths, renderer, panels, API client
style.css    dark-first, light via prefers-color-scheme
```

The UI is a **view onto the same JSON config** the daemon reads (schema v2,
[`host/config/schema.json`](../config/schema.json)) and never a separate source of truth. It
round-trips unknown top-level keys (e.g. the `$schema` pointer in `example.json`) untouched, and
edits only what the panels expose. Everything the schema defines except `watch` (Phase 6) and
`power` now has an editor; anything with no editor is preserved byte-for-byte on save.

## API it expects

| Method | Path | Used for |
|---|---|---|
| GET | `/api/config` | initial load, `Reload` |
| PUT | `/api/config` | `Save`; expects `{"ok":bool,"errors":[...]}` |
| GET | `/api/schema` | fetched and kept for reference (not yet used for validation) |
| GET | `/api/palettes` | built-in palette corpus; **replaces** the fallback set embedded in `app.js` |
| GET | `/api/status` | status bar, polled every 3 s (8 s while unreachable). `keys` drives the keyboard-synthesis warning, `input_events` the event feed's empty state |
| POST | `/api/preview/frame` | `{keys:[hex×13],underglow:[hex×8],status:[int×3],ttl:6}` on colour edits, throttled to `device.fps`. `keys` is in **logical** order, `underglow` in **ring** order — see below |
| POST | `/api/preview/effect` | `{effect:{…}}` on effect edits, debounced 140 ms |
| POST | `/api/preview/stop` | `Stop preview`, and when a sweep ends |
| POST | `/api/identify` | `{target:"keys"\|"underglow",index:int}` per sweep step — `index` is a **strip** index |
| GET | `/api/export` | `Export` bundle download |
| POST | `/api/import` | `Import` upload |
| POST | `/api/simulate` | every trigger's `Test`: `{key:i}`, `{key:i,hold_s:n}`, or `{line:"enc cw"}` |
| GET | `/api/events?since=<seq>` | the event feed, polled every 700 ms (4 s while unreachable) |
| POST | `/api/profile` | `Switch now` / `Switch next` / `prev` — `{profile:"name"\|"next"\|"prev"}` |
| POST | `/api/mode` | `Activate now` / `Leave mode now` — `{mode:"name"\|null}` |

Contract notes for whoever is writing the daemon side:

- **Live preview arbitration.** Colour/base edits push a *fully composited* frame (base layer +
  the client-rendered effect at the current animation time); effect edits hand rendering to the
  daemon via `/api/preview/effect`. A frame push after an effect push is therefore a deliberate
  override — the daemon should let the most recent call win rather than compositing them.
- **An effect is only delegated when the daemon can resolve it.** If its palette exists only in
  this page's *unsaved* edits, `/api/preview/effect` would render against a palette the daemon
  hasn't loaded, so the UI streams composited frames at `device.fps` instead until the config is
  saved. That is the one case where the page pushes frames continuously.
- **Previews expire, and that's deliberate.** Frames are pushed with `ttl: 6` (seconds) and a
  2.5 s heartbeat re-push keeps them alive only while `Live preview on device` is ticked, so a
  closed tab or a crashed browser leaves the pad reverting by itself within seconds instead of
  stuck on the last frame. Effect previews are re-pushed at most every 4 minutes, because
  re-pushing restarts the animation phase.
- **`ok: false` inside a 200** is treated as a real failure and surfaced: `/api/identify`
  answering `{"ok":false}` (no pad attached) warns once per sweep and keeps manual recording
  available, and `/api/preview/effect` errors are shown as a toast.
- **Optional status fields are used when present.** `previewing` highlights the `Stop preview`
  button when something is actually driving the strips; unknown extra fields are ignored.
- **Export/import shape is unspecified**, so import accepts either a bare v2 config or any
  wrapper with a `.config` member that is a v2 config, and posts the *whole file* to
  `/api/import` while loading the extracted config into the editor. If a bundle wrapper is
  defined later, only `doImport()` needs to learn about it.
- **The palette corpus is the daemon's.** `/api/palettes` replaces rather than merges with the
  ~12 palettes embedded in `app.js`, so the picker can never offer a name the daemon would fail
  to resolve. The embedded set exists only for the offline case; anything designed offline should
  be `Duplicate`d into `palettes` to stay portable.
- **Everything degrades.** Every request resolves rather than rejecting; with no daemon the page
  edits a local starter config and keeps the client-side preview, save reports the failure and
  suggests `Export`, and the identify sweep still lets a mapping be recorded by hand. Bindings,
  profiles and modes are all editable offline too — only `Test`, `Switch now` and the event feed
  need a daemon, and each says so instead of failing quietly. A `404` *or* a `501` on an `/api`
  route means "something is on this port but it isn't the daemon" (501 is what a plain static file
  server answers a `POST` with), so a static server never reads as a healthy daemon.
- **`/api/simulate` is a real dispatch, not a shortcut.** The `Test` buttons inject an event and
  let the daemon's own recogniser and dispatcher do the rest, which is why hold and double timing
  behave exactly as they will from hardware. The hold test uses `hold_s` so the key is genuinely
  held; past ~1.8 s (the daemon caps its own sleep at 2 s) the UI sends `key i down`, waits, and
  sends `key i up` itself.

## Bindings

The **Bindings** tab edits one control at a time: a chip per key (`0`–`12`, each showing its label
and how many triggers it has bound), plus the encoder, the touch pad and the rear button. Clicking
a key on the board selects it here too — and *stays* on this tab rather than jumping to Colour.
The encoder and touch-pad ghosts on the board take a pointer click straight into this panel; they
remain out of the tab order and `aria-hidden`, because they carry no LED and the chips are the
keyboard path to the same thing.

Every applicable trigger kind gets a card — press / release / hold / double for keys, the touch pad
and the rear button; cw / ccw / press for the encoder, which is all the schema defines for it.
Each card has one `Does` select covering all nine action keys (`launch`, `shortcut`, `text`,
`shell`, `script`, `applescript`, `mode`, `profile`, `action`), the value editor for whichever is
chosen, and the optional `flash` colour.

- **Exactly one action per binding, enforced by construction.** `binding` is a `oneOf` in the
  schema, so choosing a different type *replaces* the action key rather than adding one; `(nothing)`
  removes the trigger, and an emptied `on` / `encoder` / `touch` object is dropped rather than left
  behind as `{}`. Text is carried across a type change only between the free-text kinds — `shell` to
  `script` is a rename, `shell` to "activate mode" is not.
- **A mode's `encoder` is the exception to pruning**: the schema *requires* it, so an empty
  `encoder: {}` is the valid way for a mode to rebind nothing.
- **`mode` and `profile` are pickers, not text fields**, so they can't name something that doesn't
  exist; a value that already does (from hand-edited JSON) is offered with a "does not exist" note
  rather than being silently dropped.
- **`action` is grouped by what implements it** — the media/volume/brightness half needs the native
  helper, the rest is daemon-side and works without it.
- An action key present but empty is legal JSON and a dead binding, so the field is outlined and
  `Save` refuses it.

### The trigger kinds interact, and the editor says so where you choose

Straight out of [`events.py`](../daemon/libremicro/events.py)'s docstring, surfaced on the cards
rather than in a help page:

- Binding **double** is what costs latency: `press` on that control can no longer fire until the
  double-tap window closes. The `double` card says so, *and* the `press` card changes its own note
  to "press cannot fire until the 280 ms double-tap window has closed" the moment double is bound.
  Controls with no double binding fire press instantly whatever `double_ms` says — the `press` card
  says that too, so the absence is legible.
- If **hold** fires, the press that release would have produced is suppressed.
- **release** is independent and always fires — including on a release that already fired hold or
  double.
- The **encoder's** cw/ccw fire per detent, immediately.
- The touch pad and rear button get a warning that **hold can never fire from real hardware**:
  firmware v2 reports them as one bare line (`touch`, `rear`) with no down/up pair, and a tap has
  no duration. Their `Test` sends a genuine `touch down` … `touch up` pair, which does fire it.

`device.hold_ms` and `device.double_ms` are editable in the same panel, with the live values quoted
back in every note and a list of which controls are actually paying for `double_ms`.

### The shortcut recorder

Press the chord instead of typing its name. The recorder builds a spec in the grammar
[`keys.py`](../daemon/libremicro/keys.py)'s `parse_shortcut` accepts and shows the normalised
result — modifiers in Apple's `fn+ctrl+opt+shift+cmd` order, one key last. It reads
`KeyboardEvent.code`, not `.key`, because with modifiers held `.key` is the layout's shifted legend
(`opt+a` is `å`) while the helper wants the physical key.

`app.js` carries a mirror of `keys.py`'s name tables so a typed spec can be normalised and
rejected client-side — `cmd+` gets the same "no key after the last `+`" message the daemon would
give. **If they ever drift, the daemon is right and the mirror is wrong.**

What the browser refuses to give you, and what happens instead:

- **macOS and the browser claim some chords before any page sees the keydown** and a page cannot
  opt out. `cmd+w`, `cmd+q`, `cmd+t`, `cmd+n`, `cmd+m`, `cmd+h`, `cmd+space`, `cmd+tab`,
  `ctrl+cmd+q`, `cmd+opt+escape`, the `cmd+shift+3/4/5` screenshot chords and friends are listed
  in the collapsible under the field. **Type those.** They are perfectly good *bindings* — the pad
  sends them and macOS acts on them; only *recording* them is impossible. Typing one shows a note
  saying exactly that rather than a bare warning.
- **`fn` is invisible to browsers entirely**, so an `fn` chord always has to be typed.
- A chord that gets taken mid-recording usually blurs the window; that is detected and reported as
  "something else took that chord before this page saw it — type it instead".
- A key with no name in the grammar reports the `code` the browser used and keeps listening.
- **Escape alone cancels** the recorder, because a recorder you can't leave with the key everyone
  reaches for is a trap. The status line says to type `escape` to bind Escape itself.

The text field is always live, so there is no state in which the user is stuck.

## Testing a binding with no hardware

Firmware v2 emits real key events but **is not flashed yet**, so `POST /api/simulate` is currently
the only way to fire a binding at all — and afterwards it stays the way to test one without
reaching for the pad. Every trigger card has a `Test` button that injects the matching event:

| Trigger | What is injected |
|---|---|
| key press / release | `{key:i}` — the daemon expands it to a `down`/`up` pair |
| key hold | `{key:i,hold_s:(hold_ms+160)/1000}`, so the recogniser watches a real clock |
| key double | two `{key:i}` calls back to back, inside `double_ms` |
| encoder cw / ccw | `{line:"enc cw"}` / `"enc ccw"` |
| encoder press | `{line:"enc press"}` then `"enc release"` |
| touch / rear press, double | `{line:"touch"}` — the bare form, exactly what v2 sends |
| touch / rear hold | `{line:"touch down"}`, wait `hold_ms`, `{line:"touch up"}` |

**It is never disguised as a real press.** The toast says "this was injected, not a real press",
the event feed tags it `injected`, and the board highlight for an injected event is a *dashed*
accent ring where a real one is a solid green one. Testing `press` on a control that also binds
`release` says so, since a real press always releases.

## The event feed

The **Events** tab polls `GET /api/events?since=<seq>` and shows the tail newest-first with the
`source` spelled out (`pad` vs `injected`) and a plain-language reading of each line: which logical
key, its label, which LED strip index lights it, and which triggers that control has bound. A key
index outside 0–12 is called out as the firmware reporting raw matrix indices instead of logical
ones.

**Highlighting is the point.** As each event arrives the control it names lights up on the board —
solid green for the pad, dashed accent for injected, decaying over 900 ms — so *press a physical
key and see which logical index arrived* is a glance, not a table lookup. That is the check to run
first after flashing v2. It works from any tab, and the checkbox turns it off.

When `input_events_seen` is false the panel explains the state rather than looking broken: this
firmware has never sent an input event, the build on the pad is LED-out only, flash v2 — and note
that the encoder, touch pad and rear button additionally need `LM_ENABLE_UNVERIFIED_INPUTS` until
their pins are confirmed.

## Editing scope: profiles and modes

A mode's `keys`, `encoder` and `lighting` **override** the profile's while it is active. Rather
than growing a mode picker inside every panel, the whole editor has one **`Editing`** selector next
to the profile picker: *profile default*, or one of its modes. It follows
[`dispatch.py`](../daemon/libremicro/dispatch.py)'s resolution order exactly —

- **keys** and the **encoder** belong to the layer being edited;
- **touch** and **rear** are profile-level only, because modes don't override them, and the
  Bindings panel says so instead of letting you believe otherwise;
- the device view previews the *merged* result the way
  [`renderer.py`](../daemon/libremicro/renderer.py) composes it — profile key colours with the
  mode's layered over, and `{...profile.lighting, ...mode.lighting}` — so a colour the mode
  inherits shows on the board and the Colour panel says it is inherited.

The **Profiles** tab creates, renames, duplicates, reorders and deletes profiles, and sets
`auto_activate_app`. Order is a real edit, not cosmetic: it is the order `profile_next` / `prev`
cycle in. Renaming rewrites `active_profile` and every `{"profile": …}` binding that pointed at the
old name; the same goes for renaming a mode and its `{"mode": …}` bindings. Modes are managed in
the same tab — `activate_key`, `flash`, `timeout_s` (with `0` meaning "omit it and stay until
switched", since the schema's minimum is 1), plus jumps into that mode's bindings and lighting.

`Make active` sets `active_profile` in the config; `Switch now`, `Switch next/prev`, `Activate now`
and `Leave mode now` change the *running daemon* through the new endpoints without saving. Both are
labelled as what they are, and the status bar shows what the daemon reports.

## Keyboard synthesis has to be warned about, not discovered

A `shortcut`, `text` or media `action` binding on a machine where `host/swift/lmkey` isn't built —
or where Accessibility isn't granted — does **nothing at all, silently**. That is the worst failure
mode in this app, so `status.keys` drives a banner at the top of the page (not a line in a panel),
with the count of bindings in the config that depend on it, and it says the thing people get wrong:
**macOS attributes Accessibility to the process that launched the daemon** — your Terminal, iTerm,
or the launchd job — **not to the helper binary**. Grant it there and restart the daemon. The
Bindings panel repeats the state in a box with a `Re-check` button, and reports "trusted, shortcuts
will land" when all is well.

## Geometry: one 4×4 grid, drawn as the faceplate has it

Every slot's physical position is **confirmed** (`docs/HARDWARE.md`, machine-readable in
[`layout.py`](../daemon/libremicro/layout.py)), and this UI mirrors those constants rather than
inventing geometry — `KEY_GRID_COLS`, `SHARED_KEYCAPS`, `FEATURES`, `UNDERGLOW_RING` and the two
`DEFAULT_*_POSITIONS` tables all have a counterpart near the top of `app.js`.

- **13 switches on a 4×4 grid**, rows of 2/4/4/3 at grid columns `(1,2)`, `(0,1,2,3)`,
  `(0,1,2,3)`, `(1,2,3)`. Nothing is centred or aligned by guesswork, so there is no alignment
  control any more.
- **12 keycaps, not 13.** Logical keys 10 and 11 sit under the bottom row's single wide cap. It is
  drawn as one cap — one outline, a hairline seam, both indices inside it — while each half keeps
  its own colour and its own selection, because a two-pixel gradient across one cap is the point.
  Selecting either half explains in the Colour panel why binding the halves separately is not
  reliable, and the Bindings panel says it again where it actually bites — plus a
  `Copy to index 11` button, since giving both halves the same binding is the fix. The grouping
  comes from the `SHARED_KEYCAPS` constant, not from hardcoded positions.
- **Encoder `(0,0)`, joystick `(0,3)`, touch pad `(3,0)`** are drawn as ghost outlines for
  orientation. They are not addressable LEDs, so they are not focusable and never selectable *as
  LEDs*. The encoder and touch pad do carry bindings, so they take a pointer click into the
  Bindings panel (the chips there are the keyboard path) and they light up when their events
  arrive. The joystick has no triggers in schema v2 and stays completely inert.
- **8 underglow LEDs tiling the WHOLE perimeter**, an eighth of its length each, with no gaps. The
  eight ring positions (3×3 minus centre) are the four corners and four edge midpoints of the
  square, so going clockwise they alternate corner, edge-midpoint, corner, edge-midpoint and every
  adjacent pair is *exactly* ⅛ of the perimeter apart. Each LED therefore owns the ⅛ band **centred
  on its own point**: the four edge-midpoint bands lie flat on one side, and the four corner bands
  wrap their corner symmetrically — an L, ¹⁄₁₆ of the perimeter down each adjoining side. That is
  wanted, not an artefact: real underglow diffuses around a corner. The absent centre slot is still
  drawn dashed behind the caps, at the band's own thickness.
  - **How.** The perimeter is defined once as a rounded-rect `<path>` and each LED is its own copy
    of it showing a single dash — `stroke-dasharray: L/8, 7L/8` with `stroke-dashoffset` placing
    that dash over its own eighth, `stroke-width` the band thickness, `stroke-linecap: butt` so
    neighbours abut exactly. Equal shares and corner-wrapping fall out of arc length rather than
    trigonometry, and every band stays an individually clickable, focusable, colourable element, so
    selection, identify highlighting, live preview and the event feed all work on it unchanged.
    `L` is the path's own `getTotalLength()`, and `assertBandTiling()` checks the eight offsets are
    eight distinct eighths that sum to `L` and that each dash really is centred on its ring point.
    A share's colour is its **stroke**, so hover / selection / focus / unmapped paint a slightly
    wider sibling path underneath instead of the band's own stroke.
- **3 PWM status LEDs** are three small marks in a vertical column immediately **left of the touch
  pad**, inside the touch pad's own grid slot at the bottom-left of the key block — clearly smaller
  than a keycap, inside the key footprint rather than below it, and well clear of the perimeter
  band. The touch glyph is told how much of its slot they take and centres in what is left. Still
  0–255 duty, single colour, not RGB.

### Three numbering schemes, and which one the UI shows

- **Logical index** (keys `0–12`, row-major over populated slots) and **ring position**
  (underglow `0–7`, clockwise from top-left) are what the board labels and what selection uses.
  This is the config's contract — `profiles[*].keys[].index` is logical — and it is also the space
  the daemon's frame arrays use: `/api/preview/frame` takes `keys` in logical order and
  `underglow` in ring order, and the daemon's transport translates to strip order itself. Both are
  pure functions of physical position, so re-recording the wiring never renumbers a user's colours.
- **Strip index** (`k <i>` / `u <i>` on the wire) is wiring detail. It is shown in the Colour
  panel's hint, in every LED's accessible name, in the Identify table, and on the board *while a
  sweep is running* — the one context where strip numbering is the subject.
- The confirmed wiring order ships as the default: per-key strip index `0` is the **bottom-right**
  key and the chain snakes upward (row 3 right-to-left, row 2 left-to-right, …); underglow starts
  bottom-right and runs around the ring. `layout.key_positions` /
  `layout.underglow_positions` override it per index when present and valid.

Recorded positions stay `[row, ordinal-within-row]` for keys — the ordinal, deliberately neither
the scan-matrix column nor the 4×4 grid column (the grid column is *derived* from it for drawing,
exactly as `layout.grid_col()` does) — and `[x, y]` 0–2 for underglow, centre excluded.

The Identify sweep is therefore **confirmation, not setup**: its table starts from the mapping in
force, `Reset to confirmed default` restores the shipped order, and `Write mapping to config`
stores an explicit override. It still refuses to set `verified: true` while any index is
unrecorded, and writes no array at all rather than an all-`null` one. The warning banner appears
**only when a config explicitly sets `layout.verified: false`** — i.e. someone overrode the
mapping and hasn't checked it — not merely because the key is absent.

## Colour and effects

sRGB↔OKLab↔OKLCh conversion, sRGB gamut mapping by chroma reduction, and palette interpolation are
inline in `app.js` (§2). Palette stops are interpolated in OKLCh along the shorter hue arc, which
is why gradients keep their chroma instead of dipping through grey; the CSS gradient previews are
sampled through the same code rather than left to the browser's sRGB lerp. The `rainbow` palette
and effect are generated from equal-lightness OKLCh hues rather than hand-picked hexes.

All ten schema effects render client-side from each LED's normalised position on the board
(`u`, `v`, radius, ring angle), so the preview works with no device attached. Those coordinates use
the **same normalisation the daemon uses** — grid position over grid extent, as in
`layout.key_xy()` / `underglow_xy()`, not pixels in the SVG — so a gradient previewed here is the
gradient the device renders.

## Keyboard and accessibility

- Tab into the device view; every LED is a focusable button — and only LEDs are, so the encoder,
  joystick and touch pad ghosts are skipped entirely. Arrow keys move within a zone: across the 4×4
  key grid (stepping over the slots the non-key controls hold, and between the two halves of the
  wide cap), around the underglow ring, up and down the status stack. Enter/Space selects (or
  records a position mid-sweep). Each LED's accessible name carries its row and grid column, its
  logical index, its strip index, and — for the wide cap — the index it shares a keycap with.
- Tabs are a proper tablist: arrows, Home/End. Eight of them fit one row at 1280 px.
- Palette stop handles are buttons; arrows nudge position by 0.01 (0.05 with Shift).
- Control chips are buttons carrying `aria-pressed`; every binding editor control is a real
  labelled form field, and the trigger cards are plain tab-order DOM in trigger order.
- The shortcut recorder captures on `window` with `capture: true` while it is armed and releases
  the listener when it stops — leaving the Bindings tab stops it, so it can never sit there
  swallowing keystrokes.
- Nothing rebuilds under the cursor on a timer: the 3 s status poll re-renders the warning banner
  and the profile list only when something they display has actually changed, so focus is never
  yanked out of them mid-tab.
- `Cmd/Ctrl+S` saves. Unsaved changes prompt before leaving.
- Dark by default, light under `prefers-color-scheme: light`; two-column at ≥1100 px, single
  column below, readable down to ~900 px and usable narrower.

## Not in this phase

`key.watch` (Phase 6) and the whole `power` object have no editor; both are preserved untouched on
save. Battery renders whatever `/api/status` reports and shows "unavailable" until Phase 8 provides
real readings. Client-side validation is targeted rather than complete — it does check what this
editor could otherwise let you build (a binding with no action or two, an empty action value, an
unparseable `shortcut` spec, a mode with no `encoder`, an out-of-range `activate_key`, a
`timeout_s` below the schema minimum) — but the daemon's `PUT /api/config` is the real validator
and its `errors` are shown in the Config tab.
