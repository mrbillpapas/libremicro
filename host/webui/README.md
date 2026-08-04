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
| POST | `/api/preview/frame` | `{keys:[hex×13],underglow:[hex×8],status:[int×3],ttl:6}` on colour edits, throttled to `device.fps` |
| POST | `/api/preview/effect` | `{effect:{…}}` on effect edits, debounced 140 ms |
| POST | `/api/preview/stop` | `Stop preview`, and when a sweep ends |
| POST | `/api/identify` | `{target:"keys"\|"underglow",index:int}` per sweep step |
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

## Provisional geometry (all of it labelled in the UI)

`docs/HARDWARE.md` leaves three mappings unverified, so none of them is hardcoded as fact. The UI
reads `layout.key_positions` / `layout.underglow_positions` when present and valid, and falls back
per-index to these clearly-labelled provisional defaults:

- **Per-key strip index → cap:** `0→12` in reading order, top row first, left to right.
- **Underglow strip index → ring position:** `0→7` clockwise from the top-left of the 3×3 grid.
  That order is also the traversal order for `direction: "ring"`.
- **Short rows:** the 2-key and 3-key rows are drawn **centred** within the 4-column span. Which
  matrix columns they actually populate is unknown, so the `Short rows` control in the stage
  toolbar can redraw them left- or right-aligned. It is a view preference in `localStorage` only —
  the schema has nowhere to record it, and it does not change what the sweep records.

Recorded key positions are `[row, col]` where `col` is the **ordinal within that physical row**
(0-based, left to right), deliberately *not* the scan-matrix column. Underglow positions are
`[x, y]`, 0–2 each, centre excluded.

Whenever `layout.verified` is not `true`, a banner names which mappings are provisional and links
to the sweep. `Write mapping to config` refuses to set `verified: true` while any index in either
strip is unrecorded, and writes no array at all rather than an all-`null` one.

## Colour and effects

sRGB↔OKLab↔OKLCh conversion, sRGB gamut mapping by chroma reduction, and palette interpolation are
inline in `app.js` (§2). Palette stops are interpolated in OKLCh along the shorter hue arc, which
is why gradients keep their chroma instead of dipping through grey; the CSS gradient previews are
sampled through the same code rather than left to the browser's sRGB lerp. The `rainbow` palette
and effect are generated from equal-lightness OKLCh hues rather than hand-picked hexes.

All ten schema effects render client-side from each LED's normalised position on the board
(`u`, `v`, radius, ring angle), so the preview works with no device attached and spatial effects
follow whatever mapping the config declares.

## Keyboard and accessibility

- Tab into the device view; every LED is a focusable button. Arrow keys move within a zone,
  Enter/Space selects (or records a position mid-sweep).
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
