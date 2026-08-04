# Local web UI — lighting studio (Phase 1)

The browser half of [`docs/ROADMAP.md`](../../docs/ROADMAP.md) Phase 1: a layout-accurate view of
the pad, a colour/palette/effect designer with live preview on the device, the LED identify
sweep, and config export/import.

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
edits only what the panels expose. Bindings, modes and profile structure are read and preserved
but not editable here — that's Phase 5.

## API it expects

| Method | Path | Used for |
|---|---|---|
| GET | `/api/config` | initial load, `Reload` |
| PUT | `/api/config` | `Save`; expects `{"ok":bool,"errors":[...]}` |
| GET | `/api/schema` | fetched and kept for reference (not yet used for validation) |
| GET | `/api/palettes` | built-in palette corpus; **replaces** the fallback set embedded in `app.js` |
| GET | `/api/status` | status bar, polled every 3 s (8 s while unreachable) |
| POST | `/api/preview/frame` | `{keys:[hex×13],underglow:[hex×8],status:[int×3],ttl:6}` on colour edits, throttled to `device.fps`. `keys` is in **logical** order, `underglow` in **ring** order — see below |
| POST | `/api/preview/effect` | `{effect:{…}}` on effect edits, debounced 140 ms |
| POST | `/api/preview/stop` | `Stop preview`, and when a sweep ends |
| POST | `/api/identify` | `{target:"keys"\|"underglow",index:int}` per sweep step — `index` is a **strip** index |
| GET | `/api/export` | `Export` bundle download |
| POST | `/api/import` | `Import` upload |

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
  suggests `Export`, and the identify sweep still lets a mapping be recorded by hand.

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
  reliable. The grouping comes from the `SHARED_KEYCAPS` constant, not from hardcoded positions.
- **Encoder `(0,0)`, joystick `(0,3)`, touch pad `(3,0)`** are drawn as ghost outlines for
  orientation. They are not addressable LEDs, so they are not focusable, not clickable and not
  selectable.
- **8 underglow LEDs, all the same size**, evenly spaced around the square (3×3 minus centre):
  three across the top, one at each side midpoint, three across the bottom. The absent centre slot
  is drawn dashed at the same size, behind the caps — which is where the underglow physically is.
- **3 PWM status LEDs** are a vertical stack at the bottom-left beside the touch pad, where they
  physically sit. Still 0–255 duty, single colour, not RGB.

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
- Tabs are a proper tablist: arrows, Home/End.
- Palette stop handles are buttons; arrows nudge position by 0.01 (0.05 with Shift).
- `Cmd/Ctrl+S` saves. Unsaved changes prompt before leaving.
- Dark by default, light under `prefers-color-scheme: light`; two-column at ≥1100 px, single
  column below, readable down to ~900 px and usable narrower.

## Not in this phase

Binding, mode and profile *editing* (Phase 5), shortcut recording (Phase 5), and per-mode
lighting overrides — `modes[*].lighting` is preserved on save but has no editor, only
`profiles[*].lighting` does. Battery renders whatever `/api/status` reports and shows
"unavailable" until Phase 8 provides real readings. Client-side validation is a handful of
targeted checks; the daemon's `PUT /api/config` is the real validator and its `errors` are shown
in the Config tab.
