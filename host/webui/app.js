/* LibreMicro Studio — web UI (Phase 1 lighting + Phase 5 bindings, profiles, modes).
 *
 * Vanilla ES module. No build step, no dependencies, no network requests other than the
 * daemon's own /api/* endpoints (the daemon may be run offline). Everything here degrades to
 * a fully usable client-side design tool when the daemon or the device is absent.
 *
 * The config held in this page is a view onto host/config/*.json (schema v2) and nothing else;
 * view-only preferences live in localStorage because the schema forbids unknown properties.
 */

/* ============================================================ 1. constants */

const KEY_COUNT = 13;
const UG_COUNT = 8;
const STATUS_COUNT = 3;
const DEFAULT_KEY_ROWS = [2, 4, 4, 3];

/* The 3x3-minus-centre underglow ring, listed clockwise from the top-left corner — layout.py's
 * UNDERGLOW_RING. All eight are the same physical size, evenly spaced around the square (CONFIRMED
 * — docs/HARDWARE.md), which is exactly why each one can own a full eighth of the board's
 * perimeter: see ugBandGeom(). The (gx, gy) pairs are the position identity the config stores, and
 * this order IS the ring numbering: it indexes the underglow array posted to /api/preview/frame and
 * is the traversal order for `direction: "ring"`, so nothing here may be reordered casually.
 * Which STRIP index lights each of these is a separate mapping (DEFAULT_UNDERGLOW_POSITIONS). */
const RING_ORDER = [[0, 0], [1, 0], [2, 0], [2, 1], [2, 2], [1, 2], [0, 2], [0, 1]];

/* Everything on the pad sits on one 4x4 grid, 13 of the 16 slots being key switches — CONFIRMED
 * from the faceplate. These three constants mirror layout.py's KEY_GRID_COLS / SHARED_KEYCAPS /
 * FEATURES and are the only place this file asserts physical placement.
 *
 * KEY_GRID_COLS[row][ordinal] = grid column. The config records key positions as
 * [row, ordinal-within-row] (that is the schema contract and it has not changed); the grid column
 * is a rendering detail derived here, exactly as layout.grid_col() does on the daemon side. */
const GRID_COLS = 4;
const KEY_GRID_COLS = [[1, 2], [0, 1, 2, 3], [0, 1, 2, 3], [1, 2, 3]];

/* Logical key indices that share ONE physical keycap: 13 switches, 12 caps. Logical index runs
 * row-major over populated slots, so 10 and 11 are the bottom row's middle two — the wide cap
 * spanning grid columns 1-2. Both LEDs stay independently addressable (a two-pixel gradient
 * across one cap is the nice use for it); what is unreliable is binding the halves to different
 * actions, since a user cannot choose which half they press. */
const SHARED_KEYCAPS = [[10, 11]];

/* Non-key controls, as [row, gridColumn]. NOT addressable LEDs and never selectable — drawn as
 * ghosts purely so the board is recognisable as the actual pad. */
const FEATURES = [
  { kind: 'encoder', row: 0, gcol: 0, label: 'encoder' },
  { kind: 'joystick', row: 0, gcol: 3, label: 'joystick' },
  { kind: 'touch', row: 3, gcol: 0, label: 'touch pad' },
];

const EFFECT_NAMES = ['solid', 'gradient', 'rainbow', 'breathe', 'chase', 'ripple', 'sparkle', 'wipe', 'comet', 'off'];
const DIRECTIONS = ['horizontal', 'vertical', 'radial', 'ring'];
const TARGETS = ['keys', 'underglow', 'all'];
const BLENDS = ['replace', 'multiply', 'screen', 'overlay'];

const EFFECT_DEFAULTS = { speed: 0.3, intensity: 0.5, direction: 'horizontal', reverse: false, target: 'all', blend: 'replace' };

/* ------------------------------------------------------------------ bindings */

/* Trigger kinds per control, exactly as the schema has them: `triggers` for anything that
 * presses (keys, the touch pad, the rear button, and each joystick direction) and `encoder` for
 * the dial. The encoder has no hold/double because a detent has no duration — see events.py's
 * Recognizer.rotate. */
const KEY_TRIGGERS = ['press', 'release', 'hold', 'double'];
const ENC_TRIGGERS = ['cw', 'ccw', 'press'];

/* The joystick's eight directions, IN events.py's JOY_DIRS ORDER — that order is the direction's
 * `Trigger.index` on the daemon side, so `state.bind.index` for the joystick is an index into
 * this list and nothing here may be reordered. The config stores the NAME (`profiles.*.joystick.n`
 * and friends), so a reorder would not corrupt a document, but it would misname every injected
 * `joy` line. Each direction takes the full `triggers` set: the disc is a free 360°, split into
 * eight 45° sectors, so hold and double work on a direction exactly as they do on a key. */
const JOY_DIRS = ['e', 'ne', 'n', 'nw', 'w', 'sw', 's', 'se'];
const JOY_LABEL = {
  n: 'north', ne: 'north-east', e: 'east', se: 'south-east',
  s: 'south', sw: 'south-west', w: 'west', nw: 'north-west',
};
/* Where each direction sits in the 3x3 compass the picker draws — the direction IS its position,
 * which is the whole reason the picker is a compass and not a list. [row, col]. */
const JOY_CELL = {
  nw: [0, 0], n: [0, 1], ne: [0, 2],
  w: [1, 0], e: [1, 2],
  sw: [2, 0], s: [2, 1], se: [2, 2],
};

const TRIGGER_LABEL = {
  press: 'Press', release: 'Release', hold: 'Hold', double: 'Double tap',
  cw: 'Turn clockwise', ccw: 'Turn anticlockwise',
};

/* device.hold_ms / double_ms defaults — events.py DEFAULT_HOLD_MS / DEFAULT_DOUBLE_MS. */
const DEFAULT_HOLD_MS = 450;
const DEFAULT_DOUBLE_MS = 280;

/* The nine action keys of `binding`, which is a oneOf: exactly one may be present. `flash` is
 * not one of them — it's the optional confirmation colour and rides along with any of them. */
const BINDING_TYPES = [
  { key: 'launch', label: 'Launch app', input: 'line', ph: 'Slack', hint: 'Opened with <code>open -a</code>. The application name, not a path.' },
  { key: 'shortcut', label: 'Keyboard shortcut', input: 'shortcut', ph: 'cmd+shift+4', hint: 'Synthesised by the native helper. Modifiers: <code>cmd ctrl opt shift fn</code>, then one key, e.g. <code>cmd+shift+4</code> or <code>f13</code>.' },
  { key: 'text', label: 'Type text', input: 'multiline', ph: 'Thanks!\nBill', hint: 'Typed at the cursor, any Unicode. Newlines arrive as real Return presses.' },
  { key: 'shell', label: 'Run shell command', input: 'multiline', ph: "open -na 'Google Chrome'", hint: 'Run by the daemon. Trigger context arrives as <code>LM_*</code> environment variables.' },
  { key: 'script', label: 'Run script file', input: 'line', ph: '~/bin/deploy.sh', hint: 'Path to an executable file. Same <code>LM_*</code> environment as <code>shell</code>.' },
  { key: 'applescript', label: 'Run AppleScript', input: 'multiline', ph: 'tell application "Finder" to activate', hint: 'Source, run through <code>osascript</code>.' },
  { key: 'mode', label: 'Activate mode', input: 'mode', ph: 'media', hint: 'Switches to a mode of this profile until it times out or you switch away.' },
  { key: 'profile', label: 'Switch profile', input: 'profile', ph: 'coding', hint: 'A profile name, or <code>next</code> / <code>prev</code> to cycle.' },
  { key: 'action', label: 'Built-in action', input: 'action', ph: '', hint: 'Implemented natively by the daemon.' },
];
const BINDING_KEYS = BINDING_TYPES.map((t) => t.key);
const bindingType = (k) => BINDING_TYPES.find((t) => t.key === k) || null;

/* The schema's `action` enum, split by what implements it: the media half goes through the same
 * native helper as `shortcut` (keys.py MEDIA_ACTIONS — they're NX aux-control events, not
 * keycodes), the rest is daemon-side and works with no helper built. */
const MEDIA_ACTIONS = ['vol_up', 'vol_down', 'mute', 'play_pause', 'next_track', 'prev_track', 'bright_up', 'bright_down'];
const NATIVE_ACTIONS = ['desk_up', 'desk_down', 'stand_sit', 'sleep', 'lock', 'profile_next', 'profile_prev', 'reload_config'];
const ACTION_ENUM = MEDIA_ACTIONS.concat(NATIVE_ACTIONS);

/* ---------------------------------------------------------- shortcut grammar */

/* Mirrors keys.py: canonical modifier order is Apple's, fn first, and a spec is
 * `mod+mod+key`. Kept in the same order so a recorded chord normalises to exactly the string
 * parse_shortcut() would produce. */
const MODIFIERS = ['fn', 'ctrl', 'opt', 'shift', 'cmd'];

/* KeyboardEvent.code -> keys.py canonical key name. `code` rather than `key` because a chord's
 * `key` is layout- and modifier-dependent (opt+a is "å", shift+4 is "$") while the helper wants
 * the physical key. Anything absent here is a key we cannot name, and the recorder says so
 * instead of guessing. */
const CODE_TO_KEY = (() => {
  const m = {};
  for (const c of 'abcdefghijklmnopqrstuvwxyz') m['Key' + c.toUpperCase()] = c;
  for (let d = 0; d <= 9; d++) { m['Digit' + d] = String(d); m['Numpad' + d] = 'kp' + d; }
  for (let f = 1; f <= 20; f++) m['F' + f] = 'f' + f;
  return Object.assign(m, {
    Escape: 'escape', Tab: 'tab', Enter: 'return', Space: 'space',
    Backspace: 'delete', Delete: 'forwarddelete',
    Home: 'home', End: 'end', PageUp: 'pageup', PageDown: 'pagedown',
    ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down',
    Help: 'help', Insert: 'help', CapsLock: 'capslock',
    Minus: 'minus', Equal: 'equal', BracketLeft: 'leftbracket', BracketRight: 'rightbracket',
    Backslash: 'backslash', Semicolon: 'semicolon', Quote: 'quote',
    Comma: 'comma', Period: 'period', Slash: 'slash', Backquote: 'grave',
    NumpadDecimal: 'kpdecimal', NumpadAdd: 'kpplus', NumpadSubtract: 'kpminus',
    NumpadMultiply: 'kpmultiply', NumpadDivide: 'kpdivide', NumpadEqual: 'kpequals',
    NumpadEnter: 'kpenter', NumLock: 'kpclear', Clear: 'kpclear',
  });
})();

const MODIFIER_CODES = {
  MetaLeft: 'cmd', MetaRight: 'cmd', ControlLeft: 'ctrl', ControlRight: 'ctrl',
  AltLeft: 'opt', AltRight: 'opt', ShiftLeft: 'shift', ShiftRight: 'shift',
  OSLeft: 'cmd', OSRight: 'cmd', Fn: 'fn', FnLock: 'fn',
};

/* Chords the browser or macOS takes for itself, so a keydown listener never sees them. There is
 * no API that reports this, and a page cannot opt out — hence a curated list plus the
 * lost-focus detection in the recorder, and a text field that is always there as the way out. */
const RESERVED_CHORDS = [
  { spec: 'cmd+q', who: 'macOS quits the browser' },
  { spec: 'cmd+w', who: 'the browser closes the tab' },
  { spec: 'cmd+shift+w', who: 'the browser closes the window' },
  { spec: 'cmd+t', who: 'the browser opens a tab' },
  { spec: 'cmd+shift+t', who: 'the browser reopens a tab' },
  { spec: 'cmd+n', who: 'the browser opens a window' },
  { spec: 'cmd+shift+n', who: 'the browser opens a private window' },
  { spec: 'cmd+m', who: 'macOS minimises the window' },
  { spec: 'cmd+h', who: 'macOS hides the app' },
  { spec: 'cmd+opt+h', who: 'macOS hides the others' },
  { spec: 'cmd+space', who: 'Spotlight' },
  { spec: 'ctrl+cmd+space', who: 'the emoji picker' },
  { spec: 'cmd+tab', who: 'the macOS app switcher' },
  { spec: 'cmd+grave', who: 'the macOS window switcher' },
  { spec: 'ctrl+cmd+q', who: 'macOS locks the screen' },
  { spec: 'cmd+opt+escape', who: 'Force Quit' },
  { spec: 'cmd+shift+3', who: 'the macOS screenshot service' },
  { spec: 'cmd+shift+4', who: 'the macOS screenshot service' },
  { spec: 'cmd+shift+5', who: 'the macOS screenshot service' },
  { spec: 'cmd+ctrl+f', who: 'macOS full-screen' },
];
const reservedChord = (spec) => RESERVED_CHORDS.find((r) => r.spec === spec) || null;

/* Strip index -> physical position, CONFIRMED on hardware (layout.py DEFAULT_KEY_POSITIONS /
 * DEFAULT_UNDERGLOW_POSITIONS). Both chains are wired as one serpentine starting at the
 * bottom-right, so strip order is NOT reading order: index 0 is the bottom-right key, and the
 * top-left key is index 11. Every Creator Micro 2 is wired the same way, which is why this ships
 * as a built-in default instead of something each owner rediscovers with the identify sweep.
 * `layout.key_positions` / `layout.underglow_positions` still override it per index. */
const DEFAULT_KEY_POSITIONS = [
  [3, 2], [3, 1], [3, 0],
  [2, 0], [2, 1], [2, 2], [2, 3],
  [1, 3], [1, 2], [1, 1], [1, 0],
  [0, 0], [0, 1],
];
const DEFAULT_UNDERGLOW_POSITIONS = [
  [2, 2], [1, 2], [0, 2],
  [0, 1],
  [0, 0], [1, 0], [2, 0],
  [2, 1],
];

/* SVG geometry, in one place. The board is a square: the 8 underglow LEDs tile its WHOLE
 * perimeter, an eighth of the perimeter length each, and the 4x4 key grid sits inside that band
 * sharing its centre. Nothing here is derived from row widths — every slot has a fixed grid
 * position now. */
const GEO = {
  board: { x: 8, y: 8, w: 384, h: 384, r: 22 },
  /* Underglow is ONE continuous band around the whole perimeter, cut into eight equal shares.
   * `inset` is the distance from the board edge to the band's CENTRELINE, `thick` is the band
   * width (the stroke-width of the shared path), `r` the centreline's corner radius. See
   * ugBandGeom(). Nothing about a single LED's size lives here any more: a share's length is
   * always exactly perimeter/8, by construction. */
  ug: { inset: 21, thick: 22, r: 14 },
  /* The key block, sized to leave clear air between it and the perimeter band on all four
   * sides: the band's inner edge is at board + inset - thick/2 = 40 / 360. */
  keyBand: { x: 54, y: 80, w: 292, h: 240 },
  key: { gap: 10, r: 9 },
  /* The 3 PWM status LEDs: a small vertical column immediately LEFT of the touch pad and inside
   * the touch pad's own grid slot, at the bottom-left of the key block — three small marks
   * beside the pad circle, as the faceplate has them. `pad` is the inset from the slot's left
   * edge and `clear` the gap left before the touch glyph, which centres in what remains. */
  status: { w: 16, h: 12, gap: 5, r: 4, pad: 4, clear: 5 },
  noteY: [404],
  vbW: 400, vbH: 412,
  /* Heights for the 3D view, in the same board units as everything above — the 3D scene is the
   * SAME geometry extruded, never a second layout. See §9b. */
  z: {
    board: 9,          // board thickness
    cap: 11,           // keycap height above the board's top surface
    capTaper: 2.6,     // how much narrower the cap's top face is than its base
    ug: 4.5,           // how far the underglow strip sits below the board's top surface
    // How far the underglow strip stands out past the board outline. Enough that the rim is still
    // a readable band from straight overhead, where the board itself hides everything inside it.
    ugOut: 8,
    status: 0.8,       // status LEDs are flush-ish
    knob: 7,           // encoder / joystick body height
    ground: 46,        // how far below the board the desk plane sits
  },
};

/* ======================================================= 2. colour science */

const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const wrap01 = (t) => t - Math.floor(t);
const lerp = (a, b, t) => a + (b - a) * t;

function hexToRgb(hex) {
  let h = String(hex || '').trim().replace(/^#/, '');
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return [0, 0, 0];
  const n = parseInt(h, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

function rgbToHex(rgb) {
  return rgb.map((c) => clamp(Math.round(c * 255), 0, 255).toString(16).padStart(2, '0')).join('');
}

const isHex6 = (s) => /^[0-9a-fA-F]{6}$/.test(String(s || '').replace(/^#/, ''));

const toLinear = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
const toGamma = (c) => (c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055);

function rgbToOklab([r, g, b]) {
  const R = toLinear(r), G = toLinear(g), B = toLinear(b);
  const l = Math.cbrt(0.4122214708 * R + 0.5363325363 * G + 0.0514459929 * B);
  const m = Math.cbrt(0.2119034982 * R + 0.6806995451 * G + 0.1073969566 * B);
  const s = Math.cbrt(0.0883024619 * R + 0.2817188376 * G + 0.6299787005 * B);
  return [
    0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
  ];
}

/** OKLab -> sRGB. May return values outside 0..1 (out of gamut); callers decide. */
function oklabToRgbRaw([L, A, B]) {
  const l_ = L + 0.3963377774 * A + 0.2158037573 * B;
  const m_ = L - 0.1055613458 * A - 0.0638541728 * B;
  const s_ = L - 0.0894841775 * A - 1.2914855480 * B;
  const l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
  return [
    toGamma(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    toGamma(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    toGamma(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s),
  ];
}

const oklabToOklch = ([L, a, b]) => {
  const C = Math.hypot(a, b);
  let h = (Math.atan2(b, a) * 180) / Math.PI;
  if (h < 0) h += 360;
  return [L, C, h];
};
const oklchToOklab = ([L, C, h]) => {
  const rad = (h * Math.PI) / 180;
  return [L, C * Math.cos(rad), C * Math.sin(rad)];
};

const inGamut = (rgb, eps = 0.0005) => rgb.every((c) => c >= -eps && c <= 1 + eps);

/** OKLCh -> sRGB, reducing chroma until in gamut (constant L and H) then clamping. */
function oklchToRgb(lch) {
  const raw = oklabToRgbRaw(oklchToOklab(lch));
  if (inGamut(raw)) return raw.map((c) => clamp(c, 0, 1));
  let lo = 0, hi = lch[1];
  for (let i = 0; i < 18; i++) {
    const mid = (lo + hi) / 2;
    if (inGamut(oklabToRgbRaw(oklchToOklab([lch[0], mid, lch[2]])))) lo = mid; else hi = mid;
  }
  return oklabToRgbRaw(oklchToOklab([lch[0], lo, lch[2]])).map((c) => clamp(c, 0, 1));
}

const rgbToOklch = (rgb) => oklabToOklch(rgbToOklab(rgb));
const hexToOklch = (hex) => rgbToOklch(hexToRgb(hex));
const oklchToHex = (lch) => rgbToHex(oklchToRgb(lch));

/** Perceptual mix of two OKLCh colours, taking the shorter hue arc. */
function mixOklch(a, b, t) {
  const [L1, C1, H1] = a, [L2, C2, H2] = b;
  let h1 = H1, h2 = H2;
  if (C1 < 0.002) h1 = h2;
  if (C2 < 0.002) h2 = h1;
  let dh = h2 - h1;
  if (dh > 180) dh -= 360; else if (dh < -180) dh += 360;
  return [lerp(L1, L2, t), lerp(C1, C2, t), h1 + dh * t];
}

/** WCAG-ish relative luminance, used only to pick readable label ink over a swatch. */
const relLum = ([r, g, b]) => 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
const inkFor = (rgb) => (relLum(rgb) > 0.32 ? '#0d1015' : '#f2f5fa');

function blendRgb(mode, base, top) {
  switch (mode) {
    case 'multiply': return [0, 1, 2].map((i) => base[i] * top[i]);
    case 'screen': return [0, 1, 2].map((i) => 1 - (1 - base[i]) * (1 - top[i]));
    case 'overlay': return [0, 1, 2].map((i) => (base[i] < 0.5 ? 2 * base[i] * top[i] : 1 - 2 * (1 - base[i]) * (1 - top[i])));
    default: return top;
  }
}

/* ============================================== 3. palettes (built-in corpus) */

/* A perceptual spectrum: equal-lightness OKLCh hues, so no muddy band and no blown-out yellow.
 * Generated rather than hand-picked hexes precisely because the maths is available here. */
function spectrumStops(count, L, C, from = 20) {
  const out = [];
  for (let i = 0; i < count; i++) {
    const t = i / count;
    out.push({ pos: +(i / (count - 1)).toFixed(4), color: oklchToHex([L, C, from + t * 360]) });
  }
  return out;
}

const BUILTIN_PALETTES = {
  rainbow: { label: 'Rainbow (OKLCh)', stops: spectrumStops(9, 0.74, 0.15), cyclic: true },
  spectrum: { label: 'Spectrum (deep)', stops: spectrumStops(7, 0.58, 0.17, 250), cyclic: true },
  sunset: { label: 'Sunset', stops: [{ pos: 0, color: '2b0a3d' }, { pos: 0.45, color: 'd1495b' }, { pos: 0.75, color: 'ff9505' }, { pos: 1, color: 'ffd60a' }] },
  ocean: { label: 'Ocean', stops: [{ pos: 0, color: '03045e' }, { pos: 0.4, color: '0077b6' }, { pos: 0.72, color: '00b4d8' }, { pos: 1, color: '90e0ef' }] },
  forest: { label: 'Forest', stops: [{ pos: 0, color: '081c15' }, { pos: 0.4, color: '1b4332' }, { pos: 0.75, color: '40916c' }, { pos: 1, color: '95d5b2' }] },
  lava: { label: 'Lava', stops: [{ pos: 0, color: '03071e' }, { pos: 0.35, color: '6a040f' }, { pos: 0.7, color: 'dc2f02' }, { pos: 1, color: 'faa307' }] },
  ice: { label: 'Ice', stops: [{ pos: 0, color: '0a1128' }, { pos: 0.45, color: '1282a2' }, { pos: 0.78, color: '6cc6cb' }, { pos: 1, color: 'e7f6f8' }] },
  candy: { label: 'Candy', stops: [{ pos: 0, color: 'ff70a6' }, { pos: 0.34, color: 'ff9770' }, { pos: 0.67, color: 'ffd670' }, { pos: 1, color: 'e9ff70' }], cyclic: true },
  cyberpunk: { label: 'Cyberpunk', stops: [{ pos: 0, color: '05010d' }, { pos: 0.33, color: '6a00f4' }, { pos: 0.66, color: 'ff006e' }, { pos: 1, color: '00f5d4' }], cyclic: true },
  aurora: { label: 'Aurora', stops: [{ pos: 0, color: '03071e' }, { pos: 0.42, color: '1b998b' }, { pos: 0.74, color: '2dd4bf' }, { pos: 1, color: 'c7f9cc' }] },
  ember: { label: 'Ember', stops: [{ pos: 0, color: '000000' }, { pos: 0.5, color: '9d2c00' }, { pos: 1, color: 'ffb703' }] },
  mono: { label: 'Mono', stops: [{ pos: 0, color: '000000' }, { pos: 1, color: 'ffffff' }] },
};

let paletteRev = 0;
const palCache = new Map();

function allPalettes() {
  return { ...state.builtins, ...(state.config?.palettes || {}) };
}

/** Compile a palette to sorted OKLCh stops once per edit generation. */
function compiledPalette(name) {
  const key = `${paletteRev}:${name}`;
  if (palCache.has(key)) return palCache.get(key);
  const pal = allPalettes()[name];
  let out = null;
  if (pal && Array.isArray(pal.stops) && pal.stops.length) {
    const stops = pal.stops
      .map((s) => ({ pos: clamp(Number(s.pos) || 0, 0, 1), lch: hexToOklch(s.color) }))
      .sort((a, b) => a.pos - b.pos);
    out = { stops, cyclic: !!pal.cyclic };
  }
  palCache.set(key, out);
  return out;
}

/** Sample a compiled palette at t. Interpolates in OKLCh; wraps when cyclic. */
function samplePalette(cp, t) {
  if (!cp) return [0, 0, 0];
  const s = cp.stops;
  if (s.length === 1) return oklchToRgb(s[0].lch);
  let pts = s, u = cp.cyclic ? wrap01(t) : clamp(t, 0, 1);
  if (cp.cyclic) {
    pts = s.concat([{ pos: s[0].pos + 1, lch: s[0].lch }]);
    if (u < s[0].pos) u += 1;
  }
  if (u <= pts[0].pos) return oklchToRgb(pts[0].lch);
  const last = pts[pts.length - 1];
  if (u >= last.pos) return oklchToRgb(last.lch);
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1];
    if (u <= b.pos) {
      const span = b.pos - a.pos;
      const f = span <= 0 ? 0 : (u - a.pos) / span;
      return oklchToRgb(mixOklch(a.lch, b.lch, f));
    }
  }
  return oklchToRgb(last.lch);
}

/** CSS linear-gradient string sampled through OKLCh (browsers would lerp in sRGB otherwise). */
function paletteCss(name, steps = 32, angle = '90deg') {
  const cp = compiledPalette(name);
  if (!cp) return 'linear-gradient(90deg,#333,#333)';
  const parts = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    parts.push(`#${rgbToHex(samplePalette(cp, t))} ${(t * 100).toFixed(1)}%`);
  }
  return `linear-gradient(${angle}, ${parts.join(',')})`;
}

/* ============================================================== 4. utility */

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, kids = []) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k === 'html') n.innerHTML = v;
    else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v === true ? '' : v);
  }
  for (const k of [].concat(kids)) if (k) n.append(k);
  return n;
};
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) n.setAttribute(k, v);
  return n;
};

const clone = (o) => (typeof structuredClone === 'function' ? structuredClone(o) : JSON.parse(JSON.stringify(o)));

function throttleTrailing(fn, msFn) {
  let timer = null, last = 0, pending = null;
  return (...args) => {
    pending = args;
    if (timer) return;
    const ms = typeof msFn === 'function' ? msFn() : msFn;
    const wait = Math.max(0, ms - (performance.now() - last));
    timer = setTimeout(() => {
      timer = null; last = performance.now();
      const a = pending; pending = null;
      try { fn(...a); } catch (e) { console.error(e); }
    }, wait);
  };
}

function debounce(fn, ms) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => { try { fn(...args); } catch (e) { console.error(e); } }, ms);
  };
}

const prefs = {
  read(k, dflt) {
    try { const v = localStorage.getItem('lm.' + k); return v === null ? dflt : JSON.parse(v); }
    catch { return dflt; }
  },
  write(k, v) { try { localStorage.setItem('lm.' + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

function toast(msg, kind = 'info', ms = 3600) {
  const t = el('div', { class: 'toast', 'data-kind': kind, text: msg });
  $('toasts').append(t);
  setTimeout(() => t.remove(), ms);
}

/* ================================================================= 5. API */

/* Every call resolves — never rejects — so a missing daemon can't produce an unhandled
 * rejection anywhere in the UI. `reachable` distinguishes "no daemon" from "daemon said no". */
async function req(method, path, body) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 6000);
  try {
    const res = await fetch(path, {
      method,
      signal: ctl.signal,
      cache: 'no-store',
      headers: body === undefined ? undefined : { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await res.text();
    let data = null;
    if (text) { try { data = JSON.parse(text); } catch { data = null; } }
    // A 404 means something is serving this port but it isn't the daemon — it implements
    // every /api route, so a missing route is not "the daemon said no". Without this,
    // any static file server on the port reads as a healthy daemon. 501 is the same story
    // from the other side: that is what a plain static server answers a POST with, and the
    // daemon never does.
    const isDaemon = res.status !== 404 && res.status !== 501;
    setDaemonReachable(isDaemon);
    if (!res.ok) {
      return { ok: false, reachable: isDaemon, status: res.status, data,
               error: isDaemon ? `HTTP ${res.status}` : 'daemon unreachable' };
    }
    return { ok: true, reachable: true, status: res.status, data };
  } catch (err) {
    setDaemonReachable(false);
    return { ok: false, reachable: false, status: 0, data: null, error: err && err.name === 'AbortError' ? 'timed out' : 'daemon unreachable' };
  } finally {
    clearTimeout(timer);
  }
}
const api = {
  getConfig: () => req('GET', '/api/config'),
  putConfig: (c) => req('PUT', '/api/config', c),
  getSchema: () => req('GET', '/api/schema'),
  getPalettes: () => req('GET', '/api/palettes'),
  getStatus: () => req('GET', '/api/status'),
  // The frame the daemon is putting on the device RIGHT NOW: `keys` by logical index,
  // `underglow` by ring position, `brightness` after idle dimming. This is what the device
  // view mirrors instead of re-running the effects in JavaScript — see §10b.
  getFrame: () => req('GET', '/api/frame'),
  previewFrame: (f) => req('POST', '/api/preview/frame', f),
  previewEffect: (e) => req('POST', '/api/preview/effect', { effect: e }),
  previewStop: () => req('POST', '/api/preview/stop'),
  identify: (target, index) => req('POST', '/api/identify', { target, index }),
  exportCfg: () => req('GET', '/api/export'),
  importCfg: (b) => req('POST', '/api/import', b),
  // Phase 5. `simulate` injects an input event as though the pad had sent it — the only way to
  // fire a binding until firmware v2 is flashed, and the way to test one afterwards without
  // reaching for the pad. `events` is a polled tail of what the daemon has seen.
  simulate: (body) => req('POST', '/api/simulate', body),
  getEvents: (since) => req('GET', '/api/events?since=' + (since | 0)),
  setProfile: (profile) => req('POST', '/api/profile', { profile }),
  setMode: (mode) => req('POST', '/api/mode', { mode }),
};

/* =============================================================== 6. state */

const OFFLINE_CONFIG = {
  version: 2,
  device: { port: 'auto', brightness: 200, fps: 30 },
  // No key_positions / underglow_positions and no `verified`: the confirmed wiring order is the
  // default on both sides, so a config only carries these once someone overrides them.
  layout: { key_rows: DEFAULT_KEY_ROWS.slice() },
  palettes: {},
  active_profile: 'default',
  profiles: {
    default: {
      label: 'Default',
      lighting: {
        underglow: '0b1020',
        status_leds: [0, 0, 0],
        effect: { name: 'gradient', palette: 'ocean', target: 'all', direction: 'horizontal', speed: 0.15, intensity: 0.5, blend: 'replace' },
      },
    },
  },
  power: {},
  webui: { enabled: true, host: '127.0.0.1', port: 8777 },
};

const state = {
  config: null,
  schema: null,
  builtins: BUILTIN_PALETTES,
  daemonReachable: null,
  bootDone: false,
  status: { connected: false, port: null, active_profile: null, active_mode: null, battery: null, previewing: false, input_events: null, keys: null, firmware: null },
  loadedFromDaemon: false,
  dirty: false,
  profile: null,                 // profile name being edited
  scope: null,                   // null = the profile's own layer, else a mode name
  sel: null,                     // {zone:'keys'|'underglow'|'status', index, pos}
  bind: { control: 'key', index: 0 },  // control the Bindings panel is editing
  modeSel: null,                 // mode selected in the Profiles panel
  rec: null,                     // active shortcut recorder, or null
  events: [],                    // newest last, as /api/events returns them
  eventSeq: 0,
  inputSeen: null,               // status/events input_events_seen: has the pad EVER spoken
  hits: new Map(),               // control key -> {at, source} for the board highlight
  evPoll: true,
  evFlash: true,
  capsDismissed: false,
  palSel: null,                  // palette name selected in the designer
  palStop: 0,
  geom: null,
  geomSig: '',
  // The preview clock. It advances only while a preview is on screen or live preview is streaming
  // this page's frames, so a mirrored board costs nothing and an "Off" board is genuinely still.
  anim: { t: 0, lastTs: 0, lastPaint: 0 },
  live: false,
  identify: { active: false, target: 'keys', index: 0, map: { keys: [], underglow: [] } },

  /* What the device view is showing and how it is drawn.
   *
   * `source` is the user's choice of WHERE the picture comes from — 'device', 'preview' or 'off'
   * (see §10). `threeD` is only how it is drawn and changes no geometry. `onscreen` is set by an
   * IntersectionObserver so a scrolled-away board stops asking the daemon for frames. `focusLed`
   * tracks DOM focus inside the SVG so the 3D view can draw a focus ring for it, since in 3D the
   * focusable element is the invisible SVG cell and not anything on the canvas. */
  view: { source: 'device', threeD: false, onscreen: true, focusLed: null },
  /* The last frame read from GET /api/frame, i.e. what is physically on the pad. Colours are
   * kept as rgb triples exactly as the endpoint gave them (undimmed); `brightness` is applied
   * when painting, so the raw values stay available for the accessible name and `data-hex`. */
  mirror: {
    keys: null, ug: null, status: null,
    brightness: 255, connected: false,
    at: 0, seq: 0, ok: false, error: null,
  },
  localFrame: null,              // last locally simulated frame — what live preview pushes
  lastFrame: null,               // last frame actually painted, from either source
  jsonStale: true,
  previewChannel: null,          // which preview endpoint currently owns the device
  lastEffectPush: 0,
};

const currentProfile = () => (state.config?.profiles || {})[state.profile] || null;
const profileModes = () => currentProfile()?.modes || {};

/* The layer being edited. `null` is the profile's own `keys` / `encoder` / `lighting`; a mode
 * name is that mode's overrides. This mirrors dispatch.py's resolution order exactly — mode
 * first, then profile — so what the editor calls a layer is what the daemon calls one. */
const currentMode = () => (state.scope ? profileModes()[state.scope] || null : null);
const scopeLabel = () => (state.scope ? `mode “${state.scope}”` : 'profile default');
const scopePath = (tail) => `profiles.${state.profile || '?'}`
  + (state.scope ? `.modes.${state.scope}` : '') + (tail ? '.' + tail : '');

/** The container being edited: a mode object when scoped to one, else the profile. */
const scopeOwner = () => (state.scope ? currentMode() : currentProfile());

const currentLighting = () => scopeOwner()?.lighting || null;
const currentEffect = () => currentLighting()?.effect || null;
const deviceFps = () => clamp(Number(state.config?.device?.fps) || 30, 1, 60);
const holdMs = () => clamp(Math.round(Number(state.config?.device?.hold_ms ?? DEFAULT_HOLD_MS)), 50, 5000);
const doubleMs = () => clamp(Math.round(Number(state.config?.device?.double_ms ?? DEFAULT_DOUBLE_MS)), 50, 2000);

function ensureLighting() {
  const o = scopeOwner();
  if (!o) return null;
  if (!o.lighting || typeof o.lighting !== 'object') o.lighting = {};
  return o.lighting;
}

/** What the pad actually renders in this scope: mode lighting shallow-merged over the
 *  profile's, which is renderer.py's `lighting.update(mode_spec["lighting"])`. */
function effectiveLighting() {
  const base = currentProfile()?.lighting || {};
  const over = state.scope ? currentMode()?.lighting || {} : {};
  return { ...base, ...over };
}

function keyEntry(index, create = false) {
  const o = scopeOwner();
  if (!o) return null;
  if (!Array.isArray(o.keys)) { if (!create) return null; o.keys = []; }
  let k = o.keys.find((x) => x && x.index === index);
  if (!k && create) { k = { index }; o.keys.push(k); o.keys.sort((a, b) => a.index - b.index); }
  return k || null;
}

/** A key's rendered base colour: the mode's override if it has one, else the profile's —
 *  renderer.py applies the profile's key list and then the mode's on top. */
function keyColorOf(index) {
  const own = keyEntry(index);
  if (own && isHex6(own.color)) return own.color;
  if (!state.scope) return null;
  const p = currentProfile();
  const base = (Array.isArray(p?.keys) ? p.keys : []).find((x) => x && x.index === index);
  return base && isHex6(base.color) ? base.color : null;
}

/** Drop a key entry that has nothing left but its index — an inert `{index: n}` is legal but
 *  noise, and the colour editor already prunes them the same way. */
function pruneKeyEntry(index) {
  const o = scopeOwner();
  const k = o && Array.isArray(o.keys) ? o.keys.find((x) => x && x.index === index) : null;
  if (!k) return;
  if (k.on && !Object.keys(k.on).length) delete k.on;
  if (Object.keys(k).length === 1) o.keys = o.keys.filter((x) => x !== k);
  if (Array.isArray(o.keys) && !o.keys.length) delete o.keys;
}

/* ============================================================ 7. geometry */

function keyRows() {
  const r = state.config?.layout?.key_rows;
  return Array.isArray(r) && r.length && r.every((n) => Number.isInteger(n) && n > 0) ? r : DEFAULT_KEY_ROWS;
}

/** Whether config declares the shipped index mapping wrong / unchecked for this unit. */
const mappingVerified = () => state.config?.layout?.verified !== false;

function readPositions(arr, count, fallback, max) {
  let fromDefaults = false;
  const out = [];
  for (let i = 0; i < count; i++) {
    const v = Array.isArray(arr) ? arr[i] : undefined;
    const good = Array.isArray(v) && v.length === 2 && v.every((n) => Number.isInteger(n) && n >= 0 && (max === undefined || n <= max));
    if (good) out.push([v[0], v[1]]);
    else { out.push(fallback[i] ? fallback[i].slice() : null); fromDefaults = true; }
  }
  return { positions: out, fromDefaults };
}

/** Grid column for every key, per row: KEY_GRID_COLS when the row width matches the real pad. */
function gridColsFor(rows) {
  return rows.map((n, r) => {
    const confirmed = KEY_GRID_COLS[r];
    if (confirmed && confirmed.length === n) return confirmed.slice();
    // A config with row widths the faceplate doesn't have: fall back to left-aligned columns
    // rather than pretending to know where those keys sit. renderStageNote() says so.
    return Array.from({ length: n }, (_, i) => i);
  });
}

/** Logical key index -> [row, ordinal within row] for the given row widths. */
function logicalPos(rows, logical) {
  let n = logical;
  for (let r = 0; r < rows.length; r++) {
    if (n < rows[r]) return [r, n];
    n -= rows[r];
  }
  return null;
}

/* ---------------------------------------------------------- underglow band

 * The eight underglow LEDs tile the WHOLE perimeter, an eighth of it each.
 *
 * Why that works out to exact eighths without any trigonometry: the eight ring positions are the
 * four corners and the four edge midpoints of the 3x3-minus-centre grid, so going clockwise you
 * alternate corner, edge-midpoint, corner, edge-midpoint. On a rounded SQUARE the arc-length step
 * from a straight edge's midpoint to the adjacent corner arc's midpoint is (side)/2 + (pi*r/2)/2 —
 * half a straight plus half a corner arc — and that is the same number for all eight steps, eight
 * of which sum to the full perimeter. So adjacent positions really are exactly L/8 apart, and each
 * LED owns the L/8 band CENTRED on its own point.
 *
 * That means the four corner LEDs' bands straddle a corner (an L, L/16 down each adjoining side)
 * while the four edge-midpoint bands lie flat on one side. That is correct and wanted: real
 * underglow diffuses around a corner.
 *
 * Rendering: one shared rounded-rect path, and each LED is its own copy of it showing a single
 * dash — dasharray `L/8, 7L/8`, offset placing that dash over its own share, stroke-width the band
 * thickness, `stroke-linecap: butt` so neighbours abut exactly. Each share stays an individually
 * clickable, focusable, colourable element, and the shares tile L with no seam and no overlap
 * because they are literally slices of one length.
 *
 * The path starts at a band BOUNDARY, not at a ring point, so no share is split across the path's
 * start/end: `d0` puts the start point on the top edge exactly L/16 short of the top-left corner
 * arc's midpoint, which is ring position 0's centre. Ring position i's share therefore begins at
 * ((i - 1) mod 8) * L/8 — `ringSlot` below. */

/** The band centreline as one closed rounded-rect path, plus its analytic length. */
function ugBandGeom() {
  const b = GEO.board, ins = GEO.ug.inset;
  const x = b.x + ins, y = b.y + ins, w = b.w - 2 * ins, h = b.h - 2 * ins;
  const r = clamp(GEO.ug.r, 0, Math.min(w, h) / 2);
  // The board is square, so both straights are the same length and the eighths are exact. A
  // non-square board would still tile, just not into equal shares — hence the assertion.
  const straight = w - 2 * r;
  const L = 2 * (w - 2 * r) + 2 * (h - 2 * r) + 2 * Math.PI * r;
  const d0 = clamp(straight / 4 - (Math.PI * r) / 8, 0, straight);
  const arc = (px, py) => `A ${r},${r} 0 0 1 ${px},${py}`;
  const d = `M ${x + r + d0},${y} H ${x + w - r} ${arc(x + w, y + r)}`
    + ` V ${y + h - r} ${arc(x + w - r, y + h)}`
    + ` H ${x + r} ${arc(x, y + h - r)}`
    + ` V ${y + r} ${arc(x + r, y)}`
    + ` H ${x + r + d0} Z`;
  return { x, y, w, h, r, d, L };
}

/** Where a ring position sits on the band centreline: a corner arc's midpoint, or an edge's. */
function ugRingPoint(gx, gy, bg) {
  const k = bg.r * (1 - Math.SQRT1_2);           // corner arc midpoint, inset from the sharp corner
  const on = (g, other, span) => (
    g === 1 ? span / 2                            // an edge midpoint: halfway along that side
      : other === 1 ? (g === 0 ? 0 : span)        // ...on the near or far side
        : (g === 0 ? k : span - k));              // a corner: 45 degrees round its arc
  return { cx: bg.x + on(gx, gy, bg.w), cy: bg.y + on(gy, gx, bg.h) };
}

/** A path's own measured length, which is what the dash engine will use. */
function pathLength(el, fallback) {
  try { const l = el.getTotalLength(); return l > 0 ? l : fallback; } catch { return fallback; }
}

/** Check the eight dashes really do tile the whole perimeter — every share a distinct eighth, the
 *  eighths summing to the measured total, and each one CENTRED on its own ring point. Cheap, runs
 *  once per rebuild, and reports rather than throws: a mistiled band is a drawing bug. */
function assertBandTiling(pathEl, L, cells) {
  const seg = L / UG_COUNT;
  const slots = new Set();
  const bad = [];
  for (const c of cells) {
    slots.add(c.ringSlot);
    let p;
    try { p = pathEl.getPointAtLength((c.ringSlot + 0.5) * seg); } catch { return true; }
    const off = Math.hypot(p.x - c.cx, p.y - c.cy);
    if (off > 0.75) bad.push(`share ${c.ring} is centred ${off.toFixed(2)}px off its ring point`);
  }
  if (slots.size !== UG_COUNT) bad.push(`${slots.size} distinct shares, expected ${UG_COUNT} — they overlap or leave a gap`);
  if (Math.abs(UG_COUNT * seg - L) > 1e-6) bad.push(`shares span ${(UG_COUNT * seg).toFixed(4)} of ${L.toFixed(4)}`);
  if (bad.length) console.warn('underglow perimeter band: ' + bad.join('; '));
  return !bad.length;
}

/** A rounded rectangle as a closed polyline, counter-clockwise in SVG coordinates. Used by the 3D
 *  view for every extruded outline — board, keycaps, the underglow band centreline — so one
 *  definition of "rounded rectangle" serves the whole scene. `seg` is segments per corner arc. */
function roundedOutline(x, y, w, h, r, seg = 5) {
  const rr = clamp(r, 0, Math.min(w, h) / 2);
  const pts = [];
  // corner centres, clockwise from the top-left, with the arc's start angle
  const corners = [
    [x + w - rr, y + rr, -Math.PI / 2],
    [x + w - rr, y + h - rr, 0],
    [x + rr, y + h - rr, Math.PI / 2],
    [x + rr, y + rr, Math.PI],
  ];
  for (const [cx, cy, a0] of corners) {
    for (let i = 0; i <= seg; i++) {
      const a = a0 + (i / seg) * (Math.PI / 2);
      pts.push([cx + rr * Math.cos(a), cy + rr * Math.sin(a)]);
    }
  }
  return pts;
}

function buildGeometry() {
  const rows = keyRows();
  const gcols = gridColsFor(rows);
  const nCols = Math.max(GRID_COLS, ...gcols.map((cs) => Math.max(0, ...cs) + 1));
  const nRows = Math.max(KEY_GRID_COLS.length, rows.length);
  const band = GEO.keyBand, gap = GEO.key.gap;
  const kw = (band.w - (nCols - 1) * gap) / nCols;
  const kh = (band.h - (nRows - 1) * gap) / nRows;
  const colX = (c) => band.x + c * (kw + gap);
  const rowY = (r) => band.y + r * (kh + gap);

  // One cell per switch, at its fixed grid position. `col` stays the ORDINAL within the row —
  // that is what the config records and what the identify sweep writes — and `gcol` is the grid
  // column it translates to, used for drawing only.
  const cells = [];
  rows.forEach((n, r) => {
    for (let o = 0; o < n; o++) {
      const gcol = gcols[r][o];
      const x = colX(gcol), y = rowY(r);
      cells.push({ row: r, col: o, gcol, x, y, w: kw, h: kh, cx: x + kw / 2, cy: y + kh / 2 });
    }
  });
  const cellAt = (r, o) => cells.find((c) => c.row === r && c.col === o) || null;

  // Physical keycaps: 13 switches, 12 caps. A SHARED_KEYCAPS group (logical indices, translated
  // to [row, ordinal] here) becomes ONE cap whose halves are still separate LEDs.
  const caps = [];
  const capOf = new Map();
  for (const group of SHARED_KEYCAPS) {
    const cs = group.map((li) => { const p = logicalPos(rows, li); return p && cellAt(p[0], p[1]); });
    if (cs.some((c) => !c)) continue;                                  // not this row layout
    cs.sort((a, b) => a.gcol - b.gcol);
    if (cs.some((c) => c.row !== cs[0].row)) continue;                 // not one physical cap
    if (cs.some((c, i) => i && c.gcol !== cs[i - 1].gcol + 1)) continue;
    const last = cs[cs.length - 1];
    const cap = { shared: true, cells: cs, x: cs[0].x, y: cs[0].y, w: last.x + last.w - cs[0].x, h: cs[0].h };
    // Split the cap into one region per LED, meeting halfway between the cells they cover, so
    // the halves tile the whole cap with no seam gap.
    cs.forEach((c, i) => {
      c.hx0 = i === 0 ? cap.x : (cs[i - 1].x + cs[i - 1].w + c.x) / 2;
      c.hx1 = i === cs.length - 1 ? cap.x + cap.w : (c.x + c.w + cs[i + 1].x) / 2;
      c.capIndex = i;
      c.capCount = cs.length;
    });
    caps.push(cap);
    for (const c of cs) capOf.set(`${c.row},${c.col}`, cap);
  }
  for (const c of cells) {
    const id = `${c.row},${c.col}`;
    if (capOf.has(id)) continue;
    const cap = { shared: false, cells: [c], x: c.x, y: c.y, w: c.w, h: c.h };
    caps.push(cap);
    capOf.set(id, cap);
  }

  /* Underglow: eight equal shares of the whole perimeter, no centre LED. A share is a slice of
   * the shared band path (see ugBandGeom), so a cell carries its ring point — which is where its
   * band is centred and where its label goes — and `ringSlot`, which eighth of the path it owns. */
  const ugBand = ugBandGeom();
  const ugCells = RING_ORDER.map(([gx, gy], ring) => ({
    gx, gy, ring,
    ringSlot: (ring + UG_COUNT - 1) % UG_COUNT,
    ...ugRingPoint(gx, gy, ugBand),
  }));
  // The absent 9th slot of the 3x3 grid, at the band's own thickness so it reads as one more
  // share's worth of light that simply isn't there.
  const ncS = GEO.ug.thick + 6;
  const noCentre = {
    x: GEO.board.x + GEO.board.w / 2 - ncS / 2,
    y: GEO.board.y + GEO.board.h / 2 - ncS / 2,
    w: ncS, h: ncS, r: 6,
  };

  // The three non-key controls, at their confirmed grid slots. Drawn, never addressable.
  const featureCells = FEATURES
    .filter((f) => f.row < nRows && f.gcol < nCols && !cells.some((c) => c.row === f.row && c.gcol === f.gcol))
    .map((f) => {
      const x = colX(f.gcol), y = rowY(f.row);
      return { ...f, x, y, w: kw, h: kh, cx: x + kw / 2, cy: y + kh / 2 };
    });

  /* STRIP index -> position. The confirmed wiring order ships as the default; config overrides
   * it per index. This mapping is wiring detail: it is what the identify sweep records and what
   * gets displayed for reference, and NOTHING else in this page depends on it (see below). */
  const defKeys = DEFAULT_KEY_POSITIONS.map((p) => p.slice());
  const defUg = DEFAULT_UNDERGLOW_POSITIONS.map((p) => p.slice());
  const kp = readPositions(state.config?.layout?.key_positions, KEY_COUNT, defKeys);
  const up = readPositions(state.config?.layout?.underglow_positions, UG_COUNT, defUg, 2);
  const keyPosToStrip = new Map();
  kp.positions.forEach((p, i) => { if (p && !keyPosToStrip.has(p.join(','))) keyPosToStrip.set(p.join(','), i); });
  const ugPosToStrip = new Map();
  up.positions.forEach((p, i) => { if (p && !ugPosToStrip.has(p.join(','))) ugPosToStrip.set(p.join(','), i); });

  /* Normalised LED coordinates for spatial effects. Deliberately the SAME normalisation the
   * daemon uses (layout.key_xy / underglow_xy): grid position over grid extent, not pixels on
   * this drawing, so what the preview shows is what the device renders. */
  const spatial = (x01, y01) => {
    const dx = x01 - 0.5, dy = y01 - 0.5;
    let ang = Math.atan2(dx, -dy) / (2 * Math.PI); // 0 at top, increasing clockwise
    if (ang < 0) ang += 1;
    return { u: x01, v: y01, rad: Math.hypot(dx, dy) / Math.hypot(0.5, 0.5), angN: ang };
  };

  /* An LED's identity in this page is its LOGICAL index (keys) or its RING POSITION (underglow),
   * never its strip index — because that is the space the config and the daemon both work in:
   * `profiles[*].keys[].index` is logical, and the frame arrays posted to /api/preview/frame are
   * logical for keys and ring-ordered for underglow (the daemon's transport translates to strip
   * order itself, via the very mapping above). Both are pure functions of physical position, so
   * correcting the wiring mapping never renumbers anything a user has coloured.
   *
   * `cells` was built row-major over populated slots, which IS the logical numbering. */
  const keys = [];
  for (let i = 0; i < Math.max(KEY_COUNT, cells.length); i++) {
    const cell = cells[i] || null;
    const sp = cell
      ? spatial(nCols > 1 ? cell.gcol / (nCols - 1) : 0.5, nRows > 1 ? cell.row / (nRows - 1) : 0.5)
      : { u: 0, v: 0, rad: 0, angN: 0 };
    const pos = cell ? [cell.row, cell.col] : null;
    keys.push({
      index: i, pos, cell, ...sp, ringN: sp.angN,
      strip: pos ? (keyPosToStrip.has(pos.join(',')) ? keyPosToStrip.get(pos.join(',')) : null) : null,
    });
  }
  const ug = ugCells.map((cell, ring) => {
    const pos = [cell.gx, cell.gy];
    return {
      index: ring, pos, cell, ...spatial(cell.gx / 2, cell.gy / 2), ringN: ring / UG_COUNT,
      strip: ugPosToStrip.has(pos.join(',')) ? ugPosToStrip.get(pos.join(',')) : null,
    };
  });

  const keyPosToIndex = new Map();
  keys.forEach((k) => { if (k.pos) keyPosToIndex.set(k.pos.join(','), k.index); });
  const ugPosToIndex = new Map();
  ug.forEach((u) => { ugPosToIndex.set(u.pos.join(','), u.index); });

  /* The three PWM status LEDs: a small vertical column in the LEFT of the touch pad's own grid
   * slot, which puts them immediately left of the pad circle and inside the key block's footprint
   * — not below it, and well clear of the perimeter band. The touch glyph is told how much of its
   * slot they take so it centres in what's left instead of drawing over them. */
  const st = GEO.status;
  const stColW = st.pad + st.w + st.clear;
  const touchCell = featureCells.find((f) => f.kind === 'touch');
  if (touchCell) touchCell.padLeft = stColW;
  // A key_rows layout with no touch slot at all still needs somewhere inside the key block: the
  // bottom-left cell of the grid, never the board edge where the band now lives.
  const stHost = touchCell || { x: band.x, y: rowY(Math.max(0, nRows - 1)), w: kw, h: kh };
  const stH = STATUS_COUNT * st.h + (STATUS_COUNT - 1) * st.gap;
  const stX = stHost.x + st.pad;
  const stY = stHost.y + (stHost.h - stH) / 2;
  const statusCells = [];
  for (let i = 0; i < STATUS_COUNT; i++) {
    const y = stY + i * (st.h + st.gap);
    statusCells.push({ i, x: stX, y, w: st.w, h: st.h, cx: stX + st.w / 2, cy: y + st.h / 2 });
  }

  state.geom = {
    rows, gcols, nRows, nCols, cells, caps, capOf, ugBand, ugCells, noCentre, featureCells, statusCells,
    keys, ug, keyPosToIndex, ugPosToIndex, keyPosToStrip, ugPosToStrip,
    defaultedKeys: kp.fromDefaults, defaultedUg: up.fromDefaults,
    defKeys, defUg,
  };
  return state.geom;
}

/** One LED region of a shared cap, as a path.
 *
 *  The outer corners are rounded and the seam edge is left OPEN: an open subpath still fills
 *  (the fill closes it) but a stroke traces only the segments actually drawn. So the two regions
 *  paint independent colours while the strokes together outline exactly one keycap — no line down
 *  the middle pretending they are separate caps. The seam gets its own hairline instead. */
function capHalfPath(cap, cell) {
  const r = GEO.key.r, x0 = cell.hx0, x1 = cell.hx1, y0 = cap.y, y1 = cap.y + cap.h;
  const first = cell.capIndex === 0, last = cell.capIndex === cell.capCount - 1;
  const arc = (sweep, x, y) => `A ${r},${r} 0 0 ${sweep} ${x},${y}`;
  if (first && last) {  // a group of one: an ordinary rounded cap
    return `M ${x0 + r},${y0} H ${x1 - r} ${arc(1, x1, y0 + r)} V ${y1 - r} ${arc(1, x1 - r, y1)} `
      + `H ${x0 + r} ${arc(1, x0, y1 - r)} V ${y0 + r} ${arc(1, x0 + r, y0)} Z`;
  }
  if (first) {          // leftmost region: rounded left, open right
    return `M ${x1},${y0} H ${x0 + r} ${arc(0, x0, y0 + r)} V ${y1 - r} ${arc(0, x0 + r, y1)} H ${x1}`;
  }
  if (last) {           // rightmost region: open left, rounded right
    return `M ${x0},${y0} H ${x1 - r} ${arc(1, x1, y0 + r)} V ${y1 - r} ${arc(1, x1 - r, y1)} H ${x0}`;
  }
  // A cap covering three or more switches doesn't exist on this pad; draw a plain middle band.
  return `M ${x0},${y0} H ${x1} V ${y1} H ${x0} Z`;
}

/* ====================================================== 8. frame renderer */

function coordOf(led, dir) {
  switch (dir) {
    case 'vertical': return led.v;
    case 'radial': return led.rad;
    case 'ring': return led.ringN;
    default: return led.u;
  }
}

const ringDist = (a, b) => { const d = Math.abs(wrap01(a) - wrap01(b)); return Math.min(d, 1 - d); };
const hash01 = (n) => { let x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
const scale = (rgb, k) => rgb.map((c) => clamp(c * k, 0, 1));

/** One LED's effect colour at time t. Pure function of the effect spec + LED coordinates. */
function effectColor(eff, cp, led, t, zoneSeed) {
  const name = eff.name;
  if (name === 'off') return [0, 0, 0];
  const speed = eff.speed === undefined ? EFFECT_DEFAULTS.speed : Number(eff.speed);
  const intensity = eff.intensity === undefined ? EFFECT_DEFAULTS.intensity : Number(eff.intensity);
  const dir = eff.direction || EFFECT_DEFAULTS.direction;
  let coord = coordOf(led, dir);
  if (eff.reverse) coord = 1 - coord;
  const phase = wrap01(t * speed);
  const pal = (u) => samplePalette(cp, u);

  switch (name) {
    case 'solid':
      return pal(phase);
    case 'gradient':
      return pal(coord * (0.5 + intensity * 1.5) + phase);
    case 'rainbow': {
      const hue = wrap01(coord * (0.4 + intensity * 1.2) + phase) * 360;
      return oklchToRgb([0.74, 0.15, hue]);
    }
    case 'breathe': {
      const amp = 0.1 + 0.9 * (0.5 - 0.5 * Math.cos(2 * Math.PI * phase));
      return scale(pal(coord * intensity + phase * 0.15), amp);
    }
    case 'chase': {
      const w = 0.06 + 0.44 * intensity;
      const d = ringDist(coord, phase);
      const v = Math.max(0, 1 - d / w);
      return scale(pal(coord), v * v);
    }
    case 'wipe': {
      const filled = coord <= phase;
      return filled ? pal(coord) : [0, 0, 0];
    }
    case 'comet': {
      const tail = 0.08 + 0.5 * intensity;
      const behind = wrap01(phase - coord);
      const v = Math.exp(-behind / tail);
      return scale(pal(phase), Math.min(1, v * 1.15));
    }
    case 'ripple': {
      const waves = 1 + intensity * 3;
      const c = Math.cos(2 * Math.PI * (coord * waves - phase));
      const v = Math.pow(Math.max(0, c), 3);
      return scale(pal(coord), v);
    }
    case 'sparkle': {
      const seed = led.index + zoneSeed;
      const on = hash01(seed * 7.13) < 0.2 + 0.75 * intensity;
      if (!on) return [0, 0, 0];
      const off = hash01(seed * 3.77);
      const p = wrap01(phase * 1.7 - off);
      const v = Math.pow(Math.max(0, 1 - p * 6), 2);
      return scale(pal(hash01(seed * 11.9)), v);
    }
    default:
      return pal(coord + phase);
  }
}

/** Composite base layer + effect layer into per-strip-index colours.
 *
 *  `withEffect: false` is the "Off" state of the source picker: the configured BASE layer only —
 *  per-key colours, the underglow base colour, the status duties — with no effect and no clock. */
function computeFrame(t, withEffect = true) {
  const geom = state.geom || buildGeometry();
  // What the pad would actually show in the scope being edited: a mode's lighting overrides the
  // profile's key by key, and its key colours layer over the profile's.
  const light = effectiveLighting();
  const eff = withEffect && light.effect && light.effect.name ? light.effect : null;
  const cp = eff ? (compiledPalette(eff.palette) || compiledPalette('rainbow')) : null;

  const baseKeys = geom.keys.map((k) => {
    const c = keyColorOf(k.index);
    return c ? hexToRgb(c) : [0, 0, 0];
  });
  const ugBase = isHex6(light.underglow) ? hexToRgb(light.underglow) : [0, 0, 0];
  let keys = baseKeys, ug = geom.ug.map(() => ugBase);

  if (eff) {
    const target = eff.target || EFFECT_DEFAULTS.target;
    const mode = eff.blend || EFFECT_DEFAULTS.blend;
    if (target !== 'underglow') keys = geom.keys.map((k, i) => blendRgb(mode, baseKeys[i], effectColor(eff, cp, k, t, 0)));
    if (target !== 'keys') ug = geom.ug.map((u, i) => blendRgb(mode, ugBase, effectColor(eff, cp, u, t, 100)));
  }

  const duty = Array.isArray(light.status_leds) ? light.status_leds : [];
  const status = [0, 1, 2].map((i) => clamp(Math.round(Number(duty[i]) || 0), 0, 255));
  // `source` and `dim` say what this frame IS, so paint() need not know which path produced it.
  // A simulated frame carries no brightness: it is a drawing of a config, not a reading of a pad.
  return { keys, ug, status, dim: 1, source: eff ? 'preview' : 'off' };
}

const frameToWire = (f) => ({
  keys: f.keys.map(rgbToHex),
  underglow: f.ug.map(rgbToHex),
  status: f.status,
});

/* ========================================================== 9. device SVG */

const svgRefs = { keys: new Map(), ug: new Map(), status: [], glow: new Map(), feat: new Map() };

/** Ghost glyph for a non-key control. Never focusable, never addressable, never selectable. */
function featureGlyph(f) {
  // None of the three carries an LED, but all three carry bindings, so they take a pointer click
  // through to the Bindings panel. Still aria-hidden and still not focusable: the panel's own
  // control map is the keyboard path, and the tab order through the board stays LEDs-only.
  const g = svgEl('g', {
    class: `feat feat-${f.kind}`, 'aria-hidden': 'true', 'data-bindable': '1',
  });
  // `padLeft` is slot width claimed by something else drawn in the same slot — the status LED
  // column, which sits immediately left of the touch pad. The glyph centres in what is left of the
  // slot rather than in the whole of it, so the two never overlap.
  const pad = f.padLeft || 0;
  const bx = f.x + pad, bw = Math.max(18, f.w - pad), bcx = bx + bw / 2;
  const cy = f.cy - 6, r = Math.min(bw, f.h) / 2 - 5;
  if (f.kind === 'encoder') {
    g.append(svgEl('circle', { class: 'feat-shape', cx: bcx, cy, r }));
    g.append(svgEl('circle', { class: 'feat-shape', cx: bcx, cy, r: r * 0.42 }));
    g.append(svgEl('line', { class: 'feat-shape', x1: bcx, y1: cy - r, x2: bcx, y2: cy - r * 0.55 }));
  } else if (f.kind === 'joystick') {
    g.append(svgEl('circle', { class: 'feat-shape', cx: bcx, cy, r }));
    g.append(svgEl('circle', { class: 'feat-knob', cx: bcx, cy, r: r * 0.34 }));
    for (const [dx, dy] of [[0, -1], [1, 0], [0, 1], [-1, 0]]) {
      g.append(svgEl('line', {
        class: 'feat-shape',
        x1: bcx + dx * r * 0.5, y1: cy + dy * r * 0.5,
        x2: bcx + dx * r * 0.82, y2: cy + dy * r * 0.82,
      }));
    }
  } else {
    const w = Math.max(16, bw - 8), h = Math.min(f.h - 16, 26);
    g.append(svgEl('rect', { class: 'feat-shape feat-dash', x: bcx - w / 2, y: cy - h / 2, width: w, height: h, rx: 7 }));
    for (const k of [0.32, 0.6]) {
      g.append(svgEl('circle', { class: 'feat-shape', cx: bcx, cy, r: (h / 2) * k }));
    }
  }
  const lab = svgEl('text', { class: 'feat-lab', x: bcx, y: f.y + f.h - 2 });
  lab.textContent = f.label;
  g.append(lab);
  return g;
}

function buildDevice() {
  const geom = buildGeometry();
  const sig = JSON.stringify([geom.rows, geom.gcols]);
  const svg = $('device');
  if (sig === state.geomSig && svg.childNodes.length) return;
  state.geomSig = sig;

  svg.textContent = '';
  svgRefs.keys.clear(); svgRefs.ug.clear(); svgRefs.glow.clear(); svgRefs.feat.clear();
  svgRefs.status = [];

  svg.append(svgEl('rect', { class: 'board', x: GEO.board.x, y: GEO.board.y, width: GEO.board.w, height: GEO.board.h, rx: GEO.board.r }));

  // blurred duplicates of every lit surface, painted underneath for a soft glow
  const glow = svgEl('g', { class: 'glowlayer' });
  svg.append(glow);

  const addGlow = (kind, id, attrs, tag = 'rect') => {
    const r = svgEl(tag, attrs);
    glow.append(r);
    svgRefs.glow.set(kind + ':' + id, r);
    return r;
  };

  /* Underglow first so it reads as sitting behind/around the keys. The eight LEDs tile the WHOLE
   * perimeter with no gaps: each one is its own copy of the shared band path showing a single
   * L/8 dash centred on its own ring point, so corner LEDs wrap symmetrically round the corner
   * (L/16 down each adjoining side) and edge-midpoint LEDs lie flat on one side. `ug-edge` is a
   * slightly wider copy underneath, which is what hover / selection / focus / unmapped paint —
   * the band's own stroke is the LED colour and can't also carry a highlight. */
  const bg = geom.ugBand;
  const bands = [];
  for (const c of geom.ugCells) {
    const id = `${c.gx},${c.gy}`;
    const gl = addGlow('ug', id, { class: 'ug-glow', d: bg.d, 'stroke-width': GEO.ug.thick }, 'path');
    const g = svgEl('g', { class: 'cell-g', tabindex: 0, role: 'button', 'data-zone': 'underglow', 'data-pos': id });
    const edge = svgEl('path', { class: 'ug-edge', d: bg.d, 'stroke-width': GEO.ug.thick + 5 });
    const rect = svgEl('path', { class: 'ug-band', d: bg.d, 'stroke-width': GEO.ug.thick });
    const idx = svgEl('text', { class: 'cell-idx', x: c.cx, y: c.cy + 4 });
    g.append(edge, rect, idx);
    svg.append(g);
    svgRefs.ug.set(id, { g, rect, idx, glow: gl, cell: c });
    bands.push({ c, parts: [rect, edge, gl] });
  }
  /* Cut the shares from the path's OWN measured length, so what the dash engine tiles is exactly
   * what was measured — the eight offsets are i*L/8 and the dash is L/8, which leaves no seam and
   * no overlap by construction rather than by trigonometry. */
  const ugL = pathLength(bands[0].parts[0], bg.L);
  const ugSeg = ugL / UG_COUNT;
  const ugDash = `${ugSeg.toFixed(4)} ${(ugL - ugSeg).toFixed(4)}`;
  for (const { c, parts } of bands) {
    const off = ((ugL - c.ringSlot * ugSeg) % ugL).toFixed(4);
    for (const p of parts) {
      p.setAttribute('stroke-dasharray', ugDash);
      p.setAttribute('stroke-dashoffset', off);
    }
  }
  assertBandTiling(bands[0].parts[0], ugL, geom.ugCells);

  // The absent 9th slot of the 3x3 grid, at the band's own thickness. Drawn BEHIND the keys,
  // because that is where it would be and where the underglow physically sits: the dashes show
  // through the gap between the four middle caps. The caption below spells it out.
  svg.append(svgEl('rect', {
    class: 'nocentre', x: geom.noCentre.x, y: geom.noCentre.y,
    width: geom.noCentre.w, height: geom.noCentre.h, rx: geom.noCentre.r,
  }));

  // The three non-key controls, so the board is recognisable as the real pad. They carry no LED,
  // but all three DO carry bindings, so they are kept addressable here for the event highlight and
  // for a pointer shortcut into the Bindings panel.
  for (const f of geom.featureCells) {
    const g = featureGlyph(f);
    svg.append(g);
    svgRefs.feat.set(f.kind, { g, cell: f });
  }

  /* Keycaps. One group per LED, but a shared cap is drawn as a single wide rounded cap: the
   * regions carry their own colour and their own selection, and a hairline seam plus the shared
   * index label say they are one control.
   *
   * Drawn in grid-row order rather than geom.caps order. geom.caps lists the shared cap first, so
   * the old DOM order made the tab sequence 10, 11, 0, 1 … 12; row order makes it 0…12. That
   * matters more than it used to: in the 3D view this SVG is the keyboard layer and nobody can see
   * it, so its traversal order has to match the layout it stands for. */
  const drawCaps = geom.caps.slice().sort((a, b) => a.cells[0].row - b.cells[0].row);
  for (const cap of drawCaps) {
    if (cap.shared) {
      svg.append(svgEl('rect', {
        class: 'cap-shell', x: cap.x, y: cap.y, width: cap.w, height: cap.h, rx: GEO.key.r,
      }));
    }
    for (const c of cap.cells) {
      const id = `${c.row},${c.col}`;
      const gx = cap.shared ? c.hx0 : c.x, gw = cap.shared ? c.hx1 - c.hx0 : c.w;
      addGlow('key', id, { x: gx, y: c.y, width: gw, height: c.h, rx: GEO.key.r });
      const g = svgEl('g', { class: 'cell-g', tabindex: 0, role: 'button', 'data-zone': 'keys', 'data-pos': id });
      const tx = gx + gw / 2;
      const shape = cap.shared
        ? svgEl('path', { class: 'key-cell key-half', d: capHalfPath(cap, c) })
        : svgEl('rect', { class: 'key-cell', x: c.x, y: c.y, width: c.w, height: c.h, rx: GEO.key.r });
      const idx = svgEl('text', { class: 'cell-idx', x: tx, y: c.cy + 4 });
      const lab = svgEl('text', { class: 'cell-lab', x: tx, y: c.cy + 12 });
      g.append(shape, idx, lab);
      svg.append(g);
      svgRefs.keys.set(id, { g, rect: shape, idx, lab, cell: c, cap });
    }
    if (cap.shared) {
      for (const c of cap.cells.slice(1)) {
        svg.append(svgEl('line', { class: 'cap-seam', x1: c.hx0, y1: cap.y + 6, x2: c.hx0, y2: cap.y + cap.h - 6 }));
      }
      const t = svgEl('text', { class: 'cap-t', x: cap.x + cap.w / 2, y: cap.y + cap.h + 11 });
      t.textContent = 'one keycap, two LEDs';
      svg.append(t);
    }
  }

  /* Status LEDs: three small marks in a vertical column immediately LEFT of the touch pad and
   * inside the key block's footprint, drawn last so they sit above the touch glyph's slot. Much
   * smaller than a keycap, which is what they are — the duty number uses its own smaller type. */
  for (const c of geom.statusCells) {
    addGlow('st', String(c.i), { x: c.x, y: c.y, width: c.w, height: c.h, rx: GEO.status.r });
    const g = svgEl('g', { class: 'cell-g', tabindex: 0, role: 'button', 'data-zone': 'status', 'data-pos': String(c.i) });
    const rect = svgEl('rect', { class: 'st-cell', x: c.x, y: c.y, width: c.w, height: c.h, rx: GEO.status.r });
    const idx = svgEl('text', { class: 'cell-idx st-idx', x: c.cx, y: c.cy + 2.6 });
    g.append(rect, idx);
    svg.append(g);
    svgRefs.status.push({ g, rect, idx, cell: c });
  }

  const notes = [
    { t: 'status LEDs — PWM duty 0-255, single colour', x: GEO.board.x, y: GEO.noteY[0], cls: 'zone-t start' },
    { t: 'no centre underglow LED', x: GEO.board.x + GEO.board.w, y: GEO.noteY[0], cls: 'zone-t end' },
  ];
  for (const n of notes) {
    const t = svgEl('text', { class: n.cls, x: n.x, y: n.y });
    t.textContent = n.t;
    svg.append(t);
  }
}

/* ================================================ 9b. the 3D view (WebGL)
 *
 * A real perspective render of the pad, hand-written against the raw WebGL context: a few dozen
 * lines of matrix maths, two shaders as template strings, and meshes extruded from the SAME
 * buildGeometry() output the flat SVG is drawn from. No three.js, no CDN, no build step — those
 * were never available here and the geometry is not hard enough to need them.
 *
 * Three things constrain the design more than looks do.
 *
 * 1. THE LED COLOUR IS THE DATA, so nothing is allowed to shift it. There is no white light in
 *    this scene at all. A cap's top face is *emissive*: the fragment colour is the frame colour,
 *    byte for byte, exactly as the flat view's `fill` is — you can read a pixel out of the canvas
 *    and get the hex back. A cap's side walls are that same colour times a scalar, which is what a
 *    dimmer does and cannot move a hue. The board and the desk are a dark base plus the ADDITIVE
 *    sum of the LEDs' own colours, so the light spilling onto the surface underneath is the LED's
 *    colour rather than a tint of it. Nothing anywhere adds white or tone-maps.
 *
 * 2. IT MUST STAY OPERABLE. The SVG is not replaced: in 3D it stays in the DOM as the keyboard and
 *    accessibility layer (invisible, `pointer-events: none`) and keeps every tabindex, role, arrow
 *    key and accessible name. Focus is drawn into the 3D scene so it is visible. The pointer is
 *    handled by exact colour-buffer picking, so clicking a cap selects that cap.
 *
 * 3. IT MUST NOT BECOME A SECOND GEOMETRY. Every position, size and identity comes from
 *    `state.geom`; the only numbers this section adds are heights (GEO.z) and the camera.
 */

/* ---------------------------------------------------------------- 4x4 matrices */

const M4 = {
  perspective(fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
    return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
  },
  lookAt(eye, at, up) {
    const z = V3.norm(V3.sub(eye, at));
    const x = V3.norm(V3.cross(up, z));
    const y = V3.cross(z, x);
    return [
      x[0], y[0], z[0], 0,
      x[1], y[1], z[1], 0,
      x[2], y[2], z[2], 0,
      -V3.dot(x, eye), -V3.dot(y, eye), -V3.dot(z, eye), 1,
    ];
  },
  mul(a, b) {                                   // a * b, both column-major
    const o = new Array(16).fill(0);
    for (let c = 0; c < 4; c++) {
      for (let r = 0; r < 4; r++) {
        let s = 0;
        for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
        o[c * 4 + r] = s;
      }
    }
    return o;
  },
};

const V3 = {
  sub: (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
  add: (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]],
  mul: (a, k) => [a[0] * k, a[1] * k, a[2] * k],
  cross: (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]],
  dot: (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2],
  norm: (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; },
};

/* ------------------------------------------------------------- mesh building */

const meshNew = () => ({ pos: [], nrm: [], uv: [] });

/* SVG's y axis points DOWN, so using it as a world axis with z up gives a left-handed space and
 * every render comes out mirrored — including the text on the keycaps. So there is exactly one
 * conversion, applied the moment a coordinate enters the scene: world y is minus SVG y. `FY` flips
 * a whole outline, `fy` a single value. Nothing downstream needs to think about it again. */
const fy = (y) => -y;
const FY = (pts) => pts.map((p) => [p[0], -p[1]]);

function meshTri(m, a, b, c, n, uv) {
  m.pos.push(a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2]);
  for (let i = 0; i < 3; i++) m.nrm.push(n[0], n[1], n[2]);
  if (uv) m.uv.push(uv[0][0], uv[0][1], uv[1][0], uv[1][1], uv[2][0], uv[2][1]);
  else m.uv.push(0, 0, 0, 0, 0, 0);
}

function meshQuad(m, a, b, c, d, n, uv) {
  meshTri(m, a, b, c, n, uv && [uv[0], uv[1], uv[2]]);
  meshTri(m, a, c, d, n, uv && [uv[0], uv[2], uv[3]]);
}

const faceNormal = (a, b, c) => V3.norm(V3.cross(V3.sub(b, a), V3.sub(c, a)));

/** Fill a convex polygon at height z, as a fan from its centroid. Every polygon in this scene is
 *  a rounded rectangle or a straight-line clip of one, so a centroid fan is exact. */
function meshFillPoly(m, poly, z, nz) {
  if (poly.length < 3) return;
  let cx = 0, cy = 0;
  for (const p of poly) { cx += p[0]; cy += p[1]; }
  cx /= poly.length; cy /= poly.length;
  const n = [0, 0, nz];
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    meshTri(m, [cx, cy, z], [a[0], a[1], z], [b[0], b[1], z], n);
  }
}

/** Walls between two aligned outlines at two heights. `skipAt` drops edges whose midpoint x is at
 *  that value — the vertical cut a shared keycap's seam introduces is interior, not a wall. */
function meshWalls(m, lo, zLo, hi, zHi, skipAt) {
  const n = lo.length;
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    if (skipAt !== undefined && Math.abs((lo[i][0] + lo[j][0]) / 2 - skipAt) < 0.01) continue;
    const a = [lo[i][0], lo[i][1], zLo], b = [lo[j][0], lo[j][1], zLo];
    const c = [hi[j][0], hi[j][1], zHi], d = [hi[i][0], hi[i][1], zHi];
    meshQuad(m, a, b, c, d, faceNormal(a, b, c));
  }
}

/** A ring lying just inside a closed polygon: one quad per edge, stepped inward along that edge's
 *  own normal. Works for any polygon, including the clipped halves of the shared keycap — which a
 *  ring built between two independently generated outlines does not, because clipping the two does
 *  not always leave them with the same number of points. */
function meshRing(m, poly, z, width) {
  if (poly.length < 3) return;
  let cx = 0, cy = 0;
  for (const q of poly) { cx += q[0]; cy += q[1]; }
  cx /= poly.length; cy /= poly.length;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    let nx = -(b[1] - a[1]), ny = b[0] - a[0];
    const l = Math.hypot(nx, ny) || 1;
    nx /= l; ny /= l;
    // point the normal inward
    const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
    if ((cx - mx) * nx + (cy - my) * ny < 0) { nx = -nx; ny = -ny; }
    meshQuad(m, [a[0], a[1], z], [b[0], b[1], z],
      [b[0] + nx * width, b[1] + ny * width, z], [a[0] + nx * width, a[1] + ny * width, z], [0, 0, 1]);
  }
}

/** Sutherland–Hodgman clip of a closed polygon against a vertical line. */
function clipPolyX(poly, xCut, keepLess) {
  const inside = (p) => (keepLess ? p[0] <= xCut : p[0] >= xCut);
  const out = [];
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    const ia = inside(a), ib = inside(b);
    if (ia) out.push(a);
    if (ia !== ib) {
      const t = (xCut - a[0]) / (b[0] - a[0]);
      out.push([xCut, a[1] + (b[1] - a[1]) * t]);
    }
  }
  return out;
}

function meshCylinder(m, cx, cy, r, z0, z1, seg = 20) {
  const ring = [];
  for (let i = 0; i < seg; i++) {
    const a = (i / seg) * Math.PI * 2;
    ring.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
  }
  meshWalls(m, ring, z0, ring, z1);
  meshFillPoly(m, ring, z1, 1);
  return m;
}

/* --------------------------------------------------- the underglow perimeter
 *
 * The eight shares are sliced out of the perimeter by ARC LENGTH, with the same start offset and
 * the same slot numbering the SVG's dash offsets use (see ugBandGeom): `d0` puts a share BOUNDARY
 * a sixteenth of the perimeter before ring position 0, and ring i owns slot (i+7) mod 8. That
 * relation holds for any rounded square, so applying it to the board's own outline — where the
 * light physically leaves the board — puts every share over the same corner or edge midpoint the
 * flat view puts it on. Identity comes from geom, only the radius differs. */
function bandSlices(x, y, w, h, r, seg = 10) {
  const straight = w - 2 * r;
  const d0 = clamp(straight / 4 - (Math.PI * r) / 8, 0, straight);
  // Dense polyline starting at the same point as the SVG path: (x + r + d0, y), running clockwise.
  const pts = [[x + r + d0, y]];
  const push = (px, py) => pts.push([px, py]);
  const arc = (ccx, ccy, a0) => {
    for (let i = 1; i <= seg; i++) {
      const a = a0 + (i / seg) * (Math.PI / 2);
      push(ccx + r * Math.cos(a), ccy + r * Math.sin(a));
    }
  };
  push(x + w - r, y); arc(x + w - r, y + r, -Math.PI / 2);
  push(x + w, y + h - r); arc(x + w - r, y + h - r, 0);
  push(x + r, y + h); arc(x + r, y + h - r, Math.PI / 2);
  push(x, y + r); arc(x + r, y + r, Math.PI);
  push(x + r + d0, y);
  const cum = [0];
  for (let i = 1; i < pts.length; i++) {
    cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  }
  const L = cum[cum.length - 1];
  const at = (s) => {
    s = clamp(s, 0, L);
    let i = 1;
    while (i < cum.length - 1 && cum[i] < s) i++;
    const t = (s - cum[i - 1]) / Math.max(1e-6, cum[i] - cum[i - 1]);
    return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * t, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * t];
  };
  return {
    L,
    /** The polyline for one eighth of the perimeter, resampled so corners stay smooth. */
    slice(slot, n = 14) {
      const s0 = (slot * L) / UG_COUNT, s1 = ((slot + 1) * L) / UG_COUNT;
      const out = [];
      for (let i = 0; i <= n; i++) out.push(at(s0 + ((s1 - s0) * i) / n));
      return out;
    },
    centre(slot) { return at(((slot + 0.5) * L) / UG_COUNT); },
  };
}

/* ------------------------------------------------------------------- shaders */

const V3D_VERT = `
attribute vec3 aPos;
attribute vec3 aNrm;
attribute vec2 aUv;
uniform mat4 uMvp;
varying vec3 vWorld;
varying vec3 vNrm;
varying vec2 vUv;
void main() {
  vWorld = aPos;
  vNrm = aNrm;
  vUv = aUv;
  gl_Position = uMvp * vec4(aPos, 1.0);
}`;

/* uMode
 *   0  emissive        gl_FragColor = uColor.  The frame colour, untouched. Cap tops, underglow
 *                      strips, status LEDs, focus/selection rings.
 *   1  self-lit solid  uColor * scalar(face).  A keycap's side walls: the same light running down
 *                      the side of the cap. A scalar cannot move a hue.
 *   2  surface         uBase * shade + additive sum of the LEDs' OWN colours. The board and the
 *                      desk, so the spill under a cap is that cap's colour.
 *   3  decal           a text glyph as an alpha mask, tinted by uColor. Never carries LED data.
 */
const V3D_FRAG = `
precision highp float;
varying vec3 vWorld;
varying vec3 vNrm;
varying vec2 vUv;
uniform int uMode;
uniform vec3 uColor;
uniform vec3 uBase;
uniform float uSpill;
uniform vec4 uLpos[21];
uniform vec3 uLcol[21];
uniform sampler2D uTex;

void main() {
  if (uMode == 0) {
    gl_FragColor = vec4(uColor, 1.0);
    return;
  }
  if (uMode == 3) {
    float a = texture2D(uTex, vUv).a;
    gl_FragColor = vec4(uColor, a);
    return;
  }
  vec3 n = normalize(vNrm);
  float up = abs(n.z);
  if (uMode == 1) {
    gl_FragColor = vec4(uColor * (0.34 + 0.30 * up), 1.0);
    return;
  }
  vec3 spill = vec3(0.0);
  for (int i = 0; i < 21; i++) {
    vec3 d = uLpos[i].xyz - vWorld;
    float dist = length(d);
    float k = dist / uLpos[i].w;
    float att = 1.0 / (1.0 + k * k * 2.2);
    // Wrapped rather than a hard cosine: an LED sits only millimetres above the plate, so a strict
    // dot() term drives the spill between the caps — the part you can actually see — to nothing.
    float nl = mix(0.12, 1.0, max(0.0, dot(n, d / max(dist, 0.001))));
    spill += uLcol[i] * att * nl;
  }
  gl_FragColor = vec4(uBase * (0.55 + 0.45 * up) + spill * uSpill, 1.0);
}`;

/* --------------------------------------------------------------- the renderer */

const R3 = {
  canvas: null, gl: null, prog: null, loc: null,
  objects: [],            // {buf, count, mode, pick, zone, index, kind, base, spill}
  lightPos: null,         // Float32Array(21*4)
  lightCol: null,         // Float32Array(21*3)
  sig: '',
  tex: null, texSig: '',
  failed: false,          // no WebGL, or the context was lost
  reason: '',
  hover: null,
  /* `zoom` is a multiplier on a distance computed to FIT the board to whichever canvas dimension
   * is tighter — see camMatrix(). A fixed distance would frame the pad differently at every
   * viewport width, and this page is laid out in two columns above 1100 px and one below. */
  cam: { yaw: -0.62, pitch: 0.60, zoom: 1 },
  drag: null,
  theme: null,
};

const CAM_PRESETS = {
  three: { yaw: -0.62, pitch: 0.60, zoom: 1 },
  front: { yaw: 0, pitch: 0.30, zoom: 1 },
  top: { yaw: 0, pitch: 1.5533, zoom: 1 },
};
const FOV_Y = 0.62;

/** Theme colours the scene needs, read from CSS so the 3D view follows the page's theme. */
function themeColors() {
  const cs = getComputedStyle(document.body);
  const get = (v, dflt) => {
    const raw = cs.getPropertyValue(v).trim();
    return isHex6(raw.replace('#', '')) ? hexToRgb(raw) : hexToRgb(dflt);
  };
  return {
    board: get('--bg-3', '222834'),
    ground: get('--bg', '0d1015'),
    ink: get('--fg', 'e7eaf0'),
    faint: get('--fg-faint', '6b7480'),
    accent: get('--accent', '7aa2f7'),
    ok: get('--ok', '6ee7a8'),
    warn: get('--warn', 'ffcc66'),
  };
}

function glCompile(gl, type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) || 'shader failed');
  return s;
}

function gl3dInit() {
  if (R3.gl || R3.failed) return !R3.failed;
  const canvas = $('device3d');
  let gl = null;
  try {
    // preserveDrawingBuffer so the canvas stays readable after the frame — that is what lets the
    // rendered pixels be checked against /api/frame, and what makes a right-click Save Image work.
    const opts = { alpha: false, antialias: true, depth: true, premultipliedAlpha: false, preserveDrawingBuffer: true };
    gl = canvas.getContext('webgl', opts) || canvas.getContext('experimental-webgl', opts);
  } catch { gl = null; }
  if (!gl) {
    R3.failed = true;
    R3.reason = 'this browser gives no WebGL context';
    return false;
  }
  try {
    const prog = gl.createProgram();
    gl.attachShader(prog, glCompile(gl, gl.VERTEX_SHADER, V3D_VERT));
    gl.attachShader(prog, glCompile(gl, gl.FRAGMENT_SHADER, V3D_FRAG));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog) || 'link failed');
    R3.prog = prog;
    R3.loc = {
      aPos: gl.getAttribLocation(prog, 'aPos'),
      aNrm: gl.getAttribLocation(prog, 'aNrm'),
      aUv: gl.getAttribLocation(prog, 'aUv'),
      uMvp: gl.getUniformLocation(prog, 'uMvp'),
      uMode: gl.getUniformLocation(prog, 'uMode'),
      uColor: gl.getUniformLocation(prog, 'uColor'),
      uBase: gl.getUniformLocation(prog, 'uBase'),
      uSpill: gl.getUniformLocation(prog, 'uSpill'),
      uLpos: gl.getUniformLocation(prog, 'uLpos[0]'),
      uLcol: gl.getUniformLocation(prog, 'uLcol[0]'),
      uTex: gl.getUniformLocation(prog, 'uTex'),
    };
  } catch (e) {
    R3.failed = true;
    R3.reason = 'the shaders would not compile here (' + String(e.message || e).slice(0, 60) + ')';
    return false;
  }
  R3.canvas = canvas;
  R3.gl = gl;
  R3.lightPos = new Float32Array(21 * 4);
  R3.lightCol = new Float32Array(21 * 3);
  gl.enable(gl.DEPTH_TEST);
  gl.disable(gl.CULL_FACE);      // the SVG's y-down space makes winding awkward; two-sided shading
  gl.clearColor(0, 0, 0, 1);     // instead, so culling buys nothing here

  // A lost context is not a crash: drop back to the flat view and say why.
  canvas.addEventListener('webglcontextlost', (ev) => {
    ev.preventDefault();
    R3.failed = true;
    R3.reason = 'the browser dropped the WebGL context';
    R3.gl = null;
    fall2d();
  });
  wire3dPointer(canvas);
  return true;
}

/** Give up on 3D and show the flat view, saying why.
 *
 *  Silence would be the wrong answer twice over: the box stays ticked while nothing happens, and
 *  the reason (no context, a refused shader, a GPU reset) is only in the console. The stored
 *  preference is deliberately NOT overwritten — a lost context should not un-choose 3D for good,
 *  so a reload tries again. */
function fall2d() {
  const first = !R3.told;
  R3.told = true;
  state.view.threeD = false;
  R3.failed = true;
  $('chk-3d').checked = false;
  $('chk-3d').disabled = true;
  $('chk-3d').closest('.chk').title = '3D view unavailable here — ' + (R3.reason || 'no WebGL');
  // Set the attribute directly rather than re-entering applyViewMode, which called this.
  const host = document.querySelector('.deviceview');
  if (host) host.dataset.view = '2d';
  renderViewSource();
  if (first) toast('3D view unavailable — ' + (R3.reason || 'no WebGL') + '. Showing the flat view, which does everything the 3D one does.', 'warn', 7000);
}

/* -------------------------------------------------------------- scene assembly */

function glBuffer(gl, mesh) {
  const n = mesh.pos.length / 3;
  const data = new Float32Array(n * 8);
  for (let i = 0; i < n; i++) {
    data[i * 8 + 0] = mesh.pos[i * 3];
    data[i * 8 + 1] = mesh.pos[i * 3 + 1];
    data[i * 8 + 2] = mesh.pos[i * 3 + 2];
    data[i * 8 + 3] = mesh.nrm[i * 3];
    data[i * 8 + 4] = mesh.nrm[i * 3 + 1];
    data[i * 8 + 5] = mesh.nrm[i * 3 + 2];
    data[i * 8 + 6] = mesh.uv[i * 2];
    data[i * 8 + 7] = mesh.uv[i * 2 + 1];
  }
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
  return { buf, count: n };
}

/** Build the whole scene from state.geom. Runs once per geometry change, never per frame. */
function build3dScene() {
  const gl = R3.gl;
  const geom = state.geom || buildGeometry();
  const sig = state.geomSig + '|' + geom.cells.length;
  if (sig === R3.sig && R3.objects.length) return;
  for (const o of R3.objects) gl.deleteBuffer(o.buf);
  R3.objects = [];
  R3.sig = sig;

  const Z = GEO.z, B = GEO.board;
  const add = (mesh, o) => {
    if (!mesh.pos.length) return null;
    const b = glBuffer(gl, mesh);
    const obj = { ...o, buf: b.buf, count: b.count };
    R3.objects.push(obj);
    return obj;
  };

  /* The desk. It exists so the underglow has something to land on — which is the whole point of
   * underglow, and is invisible without a surface below the board. */
  {
    const m = meshNew();
    const pad = 950, z = -Z.ground;   // large enough that the desk always fills the frame
    meshQuad(m, [B.x - pad, fy(B.y - pad), z], [B.x + B.w + pad, fy(B.y - pad), z],
      [B.x + B.w + pad, fy(B.y + B.h + pad), z], [B.x - pad, fy(B.y + B.h + pad), z], [0, 0, 1]);
    add(m, { kind: 'ground', mode: 2, baseKey: 'ground', spill: 0.22 });
  }

  // The board: one extruded rounded rectangle.
  {
    const m = meshNew();
    const outline = FY(roundedOutline(B.x, B.y, B.w, B.h, B.r, 8));
    meshWalls(m, outline, 0, outline, Z.board);
    meshFillPoly(m, outline, Z.board, 1);
    meshFillPoly(m, outline, 0, -1);
    add(m, { kind: 'board', mode: 2, baseKey: 'board', spill: 0.62 });
  }

  /* Underglow: eight emissive shares around the board's vertical edge, standing slightly proud of
   * it so the light reads as leaving the board rather than being painted on it. */
  const band = bandSlices(B.x, B.y, B.w, B.h, B.r, 10);
  for (const cell of geom.ugCells) {
    const line = FY(band.slice(cell.ringSlot, 16));
    const m = meshNew();
    const zTop = Z.board - Z.ug, zBot = zTop - GEO.ug.thick * 0.45;
    // Push each sample outward along its own outward normal, so corners stay square to the edge.
    const bcx = B.x + B.w / 2, bcy = fy(B.y + B.h / 2);
    const outer = line.map(([px, py]) => {
      const n = V3.norm([px - bcx, py - bcy, 0]);
      return [px + n[0] * Z.ugOut, py + n[1] * Z.ugOut];
    });
    for (let i = 0; i < line.length - 1; i++) {
      const a = [line[i][0], line[i][1], zTop], b = [line[i + 1][0], line[i + 1][1], zTop];
      const c = [outer[i + 1][0], outer[i + 1][1], zBot], d = [outer[i][0], outer[i][1], zBot];
      meshQuad(m, a, b, c, d, faceNormal(a, b, c));
    }
    add(m, { kind: 'ug', mode: 0, zone: 'underglow', index: cell.ring, pick: true });

    /* A share is clickable, so selecting or focusing one has to be visible. Its own stroke carries
     * the LED colour and cannot also carry a highlight — same problem the flat view solves with a
     * wider sibling path — so this is a thin rail sitting just outside it, drawn only when needed. */
    const rail = meshNew();
    const far = line.map(([px, py], i) => {
      const n = V3.norm([outer[i][0] - px, outer[i][1] - py, 0]);
      return [outer[i][0] + n[0] * 4.5, outer[i][1] + n[1] * 4.5];
    });
    for (let i = 0; i < line.length - 1; i++) {
      const a = [outer[i][0], outer[i][1], zBot], b = [outer[i + 1][0], outer[i + 1][1], zBot];
      const c = [far[i + 1][0], far[i + 1][1], zBot], d = [far[i][0], far[i][1], zBot];
      meshQuad(rail, a, b, c, d, [0, 0, 1]);
    }
    add(rail, { kind: 'ring', mode: 0, zone: 'underglow', index: cell.ring });
  }

  /* Keycaps. One rounded frustum per physical cap — narrower at the top, as a keycap is — with the
   * TOP FACE split per LED for the shared cap, exactly as the flat view splits it. Walls and top
   * are separate objects because they shade differently: the top is the colour, the wall is the
   * colour dimmed. */
  for (const cap of geom.caps) {
    const t = Z.capTaper;
    const base = FY(roundedOutline(cap.x, cap.y, cap.w, cap.h, GEO.key.r, 5));
    const top = FY(roundedOutline(cap.x + t, cap.y + t, cap.w - 2 * t, cap.h - 2 * t, Math.max(1, GEO.key.r - t * 0.4), 5));
    const zLo = Z.board, zHi = Z.board + Z.cap;
    for (const c of cap.cells) {
      const seam = cap.shared ? (c.capIndex === 0 ? c.hx1 : c.hx0) : undefined;
      const keepLess = cap.shared ? c.capIndex === 0 : true;
      const bp = cap.shared ? clipPolyX(base, seam, keepLess) : base;
      const tp = cap.shared ? clipPolyX(top, seam, keepLess) : top;
      const zone = 'keys', index = indexAtPos('keys', `${c.row},${c.col}`);

      const wall = meshNew();
      meshWalls(wall, bp, zLo, tp, zHi, cap.shared ? seam : undefined);
      add(wall, { kind: 'capWall', mode: 1, zone, index, pick: true });

      const face = meshNew();
      meshFillPoly(face, tp, zHi, 1);
      add(face, { kind: 'capTop', mode: 0, zone, index, pick: true });

      // The index / label decal, as a quad over the cap's own top face.
      const dw = Math.min(30, cap.w * 0.62), dh = 24;
      if (tp.length && index !== null) {
        const dcx = cap.shared ? (c.hx0 + c.hx1) / 2 : cap.x + cap.w / 2;
        const dcy = fy(cap.y + cap.h / 2);
        const dm = meshNew();
        const u0 = index / KEY_COUNT, u1 = (index + 1) / KEY_COUNT;
        // The atlas cell's row 0 is its top, and after the y flip larger world y is up on screen,
        // so the top edge of the quad (dcy + dh/2) takes v = 0.
        meshQuad(dm,
          [dcx - dw / 2, dcy + dh / 2, zHi + 0.06], [dcx + dw / 2, dcy + dh / 2, zHi + 0.06],
          [dcx + dw / 2, dcy - dh / 2, zHi + 0.06], [dcx - dw / 2, dcy - dh / 2, zHi + 0.06],
          [0, 0, 1], [[u0, 0], [u1, 0], [u1, 1], [u0, 1]]);
        add(dm, { kind: 'decal', mode: 3, zone, index });
      }

      /* A ring just above the cap's top face, drawn only when this LED is focused, selected or has
       * just fired an event — the 3D stand-in for the SVG's stroke, and how focus stays visible
       * when the focusable element itself is the invisible SVG underneath. */
      const ring = meshNew();
      meshRing(ring, tp, zHi + 0.1, 2.6);
      add(ring, { kind: 'ring', mode: 0, zone, index });
    }
    if (cap.shared) {
      // The hairline that says "one keycap, two LEDs" — the two halves are often near-identical.
      for (const c of cap.cells.slice(1)) {
        const m = meshNew();
        const z = Z.board + Z.cap + 0.04;
        const y0 = fy(cap.y + Z.capTaper + 3), y1 = fy(cap.y + cap.h - Z.capTaper - 3);
        meshQuad(m, [c.hx0 - 0.35, y0, z], [c.hx0 + 0.35, y0, z],
          [c.hx0 + 0.35, y1, z], [c.hx0 - 0.35, y1, z], [0, 0, 1]);
        add(m, { kind: 'seam', mode: 0, colorKey: 'faint' });
      }
    }
  }

  // The three status LEDs: small emissive plates sitting on the board's surface.
  for (const c of geom.statusCells) {
    const m = meshNew();
    const o = FY(roundedOutline(c.x, c.y, c.w, c.h, GEO.status.r, 3));
    meshWalls(m, o, Z.board, o, Z.board + Z.status);
    meshFillPoly(m, o, Z.board + Z.status, 1);
    add(m, { kind: 'status', mode: 0, zone: 'status', index: c.i, pick: true });
  }

  /* The non-key controls, as solids rather than glyphs — an encoder that is a knob you can see the
   * side of is more use for orientation than an outline of one. None of them carries an LED. */
  for (const f of geom.featureCells) {
    const m = meshNew();
    const r = Math.min(f.w, f.h) / 2 - 6;
    const fcy = fy(f.cy);
    if (f.kind === 'encoder') {
      meshCylinder(m, f.cx, fcy, r, Z.board, Z.board + Z.knob);
      meshCylinder(m, f.cx, fcy, r * 0.42, Z.board + Z.knob, Z.board + Z.knob + 1.2);
    } else if (f.kind === 'joystick') {
      meshCylinder(m, f.cx, fcy, r, Z.board, Z.board + 2.2);
      meshCylinder(m, f.cx, fcy, r * 0.4, Z.board + 2.2, Z.board + Z.knob);
    } else {
      const pad = f.padLeft || 0;
      const bx = f.x + pad + 4, bw = Math.max(14, f.w - pad - 8);
      const o = FY(roundedOutline(bx, f.cy - 13, bw, 26, 7, 4));
      meshWalls(m, o, Z.board, o, Z.board + 0.8);
      meshFillPoly(m, o, Z.board + 0.8, 1);
    }
    add(m, { kind: 'feat', mode: 1, feat: f.kind, pick: true, colorKey: 'faint' });
  }

  // Light positions: 13 keys at their cap tops, 8 underglow at their share centres under the rim.
  for (const k of geom.keys) {
    const c = k.cell;
    const i = k.index;
    if (i >= KEY_COUNT) break;
    R3.lightPos[i * 4 + 0] = c ? c.cx : B.x + B.w / 2;
    R3.lightPos[i * 4 + 1] = fy(c ? c.cy : B.y + B.h / 2);
    R3.lightPos[i * 4 + 2] = Z.board + Z.cap * 0.85;
    R3.lightPos[i * 4 + 3] = 34;                       // spill radius: local to the cap
  }
  geom.ugCells.forEach((cell, ring) => {
    const [px, py] = band.centre(cell.ringSlot);
    const i = KEY_COUNT + ring;
    R3.lightPos[i * 4 + 0] = px;
    R3.lightPos[i * 4 + 1] = fy(py);
    R3.lightPos[i * 4 + 2] = Z.board - Z.ug;
    R3.lightPos[i * 4 + 3] = 95;
  });
}

/* ---------------------------------------------------- the keycap text atlas */

/** One texture, 13 cells wide, each carrying what that cap should say. Rebuilt only when the text
 *  changes, so the common case costs nothing. Mirrors what paint() writes into the SVG. */
function ensureAtlas(labels) {
  const gl = R3.gl;
  const sig = labels.join(' ');
  if (R3.tex && sig === R3.texSig) return R3.tex;
  R3.texSig = sig;
  const CW = 128, CH = 128;
  const cv = document.createElement('canvas');
  cv.width = CW * KEY_COUNT;
  cv.height = CH;
  const cx = cv.getContext('2d');
  cx.clearRect(0, 0, cv.width, cv.height);
  cx.fillStyle = '#fff';
  cx.textAlign = 'center';
  for (let i = 0; i < KEY_COUNT; i++) {
    const [idx, lab] = (labels[i] || ' ').split('');
    const x = i * CW + CW / 2;
    if (idx) {
      cx.font = '600 52px ui-monospace, Menlo, monospace';
      cx.textBaseline = 'middle';
      cx.fillText(idx, x, lab ? CH * 0.38 : CH * 0.5);
    }
    if (lab) {
      cx.font = '500 30px system-ui, sans-serif';
      cx.textBaseline = 'middle';
      cx.fillText(lab.slice(0, 10), x, idx ? CH * 0.72 : CH * 0.5);
    }
  }
  if (!R3.tex) R3.tex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, R3.tex);
  gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, cv);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  return R3.tex;
}

/* ------------------------------------------------------------------ drawing */

function camMatrix(aspect) {
  const B = GEO.board;
  const at = [B.x + B.w / 2, fy(B.y + B.h / 2), GEO.z.board / 2];
  const c = R3.cam;
  const cp = Math.cos(c.pitch), sp = Math.sin(c.pitch);
  /* Frame the board rather than sit at a fixed distance, so it looks the same at every viewport
   * width (this page is two columns above 1100 px and one below, so the canvas width really does
   * change by a factor of nearly two).
   *
   * The board's on-screen extent is its width across, and its depth foreshortened by the pitch plus
   * the standing height of the caps. Each axis is fitted to its own half-angle and the tighter of
   * the two wins; the fudge factor covers what this linear estimate leaves out, which is that
   * perspective makes the near edge bigger than an orthographic guess, and keeps the underglow rim
   * — which is data, not decoration — inside the frame rather than cropped at the corners. */
  const tv = Math.tan(FOV_Y / 2);
  const halfW = GEO.board.w / 2 + 16;
  const halfH = (GEO.board.h * sp + (GEO.z.board + GEO.z.cap) * cp) / 2 + 16;
  const dist = Math.max(halfH / tv, halfW / (aspect * tv)) * 1.45 * c.zoom;
  // Aim a little past the board's centre, away from the viewer: the projected shape is a trapezoid
  // whose near edge is the big one, so aiming at the true centre leaves the picture bottom-heavy.
  at[1] += 34 * cp;
  // yaw 0 puts the camera in FRONT of the pad. World y is minus SVG y, so "in front" — the bottom
  // row, larger SVG y — is NEGATIVE world y; get this sign wrong and you view the pad from behind,
  // which looks exactly like a correct render with every legend rotated 180 degrees.
  const eye = V3.add(at, V3.mul([Math.sin(c.yaw) * cp, -Math.cos(c.yaw) * cp, sp], dist));
  return M4.mul(M4.perspective(FOV_Y, aspect, 40, 3200), M4.lookAt(eye, at, [0, 0, 1]));
}

/* How tall the canvas is relative to its width. Chosen so the fitted board leaves a little air and
 * not a field of empty desk, at both the two-column and the one-column layout. */
const CANVAS_RATIO = 0.66;

function sizeCanvas() {
  const cv = R3.canvas;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const cssH = Math.max(120, Math.round(cv.clientWidth * CANVAS_RATIO));
  const w = Math.max(1, Math.round(cv.clientWidth * dpr));
  const h = Math.max(1, Math.round(cssH * dpr));
  if (cv.width !== w || cv.height !== h) {
    cv.width = w; cv.height = h;
    cv.style.height = cssH + 'px';
  }
  return cv.width / cv.height;
}

/** What decoration an LED should carry: an event hit, the selection, or focus — the same order of
 *  precedence, and the same colours, the flat view's strokes use. */
function ringFor(zone, index, th) {
  if (zone === 'keys') {
    const age = hitAge('key', index);
    if (age && state.evFlash) return age.source === 'device' ? th.ok : th.accent;
  }
  const sel = state.sel;
  if (sel && sel.zone === zone && sel.index === index) return th.accent;
  const f = state.view.focusLed;
  if (f && f.zone === zone && f.index === index) return th.ink;
  if (zone === 'keys' && state.bind.control === 'key' && state.bind.index === index) return V3.mul(th.accent, 0.55);
  return null;
}

function draw3d(frame, pickPass) {
  const gl = R3.gl;
  if (!gl) return;
  const aspect = sizeCanvas();
  build3dScene();
  const th = R3.theme || (R3.theme = themeColors());
  const mvp = camMatrix(aspect);

  // LED colours into the light arrays: exactly the frame's colours, so the spill under a cap is
  // that cap's own colour rather than a tint of it.
  for (let i = 0; i < KEY_COUNT; i++) {
    const c = frame.keys[i] || [0, 0, 0];
    R3.lightCol[i * 3] = c[0]; R3.lightCol[i * 3 + 1] = c[1]; R3.lightCol[i * 3 + 2] = c[2];
  }
  for (let i = 0; i < UG_COUNT; i++) {
    const c = frame.ug[i] || [0, 0, 0];
    const j = (KEY_COUNT + i) * 3;
    R3.lightCol[j] = c[0]; R3.lightCol[j + 1] = c[1]; R3.lightCol[j + 2] = c[2];
  }

  gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
  if (pickPass) gl.clearColor(0, 0, 0, 1);
  else gl.clearColor(th.ground[0] * 0.55, th.ground[1] * 0.55, th.ground[2] * 0.55, 1);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  gl.useProgram(R3.prog);
  gl.uniformMatrix4fv(R3.loc.uMvp, false, new Float32Array(mvp));
  gl.uniform4fv(R3.loc.uLpos, R3.lightPos);
  gl.uniform3fv(R3.loc.uLcol, R3.lightCol);
  gl.disable(gl.BLEND);
  gl.depthMask(true);

  const showIdx = $('chk-indices').checked;
  const showLab = $('chk-labels').checked;
  const sweeping = state.identify.active ? state.identify.target : null;
  const atlas = pickPass ? null : ensureAtlas(Array.from({ length: KEY_COUNT }, (_, i) => {
    const shown = sweeping === 'keys' ? state.geom?.keys?.[i]?.strip : i;
    const idx = (showIdx || sweeping === 'keys') ? (shown === null || shown === undefined ? '—' : String(shown)) : '';
    const lab = showLab ? (keyLabelOf(i) || '') : '';
    return idx + '' + lab;
  }));
  if (atlas) {
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, atlas);
    gl.uniform1i(R3.loc.uTex, 0);
  }

  const bindBuf = (o) => {
    gl.bindBuffer(gl.ARRAY_BUFFER, o.buf);
    const stride = 8 * 4;
    gl.enableVertexAttribArray(R3.loc.aPos);
    gl.vertexAttribPointer(R3.loc.aPos, 3, gl.FLOAT, false, stride, 0);
    gl.enableVertexAttribArray(R3.loc.aNrm);
    gl.vertexAttribPointer(R3.loc.aNrm, 3, gl.FLOAT, false, stride, 12);
    gl.enableVertexAttribArray(R3.loc.aUv);
    gl.vertexAttribPointer(R3.loc.aUv, 2, gl.FLOAT, false, stride, 24);
  };

  const ledColor = (o) => {
    if (o.zone === 'keys') return frame.keys[o.index] || [0, 0, 0];
    if (o.zone === 'underglow') return frame.ug[o.index] || [0, 0, 0];
    if (o.zone === 'status') {
      const d = frame.status[o.index] || 0;
      return [1, 0.94, 0.86].map((c) => (c * d) / 255);
    }
    return th[o.colorKey || 'faint'];
  };

  // Two passes: opaque first, then the decals with blending. Rings ride with the opaque pass.
  const deferred = [];
  for (let oi = 0; oi < R3.objects.length; oi++) {
    const o = R3.objects[oi];
    if (o.kind === 'decal') { if (!pickPass && atlas) deferred.push(o); continue; }
    let ringColor = null;
    if (o.kind === 'ring') {
      if (pickPass) continue;
      ringColor = ringFor(o.zone, o.index, th);
      if (!ringColor) continue;
    }
    bindBuf(o);
    if (pickPass) {
      if (!o.pick) { gl.uniform1i(R3.loc.uMode, 0); gl.uniform3f(R3.loc.uColor, 0, 0, 0); }
      else {
        const id = oi + 1;
        gl.uniform1i(R3.loc.uMode, 0);
        gl.uniform3f(R3.loc.uColor, ((id >> 16) & 255) / 255, ((id >> 8) & 255) / 255, (id & 255) / 255);
      }
      gl.drawArrays(gl.TRIANGLES, 0, o.count);
      continue;
    }
    gl.uniform1i(R3.loc.uMode, o.kind === 'ring' ? 0 : o.mode);
    if (o.mode === 2) {
      gl.uniform3fv(R3.loc.uBase, new Float32Array(th[o.baseKey]));
      gl.uniform1f(R3.loc.uSpill, o.spill);
    }
    const col = o.kind === 'ring' ? ringColor : ledColor(o);
    gl.uniform3f(R3.loc.uColor, col[0], col[1], col[2]);
    gl.drawArrays(gl.TRIANGLES, 0, o.count);
  }

  if (deferred.length) {
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.depthMask(false);
    gl.uniform1i(R3.loc.uMode, 3);
    for (const o of deferred) {
      bindBuf(o);
      const ink = hexToRgb(inkFor(ledColor(o)).replace('#', ''));
      gl.uniform3f(R3.loc.uColor, ink[0], ink[1], ink[2]);
      gl.drawArrays(gl.TRIANGLES, 0, o.count);
    }
    gl.depthMask(true);
    gl.disable(gl.BLEND);
  }
}

/* ------------------------------------------------------------------ picking */

/** Exactly which surface is under a client point: render ids to the colour buffer and read the
 *  pixel. Exact by construction — no ray/box arithmetic to get subtly wrong. */
function pick3d(clientX, clientY) {
  const gl = R3.gl;
  if (!gl || !state.lastFrame) return null;
  const r = R3.canvas.getBoundingClientRect();
  if (clientX < r.left || clientX > r.right || clientY < r.top || clientY > r.bottom) return null;
  draw3d(state.lastFrame, true);
  const px = Math.round(((clientX - r.left) / r.width) * gl.drawingBufferWidth);
  const py = Math.round(((r.bottom - clientY) / r.height) * gl.drawingBufferHeight);
  const buf = new Uint8Array(4);
  gl.readPixels(px, py, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, buf);
  draw3d(state.lastFrame, false);           // put the real picture back before anything composites
  const id = (buf[0] << 16) | (buf[1] << 8) | buf[2];
  if (!id) return null;
  const o = R3.objects[id - 1];
  if (!o || !o.pick) return null;
  return o.feat ? { feat: o.feat } : { zone: o.zone, index: o.index };
}

/* ----------------------------------------------------------- orbit + pointer */

function wire3dPointer(canvas) {
  const DRAG_PX = 4;
  canvas.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return;
    canvas.setPointerCapture(ev.pointerId);
    R3.drag = { x: ev.clientX, y: ev.clientY, yaw: R3.cam.yaw, pitch: R3.cam.pitch, moved: 0 };
  });
  canvas.addEventListener('pointermove', (ev) => {
    if (R3.drag) {
      const dx = ev.clientX - R3.drag.x, dy = ev.clientY - R3.drag.y;
      R3.drag.moved = Math.max(R3.drag.moved, Math.hypot(dx, dy));
      if (R3.drag.moved > DRAG_PX) {
        canvas.classList.add('orbiting');
        R3.cam.yaw = R3.drag.yaw - dx * 0.006;
        // Never below the desk and never quite overhead: past vertical the pad reads as mirrored.
        R3.cam.pitch = clamp(R3.drag.pitch + dy * 0.006, 0.12, 1.5533);
      }
      return;
    }
    // Hover: the cursor has to say what is clickable, and only picking knows.
    hover3d(ev.clientX, ev.clientY);
  });
  const end = (ev) => {
    if (!R3.drag) return;
    const wasDrag = R3.drag.moved > DRAG_PX;
    R3.drag = null;
    canvas.classList.remove('orbiting');
    if (wasDrag) { prefs.write('cam', { ...R3.cam }); return; }
    const hit = pick3d(ev.clientX, ev.clientY);
    if (hit) activate3d(hit);
  };
  canvas.addEventListener('pointerup', end);
  canvas.addEventListener('pointercancel', () => { R3.drag = null; canvas.classList.remove('orbiting'); });
  canvas.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    R3.cam.zoom = clamp(R3.cam.zoom * (1 + Math.sign(ev.deltaY) * 0.08), 0.55, 2.4);
    prefs.write('cam', { ...R3.cam });
  }, { passive: false });
}

const hover3d = throttleTrailing((x, y) => {
  if (!R3.gl || R3.drag) return;
  const hit = pick3d(x, y);
  const key = hit ? (hit.feat || `${hit.zone}:${hit.index}`) : '';
  R3.canvas.style.cursor = hit ? 'pointer' : 'grab';
  if (key !== R3.hover) R3.hover = key;
}, 70);

/** A click in the 3D scene, routed to exactly what a click on the SVG would do. */
function activate3d(hit) {
  if (hit.feat) {
    selectControl(hit.feat, hit.feat === 'joystick' ? joyDefaultDir() : 0);
    showTab('bindings');
    return;
  }
  const ref = hit.zone === 'keys'
    ? [...svgRefs.keys.values()].find((r) => indexAtPos('keys', `${r.cell.row},${r.cell.col}`) === hit.index)
    : hit.zone === 'underglow'
      ? svgRefs.ug.get(state.geom.ug[hit.index]?.pos?.join(',') || '')
      : svgRefs.status[hit.index];
  // Go through the SVG's own group so there is ONE activation path for both views: identify
  // recording, selection, tab switching and the shared-cap notes all behave identically.
  if (ref && ref.g) { ref.g.dispatchEvent(new MouseEvent('click', { bubbles: true })); ref.g.focus({ preventScroll: true }); }
}

/* ------------------------------------------------------- switching the views */

function applyViewMode() {
  const host = document.querySelector('.deviceview');
  const want3d = state.view.threeD && !R3.failed && !state.identify.active;
  // Asked for 3D and it cannot be had: say so rather than quietly staying flat.
  if (want3d && !R3.gl && !gl3dInit()) { fall2d(); return; }
  const want = want3d ? '3d' : '2d';
  if (host.dataset.view === want) return;
  host.dataset.view = want;
  if (want === '3d') {
    build3dScene();
    if (state.lastFrame) draw3d(state.lastFrame, false);
  } else {
    R3.canvas && (R3.canvas.style.cursor = '');
  }
}

const view3dActive = () => document.querySelector('.deviceview')?.dataset.view === '3d';

/** Logical indices of the OTHER LEDs under the same physical keycap as this position. */
function capMateIndices(posKey) {
  const cap = state.geom?.capOf?.get(posKey);
  if (!cap || !cap.shared) return [];
  return cap.cells
    .map((c) => `${c.row},${c.col}`)
    .filter((k) => k !== posKey)
    .map((k) => indexAtPos('keys', k))
    .filter((i) => i !== null);
}

/** The LED index at a physical position: logical for keys, ring position for underglow.
 *  Pure geometry — independent of the strip wiring mapping, and what the config records. */
function indexAtPos(zone, posKey) {
  const m = zone === 'keys' ? state.geom.keyPosToIndex : state.geom.ugPosToIndex;
  const v = m.get(posKey);
  return v === undefined ? null : v;
}

/** The strip index wired to a physical position — the pending sweep map wins while identifying. */
function stripAtPos(zone, posKey) {
  if (state.identify.active && state.identify.target === zone) {
    const list = state.identify.map[zone];
    const i = list.findIndex((p) => Array.isArray(p) && p.join(',') === posKey);
    return i === -1 ? null : i;
  }
  const m = zone === 'keys' ? state.geom.keyPosToStrip : state.geom.ugPosToStrip;
  const v = m.get(posKey);
  return v === undefined ? null : v;
}

/** A key's label in the scope being edited, falling back to the profile's for a mode. */
function keyLabelOf(index) {
  const own = keyEntry(index);
  if (own && own.label) return own.label;
  if (!state.scope) return '';
  const p = currentProfile();
  const base = (Array.isArray(p?.keys) ? p.keys : []).find((x) => x && x.index === index);
  return (base && base.label) || '';
}

/* ------------------------------------------------------------- event highlight */

/* How long a control stays lit on the board after its event arrives. Long enough to catch out of
 * the corner of an eye while tapping down a row of keys, short enough that two presses of the
 * same key still read as two. */
const HIT_MS = 900;

function noteHit(control, index, source) {
  state.hits.set(`${control}:${index}`, { at: performance.now(), source });
}

/** Decay state for a control's most recent event: {f: 1..0, source} or null. */
function hitAge(control, index) {
  const k = `${control}:${index}`;
  const h = state.hits.get(k);
  if (!h) return null;
  const f = 1 - (performance.now() - h.at) / HIT_MS;
  if (f <= 0) { state.hits.delete(k); return null; }
  return { f, source: h.source };
}

/** Paint the highlight onto an LED shape. Inline style, because the stroke rules in style.css
 *  are CSS declarations and would win over a presentation attribute. */
function applyHit(shape, age) {
  if (!age || !state.evFlash) {
    if (shape.style.stroke) { shape.style.stroke = ''; shape.style.strokeWidth = ''; shape.style.strokeDasharray = ''; }
    return;
  }
  shape.style.stroke = age.source === 'device' ? 'var(--ok)' : 'var(--accent)';
  shape.style.strokeWidth = String(1.6 + 2.4 * age.f);
  // Injected events are drawn dashed. A simulated press must never look like a real one.
  shape.style.strokeDasharray = age.source === 'device' ? '' : '5 3';
}

function paint(frame) {
  const showIdx = $('chk-indices').checked;
  const showLab = $('chk-labels').checked;
  const sel = state.sel;
  // A sweep is about strip numbering, so the board switches to it for the duration.
  const sweeping = state.identify.active ? state.identify.target : null;
  /* `frame.keys` / `frame.ug` are what to PAINT — brightness already folded in for a mirrored
   * frame. `rawKeys` / `rawUg` are the LED's own colour before dimming, which is what `data-hex`
   * and the accessible name report, because "this LED is #00ff9e and the pad is at 16% brightness"
   * is two facts and collapsing them would lose one. */
  const rawK = frame.rawKeys || frame.keys;
  const rawU = frame.rawUg || frame.ug;
  const dim = frame.dim === undefined ? 1 : frame.dim;
  const dimSay = dim < 0.995 ? `, dimmed to ${Math.round(dim * 100)} percent on the pad` : '';

  for (const [id, ref] of svgRefs.keys) {
    const i = indexAtPos('keys', id);
    const strip = stripAtPos('keys', id);
    const shown = sweeping === 'keys' ? strip : i;
    const rgb = i === null ? [0.06, 0.07, 0.09] : frame.keys[i] || [0, 0, 0];
    const hex = '#' + rgbToHex(rgb);
    ref.rect.setAttribute('fill', hex);
    svgRefs.glow.get('key:' + id).setAttribute('fill', hex);
    // The frame colour exactly as the source gave it, undimmed — what the mirroring check reads.
    ref.g.dataset.hex = i === null ? '' : rgbToHex(rawK[i] || [0, 0, 0]);
    ref.idx.textContent = showIdx || sweeping === 'keys' ? (shown === null ? '—' : String(shown)) : '';
    ref.idx.setAttribute('fill', inkFor(rgb));
    const label = i === null ? '' : keyLabelOf(i);
    const entry = i === null ? null : keyEntry(i);
    ref.lab.textContent = showLab && label ? label.slice(0, 12) : '';
    ref.lab.setAttribute('fill', inkFor(rgb));
    // keep the pair optically centred whether or not a label is showing
    ref.idx.setAttribute('y', ref.lab.textContent ? ref.cell.cy - 1 : ref.cell.cy + 4);
    ref.g.dataset.unmapped = strip === null ? '1' : '0';
    ref.g.dataset.sel = sel && sel.zone === 'keys' && sel.index === i ? '1' : '0';
    ref.g.dataset.bindsel = state.bind.control === 'key' && state.bind.index === i ? '1' : '0';
    applyHit(ref.rect, i === null ? null : hitAge('key', i));
    const mates = capMateIndices(id);
    ref.g.setAttribute('aria-label',
      `key at row ${ref.cell.row + 1}, grid column ${ref.cell.gcol + 1}` +
      (i === null ? ', no LED index' : `, index ${i}`) +
      (strip === null ? ', no strip index mapped' : `, strip index ${strip}`) +
      (label ? `, ${label}` : '') +
      (mates.length ? `, one wide keycap shared with index ${mates.join(' and ')}` : '') +
      (i === null ? '' : `, colour ${rgbToHex(rawK[i] || [0, 0, 0])}${dimSay}`));
  }

  // Encoder / touch-pad ghosts light up for their events too, so a rotate or a tap is as
  // visible on the board as a key press.
  for (const [kind, ref] of svgRefs.feat) {
    const age = hitAge(kind === 'joystick' ? 'joystick' : kind, 0);
    ref.g.dataset.hit = age ? age.source : '';
    ref.g.style.opacity = age ? String(0.5 + 0.5 * age.f) : '';
  }

  for (const [id, ref] of svgRefs.ug) {
    const i = indexAtPos('underglow', id);
    const strip = stripAtPos('underglow', id);
    const shown = sweeping === 'underglow' ? strip : i;
    const rgb = i === null ? [0.06, 0.07, 0.09] : frame.ug[i] || [0, 0, 0];
    const hex = '#' + rgbToHex(rgb);
    // A share is a STROKED slice of the perimeter path, so its colour is its stroke.
    ref.rect.setAttribute('stroke', hex);
    ref.glow.setAttribute('stroke', hex);
    ref.idx.textContent = showIdx || sweeping === 'underglow' ? (shown === null ? '—' : String(shown)) : '';
    ref.idx.setAttribute('fill', inkFor(rgb));
    ref.g.dataset.unmapped = strip === null ? '1' : '0';
    ref.g.dataset.sel = sel && sel.zone === 'underglow' && sel.index === i ? '1' : '0';
    ref.g.dataset.hex = i === null ? '' : rgbToHex(rawU[i] || [0, 0, 0]);
    ref.g.setAttribute('aria-label',
      `underglow at grid x ${ref.cell.gx} y ${ref.cell.gy}` +
      (i === null ? '' : `, ring position ${i}, an eighth of the board perimeter centred on that point`) +
      (strip === null ? ', no strip index mapped' : `, strip index ${strip}`) +
      (i === null ? '' : `, colour ${rgbToHex(rawU[i] || [0, 0, 0])}${dimSay}`));
  }

  svgRefs.status.forEach((ref, i) => {
    // Not brightness-scaled, and that is not an oversight: `bright` in the firmware scales the two
    // WS2812 chains only (main.c's `scale()`), while these three are their own LEDC PWM channels.
    const d = frame.status[i] || 0;
    const rgb = [1, 0.94, 0.86].map((c) => (c * d) / 255);
    const hex = '#' + rgbToHex(rgb);
    ref.rect.setAttribute('fill', hex);
    svgRefs.glow.get('st:' + String(i)).setAttribute('fill', hex);
    ref.idx.textContent = showIdx ? String(d) : '';
    ref.idx.setAttribute('fill', inkFor(rgb));
    ref.g.dataset.sel = sel && sel.zone === 'status' && sel.index === i ? '1' : '0';
    ref.g.setAttribute('aria-label', `status LED ${i}, duty ${d}`);
  });

  $('device').classList.toggle('identifying', state.identify.active);
  applyViewMode();
}

/* ============================================ 10. mirroring the device
 *
 * The device view has two possible sources, and which one it is showing is stated on screen,
 * because a simulation that claims to be the device is worse than no picture at all.
 *
 *   DEVICE   GET /api/frame, polled. The daemon composes the frame it is putting on the pad —
 *            effects.py, the real clock, idle dimming, flashes, pulses, the volume bar, whatever
 *            a preview has taken over — and answers with it. Nothing is recomputed here; the
 *            colours painted are the colours returned, scaled by the brightness the daemon says
 *            is actually applied. There is no second implementation to drift. Falls back to the
 *            preview, saying so, when nothing answers.
 *
 *   PREVIEW  computeFrame(), this file's own reading of the config being edited. It is a design
 *            tool: it works with no daemon and no pad, and it shows UNSAVED edits, which the pad
 *            by definition cannot. It is never labelled as the device.
 *
 *   OFF      Preview off: the configured BASE layer only — per-key colours, the underglow base,
 *            the status duties — with no effect and no animation. The quiet state, and the one to
 *            pick when the board is a map you are aiming at rather than a picture.
 *
 * One three-way radio group picks between them, and the badge over the board always says which one
 * you are looking at. There is deliberately no second "animate" control: two overlapping switches
 * whose difference nobody can state is how the original bug got to hide.
 *
 * Polling rather than a stream: the daemon has no push channel, and /api/frame composes a frame
 * per request, so the rate is capped by device.fps and backed off hard whenever nobody is looking
 * — a hidden tab, a board scrolled out of view, a non-device source, or no daemon at all.
 */

const MIRROR_MIN_MS = 55;          // ~18 Hz ceiling however high device.fps is set
const MIRROR_STALE_MS = 1200;      // a frame older than this plus one interval is not "now"
const MIRROR_IDLE_MS = 1400;       // preview pinned, or board scrolled off: just enough to know
const MIRROR_HIDDEN_MS = 3000;     // document.hidden — nobody is looking at all
const MIRROR_RETRY_MS = 2500;      // no daemon answering

function mirrorInterval() {
  if (document.hidden) return MIRROR_HIDDEN_MS;
  if (state.daemonReachable === false) return MIRROR_RETRY_MS;
  if (!state.view.onscreen || state.view.source !== 'device') return MIRROR_IDLE_MS;
  return Math.max(MIRROR_MIN_MS, Math.round(1000 / deviceFps()));
}

/** Is the last mirrored frame recent enough to be called "what the pad is showing"? */
function mirrorFresh() {
  const m = state.mirror;
  return !!(m.ok && m.keys) && (performance.now() - m.at) < MIRROR_STALE_MS + mirrorInterval();
}

/** What the device view is showing right now: 'device', 'preview' or 'off'.
 *
 *  Only 'device' can be refused — asking for the pad when nothing answers falls back to the
 *  preview, which the badge then labels as a preview and explains. */
function viewMode() {
  if (state.view.source !== 'device') return state.view.source;
  return mirrorFresh() ? 'device' : 'preview';
}

/** The last mirrored frame, as a paintable frame. Brightness is applied here and only here. */
function mirrorFrame() {
  const m = state.mirror;
  if (!m.keys) return null;
  const dim = clamp(m.brightness / 255, 0, 1);
  return {
    keys: m.keys.map((c) => scale(c, dim)),
    ug: m.ug.map((c) => scale(c, dim)),
    status: m.status.slice(),
    rawKeys: m.keys, rawUg: m.ug,
    dim, source: 'device', connected: m.connected, seq: m.seq,
  };
}

const padTo = (arr, n, fill) => {
  const out = arr.slice(0, n);
  while (out.length < n) out.push(Array.isArray(fill) ? fill.slice() : fill);
  return out;
};

let frameTimer = null;
let framePolling = false;

async function pollFrame() {
  if (framePolling) return;                 // one request in flight; it reschedules itself
  clearTimeout(frameTimer); frameTimer = null;
  framePolling = true;
  let res;
  try { res = await api.getFrame(); } finally { framePolling = false; }
  const m = state.mirror;
  const d = res.data;
  if (res.ok && d && Array.isArray(d.keys) && Array.isArray(d.underglow)) {
    // Indexed exactly as this page indexes LEDs already: keys by LOGICAL index, underglow by RING
    // position. No translation, which is the point — the daemon's transport owns strip order.
    m.keys = padTo(d.keys.map(hexToRgb), KEY_COUNT, [0, 0, 0]);
    m.ug = padTo(d.underglow.map(hexToRgb), UG_COUNT, [0, 0, 0]);
    m.status = padTo((Array.isArray(d.status) ? d.status : []).map((v) => clamp(Math.round(Number(v) || 0), 0, 255)), STATUS_COUNT, 0);
    m.brightness = Number.isFinite(Number(d.brightness)) ? clamp(Math.round(Number(d.brightness)), 0, 255) : 255;
    m.connected = !!d.connected;
    m.at = performance.now();
    m.seq++;
    m.ok = true;
    m.error = null;
  } else {
    m.ok = false;
    m.error = res.reachable
      ? (res.status === 404 ? 'this daemon has no /api/frame' : res.error || 'the daemon would not give a frame')
      : 'no daemon is answering';
  }
  renderViewSource();
  frameTimer = setTimeout(pollFrame, mirrorInterval());
}

/* ---------------------------------------------------------- saying which one */

/** Which of the three the board is showing, said in the badge over it and in a line beneath. */
function renderViewSource() {
  const mode = viewMode();
  const m = state.mirror;
  const badge = $('view-src');
  if (!badge) return;

  let dot = 'off', label = '', why = [];
  if (mode === 'device') {
    dot = m.connected ? 'on' : 'warn';
    label = m.connected ? 'mirroring the device' : 'mirroring the daemon · no device attached';
    why.push(m.connected
      ? 'Every colour on this board is the frame the daemon is putting on the pad right now — read from GET /api/frame, not recomputed here.'
      : 'The daemon is composing frames but reports no pad connected, so this is what it WOULD send.');
    if (m.brightness <= 0) {
      // Faithful and unhelpful at the same time, so it says which knob gets the picture back.
      why.push('The pad\'s LEDs are off right now — brightness 0, which is what an idle timeout does — so this board is dark too. Touch the pad, or switch to Preview to see the lighting the config describes.');
    } else if (m.brightness < 250) {
      why.push(`The pad is at brightness ${m.brightness}/255 (idle dimming included), so the board is drawn that dim too.`);
    }
    if (state.dirty && !state.live) {
      why.push('You have unsaved edits: the pad is still running the saved config, so they are not on this board. Save, tick “Live preview on device”, or switch to Preview to see them.');
    } else if (state.live) {
      why.push('Live preview is on, so what the pad is showing — and therefore this board — is this page\'s design.');
    }
  } else if (mode === 'off') {
    label = 'preview off · base colours only';
    why.push('Preview is off, so this is the configured base layer and nothing else: per-key colours, the underglow base colour and the status duties, with no effect and no animation. Not a reading of any hardware.');
  } else {
    label = 'preview · not the device';
    const asked = state.view.source === 'device';
    const reason = asked
      ? (m.error ? `${m.error.charAt(0).toUpperCase()}${m.error.slice(1)}, so Device is not available. ` : 'No frame from the daemon yet. ')
      : '';
    why.push(`${reason}This is this page's own animated simulation of the config being edited — including unsaved changes — and not a reading of any hardware.`);
  }
  const note = why.join(' ');

  // Rebuilding text at frame rate would churn the DOM and fight a screen reader, so nothing is
  // written unless what it says has changed.
  const sig = `${mode}|${dot}|${label}|${note}`;
  if (badge.dataset.sig === sig) return;
  badge.dataset.sig = sig;
  badge.dataset.mode = mode;
  badge.querySelector('.dot').dataset.state = dot;
  badge.querySelector('.v').textContent = label;
  $('view-note').textContent = note;
}

/* ==================================================== 10b. the paint loop */

function tick(ts) {
  const a = state.anim;
  if (!a.lastTs) a.lastTs = ts;
  const dt = (ts - a.lastTs) / 1000;
  a.lastTs = ts;
  const mode = viewMode();
  // The preview clock only advances when a preview is what is on screen — or when live preview is
  // streaming this page's frames to the pad, which needs the clock whatever the board shows.
  if (mode === 'preview' || (state.live && state.previewChannel === 'frame')) a.t += dt;
  const interval = 1000 / deviceFps();
  if (ts - a.lastPaint >= interval - 1) {
    a.lastPaint = ts;
    try {
      /* The local simulation runs only when it is what is on screen. While mirroring, computing it
       * would be work whose only possible use is to disagree with the pad. */
      if (mode !== 'device') state.localFrame = computeFrame(a.t, mode !== 'off');
      const frame = mode === 'device' ? mirrorFrame() : state.localFrame;
      if (frame) {
        state.lastFrame = frame;
        paint(frame);
        if (view3dActive()) draw3d(frame, false);
      }
      renderViewSource();
    } catch (e) { console.error(e); }
  }
  requestAnimationFrame(tick);
}

/* ==================================================== 11. device preview */

/* A pushed frame carries a ttl so the pad reverts on its own if this page goes away; the
 * heartbeat below is what keeps it alive for as long as the toggle is on. */
const PREVIEW_TTL_S = 6;
const HEARTBEAT_MS = 2500;
const EFFECT_REPUSH_MS = 240000;

const pushFrame = throttleTrailing(() => {
  if (!state.live) return;
  /* Computed here rather than reused from whatever is on screen, and that is the point: the board
   * may be mirroring the pad (pushing that back would be a feedback loop that ate the config's
   * lighting one frame at a time) or showing base colours only with "Off". What live preview
   * streams is always this page's full design, whatever the board happens to be displaying. */
  const f = computeFrame(state.anim.t);
  state.previewChannel = 'frame';
  api.previewFrame({ ...frameToWire(f), ttl: PREVIEW_TTL_S }).then((r) => {
    if (!r.ok && !r.reachable) setLive(false, 'daemon unreachable — live preview off');
  });
}, () => 1000 / deviceFps());

/* The daemon renders /api/preview/effect against the palettes it has loaded, so an effect
 * pointing at a palette that only exists in this page's unsaved edits cannot be delegated —
 * fall back to streaming composited frames, which are self-describing. */
function canDelegateEffect(eff) {
  if (!eff || !eff.name) return false;
  const name = eff.palette;
  if (!name) return true;
  if (state.builtins[name]) return true;
  return !state.dirty; // a config palette is only on the daemon once the config is saved
}

const pushEffect = debounce(() => {
  if (!state.live) return;
  const eff = currentEffect();
  if (!canDelegateEffect(eff)) { pushFrame(); return; }
  state.previewChannel = 'effect';
  state.lastEffectPush = performance.now();
  api.previewEffect(normalisedEffect(eff)).then((r) => {
    if (!r.ok && !r.reachable) { setLive(false, 'daemon unreachable — live preview off'); return; }
    if (r.data && r.data.ok === false) toast('Daemon rejected the effect: ' + (r.data.errors || []).join('; '), 'err', 6000);
  });
}, 140);

let heartbeat = null;
function beat() {
  if (!state.live) { heartbeat = null; return; }
  let delay = HEARTBEAT_MS;
  if (state.previewChannel === 'effect') {
    // Re-pushing an effect restarts its animation phase, so only do it near the daemon's own
    // preview expiry rather than on every beat.
    if (performance.now() - state.lastEffectPush > EFFECT_REPUSH_MS) pushEffect();
  } else {
    pushFrame();
    // When the daemon can't own the animation, this page has to stream it at frame rate.
    const eff = currentEffect();
    if (eff && eff.name !== 'off' && Number(eff.speed ?? EFFECT_DEFAULTS.speed) > 0) {
      delay = Math.max(1000 / Math.min(deviceFps(), 30), 33);
    }
  }
  heartbeat = setTimeout(beat, delay);
}
function startHeartbeat() { stopHeartbeat(); heartbeat = setTimeout(beat, HEARTBEAT_MS); }
function stopHeartbeat() { clearTimeout(heartbeat); heartbeat = null; }

/** The effect as the daemon should receive it: explicit values, schema-legal ranges. */
function normalisedEffect(eff) {
  const out = { name: eff.name };
  if (eff.palette) out.palette = eff.palette;
  out.speed = clamp(Number(eff.speed ?? EFFECT_DEFAULTS.speed), 0, 10);
  out.intensity = clamp(Number(eff.intensity ?? EFFECT_DEFAULTS.intensity), 0, 1);
  out.direction = DIRECTIONS.includes(eff.direction) ? eff.direction : EFFECT_DEFAULTS.direction;
  out.reverse = !!eff.reverse;
  out.target = TARGETS.includes(eff.target) ? eff.target : EFFECT_DEFAULTS.target;
  out.blend = BLENDS.includes(eff.blend) ? eff.blend : EFFECT_DEFAULTS.blend;
  return out;
}

function setLive(on, why) {
  state.live = !!on;
  $('chk-live').checked = state.live;
  if (why) toast(why, on ? 'ok' : 'warn');
  if (state.live) {
    const eff = currentEffect();
    if (canDelegateEffect(eff) && Number(eff.speed ?? EFFECT_DEFAULTS.speed) > 0) pushEffect();
    else pushFrame();
    startHeartbeat();
  } else {
    stopHeartbeat();
    state.previewChannel = null;
  }
}

function stopPreview() {
  setLive(false);
  api.previewStop().then((r) => {
    if (r.ok) toast('Device reverted to the config lighting', 'ok');
    else if (!r.reachable) toast('No daemon — nothing to revert', 'warn');
    else toast('Daemon refused stop: ' + (r.error || 'error'), 'err');
  });
}

/** Called after any config mutation. kind picks which preview channel to nudge. */
function touch(kind = 'frame') {
  state.dirty = true;
  state.jsonStale = true;
  renderTop();
  if ($('tab-config').getAttribute('aria-selected') === 'true') syncJson();
  if (kind === 'effect') pushEffect();
  else if (kind === 'frame') pushFrame();
}

/* ================================================ 12. colour editor widget */

class ColorEditor {
  constructor(mount, onChange, label = 'Colour') {
    this.onChange = onChange;
    this.hue = 250;
    this.hex = '000000';
    this.silent = false;

    this.root = el('div', { class: 'ce' });
    this.native = el('input', { type: 'color', 'aria-label': label + ' picker', value: '#000000' });
    this.hexIn = el('input', { type: 'text', class: 'ce-hex', 'aria-label': label + ' hex', spellcheck: 'false', maxlength: '7' });
    this.sw = el('div', { class: 'ce-sw' });
    this.root.append(el('div', { class: 'ce-top' }, [this.native, this.hexIn, this.sw]));

    const mk = (name, min, max, step, unit) => {
      const out = el('output');
      const rng = el('input', { type: 'range', class: 'tracked', min, max, step, 'aria-label': `${label} ${name}` });
      const lab = el('label', { class: 'field' }, [el('span', {}, [el('span', { text: name }), out]), rng]);
      this.root.append(lab);
      return { rng, out, unit };
    };
    this.L = mk('Lightness', 0, 100, 0.5, '%');
    this.C = mk('Chroma', 0, 37, 0.25, '');
    this.H = mk('Hue', 0, 360, 1, '°');

    this.note = el('p', { class: 'ce-note' });
    this.root.append(this.note);
    mount.append(this.root);

    this.native.addEventListener('input', () => this.setHex(this.native.value.replace('#', ''), true));
    this.hexIn.addEventListener('input', () => {
      const v = this.hexIn.value.replace('#', '').trim();
      if (isHex6(v)) this.setHex(v, true, true);
    });
    this.hexIn.addEventListener('blur', () => { this.hexIn.value = this.hex; });
    for (const s of [this.L, this.C, this.H]) {
      s.rng.addEventListener('input', () => this.fromSliders());
    }
  }

  setEnabled(on) { this.root.dataset.disabled = on ? '0' : '1'; }

  setHex(hex, emit, keepHexField) {
    if (!isHex6(hex)) return;
    this.note.textContent = '';
    this.hex = hex.toLowerCase();
    const lch = hexToOklch(this.hex);
    if (lch[1] > 0.002) this.hue = lch[2];
    this.L.rng.value = (lch[0] * 100).toFixed(1);
    this.C.rng.value = (lch[1] * 100).toFixed(2);
    this.H.rng.value = this.hue.toFixed(0);
    this.render(keepHexField);
    if (emit && !this.silent) this.onChange(this.hex);
  }

  fromSliders() {
    const L = Number(this.L.rng.value) / 100;
    const C = Number(this.C.rng.value) / 100;
    const H = Number(this.H.rng.value);
    this.hue = H;
    const rgb = oklchToRgb([L, C, H]);
    const raw = oklabToRgbRaw(oklchToOklab([L, C, H]));
    this.hex = rgbToHex(rgb);
    this.note.textContent = inGamut(raw) ? '' : 'outside sRGB — chroma reduced to fit';
    this.render();
    if (!this.silent) this.onChange(this.hex);
  }

  render(keepHexField) {
    const css = '#' + this.hex;
    this.native.value = css;
    if (!keepHexField) this.hexIn.value = this.hex;
    this.sw.style.background = css;
    const L = Number(this.L.rng.value) / 100, C = Number(this.C.rng.value) / 100, H = this.hue;
    const track = (fn) => {
      const parts = [];
      for (let i = 0; i <= 12; i++) parts.push('#' + oklchToHex(fn(i / 12)));
      return `linear-gradient(90deg,${parts.join(',')})`;
    };
    this.L.rng.style.setProperty('--track', track((t) => [t, C, H]));
    this.C.rng.style.setProperty('--track', track((t) => [L, t * 0.37, H]));
    this.H.rng.style.setProperty('--track', track((t) => [L, Math.max(C, 0.08), t * 360]));
    this.L.out.textContent = Number(this.L.rng.value).toFixed(1) + '%';
    this.C.out.textContent = Number(this.C.rng.value).toFixed(2);
    this.H.out.textContent = Math.round(H) + '°';
  }

  /** Set the displayed colour without firing onChange (used when selection changes). */
  show(hex) { this.silent = true; this.setHex(isHex6(hex) ? hex : '000000', false); this.silent = false; }
}

let ceLed = null, ceUnder = null, ceStop = null;

/* ===================================================== 13. panels: colour */

function selectLed(zone, index, pos) {
  state.sel = { zone, index, pos };
  renderColorPanel();
  if (state.lastFrame) paint(state.lastFrame);
}

/** The shared-keycap warning for a selected key, or '' when it has its own cap. */
function sharedCapNote(index) {
  const k = state.geom?.keys?.[index];
  if (!k || !k.pos) return '';
  const mates = capMateIndices(k.pos.join(','));
  if (!mates.length) return '';
  const list = mates.join(' and ');
  return `One keycap, two switches: this LED and index ${list} sit under the single wide cap `
    + 'in the bottom row. Colouring them separately is the point — a two-pixel gradient across one '
    + 'cap. Binding them to different actions is not reliable, because nobody can choose which half '
    + 'of the cap they press: give the pair the same binding and treat it as one control.';
}

/** The same warning, phrased for the Bindings panel where it actually bites. */
function sharedCapBindNote(index) {
  const k = state.geom?.keys?.[index];
  if (!k || !k.pos) return '';
  const mates = capMateIndices(k.pos.join(','));
  if (!mates.length) return '';
  return `One physical keycap covers this switch and index ${mates.join(' and ')}. Whichever half `
    + 'of the wide cap a finger lands on is chance, so binding the two to different actions makes '
    + 'the cap do a different thing each press. Bind both the same — “Copy to index '
    + `${mates.join('/')}” below does that — or leave the other half unbound.`;
}

function renderColorPanel() {
  const sel = state.sel;
  const nameEl = $('sel-name'), hintEl = $('sel-hint'), shEl = $('sel-shared');
  const keyBox = $('key-extra'), stBox = $('status-led-box');
  $('light-scope').textContent = scopePath('lighting');
  shEl.hidden = true;
  shEl.textContent = '';

  if (!sel) {
    nameEl.textContent = 'none';
    hintEl.textContent = 'Click an LED in the device view, or tab into it and press Enter.';
    ceLed.show('000000');
    ceLed.setEnabled(false);
    keyBox.hidden = true; stBox.hidden = true;
    return;
  }
  if (sel.zone === 'keys') {
    const entry = keyEntry(sel.index);
    const strip = state.geom?.keys?.[sel.index]?.strip;
    const label = keyLabelOf(sel.index);
    nameEl.textContent = `key · index ${sel.index}`;
    const inherited = state.scope && !isHex6(entry?.color) && keyColorOf(sel.index);
    hintEl.textContent = (label ? `“${label}” — ` : '')
      + `writes ${scopePath(`keys[index ${sel.index}].color`)}`
      + (state.scope ? ' — an override that applies only while that mode is active' : '')
      + (inherited ? `, currently inherited from the profile (#${inherited}); changing it here creates the override` : '')
      + (strip === null || strip === undefined ? '' : ` · lit by per-key strip index ${strip}`);
    ceLed.setEnabled(true);
    ceLed.show(isHex6(entry?.color) ? entry.color : (inherited || '000000'));
    keyBox.hidden = false; stBox.hidden = true;
    const note = sharedCapNote(sel.index);
    if (note) { shEl.textContent = note; shEl.hidden = false; }
  } else if (sel.zone === 'underglow') {
    const strip = state.geom?.ug?.[sel.index]?.strip;
    nameEl.textContent = `underglow · ring position ${sel.index}`;
    hintEl.textContent = 'The config stores one shared underglow base colour — edit it under “Base layer” below. Per-LED underglow colour comes from an effect.'
      + (strip === null || strip === undefined ? '' : ` This position is lit by underglow strip index ${strip}.`);
    ceLed.setEnabled(false);
    ceLed.show(isHex6(effectiveLighting().underglow) ? effectiveLighting().underglow : '000000');
    keyBox.hidden = true; stBox.hidden = true;
  } else {
    nameEl.textContent = `status LED ${sel.index}`;
    hintEl.textContent = 'Single-colour LED immediately left of the touch pad, at the key block’s bottom-left: 8-bit PWM duty, no hue.';
    ceLed.setEnabled(false);
    keyBox.hidden = true; stBox.hidden = false;
  }
  renderStatusSliders();
}

function renderStatusSliders() {
  const box = $('status-sliders');
  if (box.childElementCount === STATUS_COUNT) { syncStatusSliders(); return; }
  box.textContent = '';
  for (let i = 0; i < STATUS_COUNT; i++) {
    const out = el('output');
    const rng = el('input', { type: 'range', min: '0', max: '255', step: '1', 'aria-label': `status LED ${i} duty` });
    rng.addEventListener('input', () => {
      const light = ensureLighting();
      if (!light) return;
      if (!Array.isArray(light.status_leds)) light.status_leds = [0, 0, 0];
      while (light.status_leds.length < STATUS_COUNT) light.status_leds.push(0);
      light.status_leds[i] = Number(rng.value);
      out.textContent = rng.value;
      touch('frame');
    });
    box.append(el('label', { class: 'field' }, [el('span', {}, [el('span', { text: `LED ${i}` }), out]), rng]));
  }
  syncStatusSliders();
}

function syncStatusSliders() {
  const duty = effectiveLighting().status_leds || [];
  $('status-sliders').querySelectorAll('input[type=range]').forEach((rng, i) => {
    rng.value = String(clamp(Math.round(Number(duty[i]) || 0), 0, 255));
    const out = rng.previousElementSibling?.querySelector('output');
    if (out) out.textContent = rng.value;
  });
}

/* =================================================== 14. panels: palettes */

function paletteOptions(selectEl, includeNone) {
  const cur = selectEl.value;
  selectEl.textContent = '';
  if (includeNone) selectEl.append(el('option', { value: '', text: '(none)' }));
  const mine = state.config?.palettes || {};
  if (Object.keys(mine).length) {
    const g = el('optgroup', { label: 'In this config' });
    for (const [k, v] of Object.entries(mine)) g.append(el('option', { value: k, text: `${v?.label || k} — ${k}` }));
    selectEl.append(g);
  }
  const g2 = el('optgroup', { label: 'Built-in' });
  for (const [k, v] of Object.entries(state.builtins)) {
    if (mine[k]) continue;
    g2.append(el('option', { value: k, text: `${v?.label || k} — ${k}` }));
  }
  selectEl.append(g2);
  if (cur && [...selectEl.options].some((o) => o.value === cur)) selectEl.value = cur;
}

function isOwnPalette(name) { return !!(state.config?.palettes && state.config.palettes[name]); }
function selectedPalette() {
  const n = state.palSel;
  return n ? allPalettes()[n] || null : null;
}

function renderPalettePanel() {
  const sel = $('sel-palette');
  paletteOptions(sel, false);
  const names = [...sel.options].map((o) => o.value).filter(Boolean);
  if (!state.palSel || !names.includes(state.palSel)) state.palSel = names[0] || null;
  if (state.palSel) sel.value = state.palSel;

  const pal = selectedPalette();
  const own = state.palSel ? isOwnPalette(state.palSel) : false;
  $('pal-origin').textContent = !pal
    ? 'No palettes available.'
    : own
      ? 'Stored in this config under palettes — editable. Saving overrides any built-in of the same name.'
      : 'Built-in corpus — read only. Duplicate makes an editable copy in this config, which also makes an exported config self-contained.';

  $('pal-name').value = state.palSel || '';
  $('pal-name').disabled = !own;
  $('pal-cyclic').checked = !!pal?.cyclic;
  $('pal-cyclic').disabled = !own;
  for (const id of ['btn-pal-del', 'btn-stop-add', 'btn-stop-del', 'btn-stop-even']) $(id).disabled = !own;

  $('gradfill').style.setProperty('--grad', state.palSel ? paletteCss(state.palSel, 48) : 'none');
  $('gradbar').style.cursor = own ? 'copy' : 'default';

  renderHandles();
  renderStopEditor();
}

function renderHandles() {
  const wrap = $('pal-handles');
  wrap.textContent = '';
  const pal = selectedPalette();
  if (!pal) return;
  const own = isOwnPalette(state.palSel);
  pal.stops.forEach((s, i) => {
    const h = el('button', {
      type: 'button', class: 'phandle', 'data-i': String(i),
      'aria-label': `stop ${i + 1} at ${Number(s.pos).toFixed(3)}, colour ${s.color}`,
      title: `${Number(s.pos).toFixed(3)} · #${s.color}`,
    });
    h.style.left = `${clamp(Number(s.pos) || 0, 0, 1) * 100}%`;
    h.style.background = '#' + (isHex6(s.color) ? s.color : '000000');
    if (i === state.palStop) h.dataset.active = '1';
    h.addEventListener('pointerdown', (ev) => {
      state.palStop = i; renderStopEditor(); markActiveHandle();
      if (!own) return;
      ev.preventDefault();
      h.setPointerCapture(ev.pointerId);
      const bar = $('gradbar').getBoundingClientRect();
      const move = (e) => {
        const t = clamp((e.clientX - bar.left) / bar.width, 0, 1);
        pal.stops[i].pos = Math.round(t * 1000) / 1000;
        h.style.left = `${t * 100}%`;
        paletteRev++;
        $('gradfill').style.setProperty('--grad', paletteCss(state.palSel, 48));
        $('rng-stoppos').value = String(pal.stops[i].pos);
        $('out-stoppos').textContent = pal.stops[i].pos.toFixed(3);
        touch('effect');
      };
      const up = () => { h.removeEventListener('pointermove', move); h.removeEventListener('pointerup', up); renderPalettePanel(); };
      h.addEventListener('pointermove', move);
      h.addEventListener('pointerup', up);
    });
    h.addEventListener('keydown', (ev) => {
      if (!own) return;
      const step = ev.shiftKey ? 0.05 : 0.01;
      let d = 0;
      if (ev.key === 'ArrowLeft') d = -step; else if (ev.key === 'ArrowRight') d = step; else return;
      ev.preventDefault();
      pal.stops[i].pos = clamp(Math.round((Number(pal.stops[i].pos) + d) * 1000) / 1000, 0, 1);
      state.palStop = i;
      paletteRev++; touch('effect'); renderPalettePanel();
      $('pal-handles').querySelector(`[data-i="${i}"]`)?.focus();
    });
    h.addEventListener('focus', () => { state.palStop = i; renderStopEditor(); markActiveHandle(); });
    wrap.append(h);
  });
}

function markActiveHandle() {
  $('pal-handles').querySelectorAll('.phandle').forEach((h, i) => {
    if (i === state.palStop) h.dataset.active = '1'; else delete h.dataset.active;
  });
}

function renderStopEditor() {
  const pal = selectedPalette();
  const own = state.palSel ? isOwnPalette(state.palSel) : false;
  if (!pal || !pal.stops.length) {
    $('stop-name').textContent = '—';
    ceStop.setEnabled(false);
    $('rng-stoppos').disabled = true;
    return;
  }
  state.palStop = clamp(state.palStop, 0, pal.stops.length - 1);
  const s = pal.stops[state.palStop];
  $('stop-name').textContent = `${state.palStop + 1} of ${pal.stops.length}`;
  $('rng-stoppos').value = String(clamp(Number(s.pos) || 0, 0, 1));
  $('rng-stoppos').disabled = !own;
  $('out-stoppos').textContent = (Number(s.pos) || 0).toFixed(3);
  ceStop.setEnabled(own);
  ceStop.show(s.color);
}

function mutatePalette(fn) {
  if (!state.palSel || !isOwnPalette(state.palSel)) { toast('Built-in palettes are read only — duplicate it first', 'warn'); return; }
  fn(state.config.palettes[state.palSel]);
  paletteRev++;
  touch('effect');
  renderPalettePanel();
}

function newPaletteName(base) {
  const taken = new Set(Object.keys(allPalettes()));
  let n = base, i = 2;
  while (taken.has(n)) n = `${base}-${i++}`;
  return n;
}

/* ==================================================== 15. panels: effect */

function fillSelect(selectEl, values, labels) {
  selectEl.textContent = '';
  values.forEach((v, i) => selectEl.append(el('option', { value: v, text: (labels && labels[i]) || v })));
}

function renderEffectPanel() {
  const eff = currentEffect();
  $('eff-scope').textContent = scopePath('lighting.effect');
  paletteOptions($('eff-palette'), true);

  const on = !!eff;
  for (const id of ['eff-palette', 'eff-speed', 'eff-intensity', 'eff-direction', 'eff-target', 'eff-blend', 'eff-reverse', 'btn-eff-remove', 'btn-eff-send']) {
    $(id).disabled = !on;
  }
  const e = eff || {};
  $('eff-name').value = on && EFFECT_NAMES.includes(e.name) ? e.name : '';
  $('eff-palette').value = e.palette && [...$('eff-palette').options].some((o) => o.value === e.palette) ? e.palette : '';
  $('eff-speed').value = String(clamp(Number(e.speed ?? EFFECT_DEFAULTS.speed), 0, 10));
  $('eff-intensity').value = String(clamp(Number(e.intensity ?? EFFECT_DEFAULTS.intensity), 0, 1));
  $('eff-direction').value = DIRECTIONS.includes(e.direction) ? e.direction : EFFECT_DEFAULTS.direction;
  $('eff-target').value = TARGETS.includes(e.target) ? e.target : EFFECT_DEFAULTS.target;
  $('eff-blend').value = BLENDS.includes(e.blend) ? e.blend : EFFECT_DEFAULTS.blend;
  $('eff-reverse').checked = !!e.reverse;
  $('out-speed').textContent = Number($('eff-speed').value).toFixed(2) + ' cyc/s';
  $('out-intensity').textContent = Number($('eff-intensity').value).toFixed(2);

  const notes = [];
  if (state.scope) {
    notes.push(`Editing mode “${state.scope}”. A mode's lighting is merged over the profile's key by `
      + 'key, so what you leave unset here keeps coming from the profile — which is why the board '
      + 'may show an effect this panel calls absent.');
  }
  if (!eff) notes.push(state.scope
    ? 'This mode sets no effect of its own, so the profile\'s runs while it is active. Pick a name to override it.'
    : 'This profile has no effect — only the base colours render. Pick a name to add one.');
  else {
    if (e.palette && !allPalettes()[e.palette]) notes.push(`Palette “${e.palette}” is not in this config or the built-in corpus — the preview falls back to rainbow.`);
    if (!e.palette && !['rainbow', 'off'].includes(e.name)) notes.push('No palette set — the preview falls back to rainbow.');
    if (e.direction === 'ring' && e.target === 'keys') notes.push('Ring direction on the keys uses the angle around the pad centre.');
    if (e.direction === 'ring' && !mappingVerified()) notes.push('This config marks its underglow mapping unverified, so a ring chase may run in an unexpected order.');
  }
  $('eff-note').textContent = notes.join(' ');
}

function mutateEffect(fn) {
  const light = ensureLighting();
  if (!light) return;
  if (!light.effect) light.effect = { name: 'gradient' };
  fn(light.effect);
  if (!light.effect.name) light.effect.name = 'gradient';
  touch('effect');
  renderEffectPanel();
}

/* ================================================== 16. panels: identify */

/* The sweep starts from the mapping currently in force — config overrides where present, the
 * confirmed wiring order everywhere else — so the panel's job is confirming or correcting what
 * the pad already does, not filling in blanks. */
function seedIdentifyMap() {
  const g = state.geom || buildGeometry();
  state.identify.map = {
    keys: g.keys.slice(0, KEY_COUNT).map(() => null),
    underglow: g.ug.slice(0, UG_COUNT).map(() => null),
  };
  for (const k of g.keys) if (k.pos && k.strip !== null && k.strip < KEY_COUNT) state.identify.map.keys[k.strip] = k.pos.slice();
  for (const u of g.ug) if (u.strip !== null && u.strip < UG_COUNT) state.identify.map.underglow[u.strip] = u.pos.slice();
  while (state.identify.map.keys.length < KEY_COUNT) state.identify.map.keys.push(null);
  while (state.identify.map.underglow.length < UG_COUNT) state.identify.map.underglow.push(null);
}

const identifyCount = () => (state.identify.target === 'keys' ? KEY_COUNT : UG_COUNT);

function renderIdentifyPanel() {
  const id = state.identify;
  const count = identifyCount();
  $('id-current').textContent = id.active ? `${id.target} index ${id.index}` : 'idle';
  $('btn-id-start').textContent = id.active ? 'Stop sweep' : 'Start sweep';
  for (const b of ['btn-id-prev', 'btn-id-next', 'btn-id-skip']) $(b).disabled = !id.active;
  $('chk-verified').checked = mappingVerified();

  const body = $('id-table');
  body.textContent = '';
  const seen = new Map();
  const list = id.map[id.target];
  list.forEach((p, i) => { if (p) { const k = p.join(','); seen.set(k, (seen.get(k) || 0) + 1); } });

  for (let i = 0; i < count; i++) {
    const p = list[i];
    const key = p ? p.join(',') : null;
    const dupe = key && seen.get(key) > 1;
    const tdPos = el('td', { text: p ? (id.target === 'keys' ? `row ${p[0]}, col ${p[1]}` : `x ${p[0]}, y ${p[1]}`) : 'not recorded' });
    if (!p) tdPos.className = 'miss'; else if (dupe) tdPos.className = 'dupe';
    const clear = el('button', { type: 'button', class: 'ghost small', text: 'clear' });
    clear.addEventListener('click', () => { list[i] = null; renderIdentifyPanel(); });
    const jump = el('button', { type: 'button', class: 'ghost small', text: 'light' });
    jump.addEventListener('click', () => { id.active = true; id.index = i; runIdentify(); });
    const tr = el('tr', { 'data-current': id.active && id.index === i ? '1' : '0' }, [
      el('td', { text: String(i) }), tdPos, el('td', {}, [jump, clear]),
    ]);
    body.append(tr);
  }

  const missing = list.slice(0, count).filter((p) => !p).length;
  const dupes = [...seen.values()].filter((v) => v > 1).length;
  const def = id.target === 'keys' ? state.geom.defKeys : state.geom.defUg;
  const sameAsDefault = list.slice(0, count).every((p, i) => p && def[i] && p.join(',') === def[i].join(','));
  const bits = [];
  if (missing) bits.push(`${missing} of ${count} indices unrecorded`);
  if (dupes) bits.push(`${dupes} position(s) claimed by more than one index`);
  if (!missing && !dupes) {
    bits.push(sameAsDefault
      ? 'This table matches the confirmed default wiring order — writing it just pins it explicitly into the config.'
      : 'This table differs from the confirmed default wiring order — writing it overrides the default for this unit.');
  }
  if (!state.daemonReachable) bits.push('No daemon: the sweep cannot light the pad, but you can still edit the mapping by hand.');
  if (id.active && state.view.threeD) {
    bits.push('The board drops to the flat 2D view while a sweep runs, so the position you click is exactly where it looks.');
  }
  $('id-note').textContent = bits.join(' · ');
}

let identifyWarned = false;
function runIdentify() {
  renderIdentifyPanel();
  if (state.lastFrame) paint(state.lastFrame);
  api.identify(state.identify.target, state.identify.index).then((r) => {
    if (!r.ok && r.reachable) { toast('Identify failed: ' + (r.error || 'error'), 'err'); return; }
    // The daemon answers 200 with ok:false when the pad itself did not take the command.
    if (r.ok && r.data && r.data.ok === false && !identifyWarned) {
      identifyWarned = true;
      toast('The daemon could not light that LED — is the pad connected? You can still record positions by hand.', 'warn', 7000);
    }
  });
}

function identifyStep(delta) {
  const count = identifyCount();
  state.identify.index = (state.identify.index + delta + count) % count;
  runIdentify();
}

function recordIdentify(zone, posKey) {
  const id = state.identify;
  if (zone !== id.target) {
    toast(`Sweeping ${id.target} — click a ${id.target === 'keys' ? 'key' : 'underglow'} position`, 'warn');
    return;
  }
  const pos = posKey.split(',').map(Number);
  const list = id.map[zone];
  // one position can only host one strip index: steal it from whoever had it
  list.forEach((p, i) => { if (p && p.join(',') === posKey && i !== id.index) list[i] = null; });
  list[id.index] = pos;
  const count = identifyCount();
  if (id.index < count - 1) { id.index++; runIdentify(); }
  else { toast(`Recorded all ${count} ${zone} indices — review, then write to config`, 'ok'); renderIdentifyPanel(); }
}

function writeMapping() {
  const cfg = state.config;
  if (!cfg.layout || typeof cfg.layout !== 'object') cfg.layout = {};
  if (!Array.isArray(cfg.layout.key_rows)) cfg.layout.key_rows = keyRows().slice();

  const k = state.identify.map.keys.slice(0, KEY_COUNT).map((p) => (p ? [p[0], p[1]] : null));
  const u = state.identify.map.underglow.slice(0, UG_COUNT).map((p) => (p ? [p[0], p[1]] : null));
  // An all-null array says nothing the absent key doesn't, so don't write one.
  if (k.some(Boolean)) cfg.layout.key_positions = k; else delete cfg.layout.key_positions;
  if (u.some(Boolean)) cfg.layout.underglow_positions = u; else delete cfg.layout.underglow_positions;

  const missing = [];
  if (k.some((p) => !p)) missing.push('per-key');
  if (u.some((p) => !p)) missing.push('underglow');
  const wantVerified = $('chk-verified').checked;
  cfg.layout.verified = wantVerified && missing.length === 0;

  buildDevice();
  renderBanner();
  renderIdentifyPanel();
  renderStageNote();
  touch('frame');
  if (wantVerified && missing.length) {
    toast(`Mapping written, but ${missing.join(' and ')} indices are still unrecorded — layout.verified stays false`, 'warn', 6500);
  } else {
    toast(`Mapping written to layout as an override${cfg.layout.verified ? ' and marked verified' : ''} — remember to Save`, 'ok');
  }
}

/* ================================================== 16b. shortcut grammar */

/* A client-side mirror of keys.py's parse_shortcut, for two things the recorder needs and a
 * round trip to the daemon cannot give: normalising a chord the moment it is pressed, and
 * telling the user a typed spec is wrong before Save. The tables are the same tables; if they
 * drift, the daemon is right and this is wrong. */

const KEY_NAMES = new Set([
  ...'abcdefghijklmnopqrstuvwxyz',
  ...Array.from({ length: 10 }, (_, i) => String(i)),
  ...Array.from({ length: 20 }, (_, i) => 'f' + (i + 1)),
  'escape', 'tab', 'return', 'space', 'delete', 'forwarddelete',
  'home', 'end', 'pageup', 'pagedown', 'left', 'right', 'down', 'up',
  'help', 'capslock',
  'minus', 'equal', 'leftbracket', 'rightbracket', 'backslash',
  'semicolon', 'quote', 'comma', 'period', 'slash', 'grave',
  ...Array.from({ length: 10 }, (_, i) => 'kp' + i),
  'kpdecimal', 'kpplus', 'kpminus', 'kpmultiply', 'kpdivide', 'kpequals', 'kpenter', 'kpclear',
  'plus',
]);

const MOD_ALIASES = {
  cmd: 'cmd', command: 'cmd', '⌘': 'cmd', meta: 'cmd', super: 'cmd', win: 'cmd',
  ctrl: 'ctrl', control: 'ctrl', ctl: 'ctrl', '⌃': 'ctrl',
  opt: 'opt', option: 'opt', alt: 'opt', '⌥': 'opt',
  shift: 'shift', shft: 'shift', '⇧': 'shift',
  fn: 'fn', function: 'fn',
};

const KEY_ALIASES = {
  esc: 'escape', enter: 'return', ret: 'return', cr: 'return',
  spc: 'space', spacebar: 'space',
  backspace: 'delete', bksp: 'delete', bs: 'delete',
  fwddelete: 'forwarddelete', forward_delete: 'forwarddelete', fdel: 'forwarddelete',
  pgup: 'pageup', page_up: 'pageup', pgdn: 'pagedown', pagedn: 'pagedown', page_down: 'pagedown',
  uparrow: 'up', arrowup: 'up', downarrow: 'down', arrowdown: 'down',
  leftarrow: 'left', arrowleft: 'left', rightarrow: 'right', arrowright: 'right',
  caps: 'capslock', caps_lock: 'capslock',
  '-': 'minus', dash: 'minus', hyphen: 'minus',
  '=': 'equal', equals: 'equal',
  '[': 'leftbracket', lbracket: 'leftbracket', left_bracket: 'leftbracket',
  ']': 'rightbracket', rbracket: 'rightbracket', right_bracket: 'rightbracket',
  '\\': 'backslash', ';': 'semicolon', "'": 'quote', apostrophe: 'quote',
  ',': 'comma', '.': 'period', '/': 'slash',
  '`': 'grave', backtick: 'grave', backquote: 'grave', tilde: 'grave',
  num_enter: 'kpenter', clear: 'kpclear', '+': 'plus',
};

const canonicalKeyName = (raw) => (KEY_NAMES.has(raw) ? raw : (KEY_ALIASES[raw] && KEY_NAMES.has(KEY_ALIASES[raw]) ? KEY_ALIASES[raw] : null));

/** Split a chord on '+', keeping a literal plus addressable — keys.py's `_components` rules. */
function chordComponents(spec) {
  if (!spec) throw new Error('empty shortcut');
  if (spec === '+') return ['+'];
  if (spec.endsWith('++')) {
    const mods = spec.slice(0, -1).split('+').filter(Boolean);
    if (!mods.length) throw new Error(`“${spec}” has no key`);
    return [...mods, '+'];
  }
  if (spec.endsWith('+')) throw new Error(`“${spec}” has no key after the last “+” (for a literal plus write “${spec}+” or “${spec}plus”)`);
  const parts = spec.split('+');
  if (parts.some((p) => !p)) throw new Error(`“${spec}” has an empty component (write a literal plus last, as in “cmd++”, or use “plus”)`);
  return parts;
}

/** Normalise a spec the way the daemon will. Returns {ok, spec} or {ok:false, error}. */
function parseSpec(raw) {
  const text = String(raw || '').trim().toLowerCase();
  if (!text) return { ok: false, error: 'empty' };
  let comps;
  try { comps = chordComponents(text); } catch (e) { return { ok: false, error: e.message }; }
  const rawKey = comps[comps.length - 1];
  const mods = new Set();
  for (const m of comps.slice(0, -1)) {
    const mod = MOD_ALIASES[m];
    if (!mod) {
      return { ok: false, error: canonicalKeyName(m)
        ? `“${m}” is a key, not a modifier — a shortcut has exactly one key, written last`
        : `unknown modifier “${m}” (one of: ${MODIFIERS.join(', ')})` };
    }
    mods.add(mod);
  }
  const key = canonicalKeyName(rawKey);
  if (!key) {
    return { ok: false, error: MOD_ALIASES[rawKey]
      ? `“${rawKey}” is a modifier — a shortcut needs a key too`
      : `unknown key “${rawKey}”` };
  }
  return { ok: true, spec: [...MODIFIERS.filter((m) => mods.has(m)), key].join('+') };
}

/** A KeyboardEvent as a spec, or why it can't be one. */
function specFromEvent(ev) {
  const mods = [];
  if (ev.ctrlKey) mods.push('ctrl');
  if (ev.altKey) mods.push('opt');
  if (ev.shiftKey) mods.push('shift');
  if (ev.metaKey) mods.push('cmd');
  const ordered = MODIFIERS.filter((m) => mods.includes(m));
  if (MODIFIER_CODES[ev.code]) return { pending: true, mods: ordered };
  // `code` not `key`: with modifiers held, `key` is the layout's shifted/opted legend (opt+a is
  // "å") while the helper wants the physical key.
  const key = CODE_TO_KEY[ev.code];
  if (!key) return { error: `this browser calls that key “${ev.code}”, which has no name in the spec grammar` };
  return { spec: [...ordered, key].join('+'), mods: ordered, key };
}

/* ===================================================== 16c. panels: bindings */

const CONTROL_META = {
  key: { label: 'Key', triggers: KEY_TRIGGERS },
  encoder: { label: 'Encoder', triggers: ENC_TRIGGERS },
  touch: { label: 'Touch pad', triggers: KEY_TRIGGERS },
  rear: { label: 'Rear button', triggers: KEY_TRIGGERS },
  joystick: { label: 'Joystick', triggers: KEY_TRIGGERS },
};
const controlTriggers = (c) => (CONTROL_META[c] || CONTROL_META.key).triggers;
const controlName = (c, i) => {
  if (c === 'key') return `key ${i}`;
  // A joystick direction is the control here, not the stick: each of the eight binds on its own.
  if (c === 'joystick') return `joystick ${JOY_LABEL[JOY_DIRS[i]] || '?'}`;
  return (CONTROL_META[c] || {}).label || c;
};

/** The `triggers` object for a control in the scope being edited, created on demand.
 *
 *  Where it lives follows dispatch.py exactly: a key's bindings, the encoder's and each joystick
 *  direction's belong to the layer (mode override, else profile), while touch and rear are
 *  profile-level only because modes don't override them. */
function triggersFor(control, index, create = false) {
  if (control === 'key') {
    const e = keyEntry(index, create);
    if (!e) return null;
    if (!e.on || typeof e.on !== 'object') { if (!create) return null; e.on = {}; }
    return e.on;
  }
  if (control === 'joystick') {
    // `joystick` is an object of eight named directions, each one a full `triggers` object —
    // dispatch.py resolves a mode's joystick first and falls back to the profile's, direction by
    // direction, exactly as it does for keys, so this belongs to the layer being edited.
    const owner = scopeOwner();
    const name = JOY_DIRS[index];
    if (!owner || !name) return null;
    if (!owner.joystick || typeof owner.joystick !== 'object') { if (!create) return null; owner.joystick = {}; }
    const j = owner.joystick;
    if (!j[name] || typeof j[name] !== 'object') { if (!create) return null; j[name] = {}; }
    return j[name];
  }
  const owner = control === 'encoder' ? scopeOwner() : currentProfile();
  if (!owner) return null;
  if (!owner[control] || typeof owner[control] !== 'object') { if (!create) return null; owner[control] = {}; }
  return owner[control];
}

function bindingAt(control, index, kind) {
  const t = triggersFor(control, index);
  return t && t[kind] && typeof t[kind] === 'object' ? t[kind] : null;
}

const boundKinds = (control, index) => {
  const t = triggersFor(control, index) || {};
  return controlTriggers(control).filter((k) => t[k]);
};

/** Which action key a binding carries. The schema's oneOf means there is at most one. */
const actionKeyOf = (b) => (b ? BINDING_KEYS.find((k) => b[k] !== undefined) || null : null);

/** Drop empty containers rather than leaving `"on": {}` behind. A mode is the exception: the
 *  schema *requires* its `encoder`, so an empty one there is the valid way to say "no rotation". */
function cleanupTriggers(control, index) {
  if (control === 'key') { pruneKeyEntry(index); return; }
  if (control === 'joystick') {
    // Two levels to prune: the direction, then `joystick` itself once its last direction goes.
    const o = scopeOwner();
    const name = JOY_DIRS[index];
    if (!o || !name || !o.joystick) return;
    if (o.joystick[name] && !Object.keys(o.joystick[name]).length) delete o.joystick[name];
    if (!Object.keys(o.joystick).length) delete o.joystick;
    return;
  }
  const owner = control === 'encoder' ? scopeOwner() : currentProfile();
  if (!owner || !owner[control]) return;
  if (Object.keys(owner[control]).length) return;
  if (control === 'encoder' && state.scope) return;
  delete owner[control];
}

function removeBinding(control, index, kind) {
  const t = triggersFor(control, index);
  if (!t) return;
  delete t[kind];
  cleanupTriggers(control, index);
}

function defaultBindingValue(type, carried) {
  const str = typeof carried === 'string' ? carried : '';
  if (type === 'action') return ACTION_ENUM.includes(str) ? str : 'play_pause';
  if (type === 'mode') {
    const names = Object.keys(profileModes());
    return names.includes(str) ? str : (names[0] || '');
  }
  if (type === 'profile') {
    const names = Object.keys(state.config?.profiles || {});
    return names.includes(str) || str === 'next' || str === 'prev' ? str : 'next';
  }
  return str;
}

/** Set (or clear) the single action key of a binding, keeping any flash colour. */
function setBindingType(control, index, kind, type) {
  if (!type) { removeBinding(control, index, kind); return; }
  const t = triggersFor(control, index, true);
  if (!t) return;
  const prev = t[kind] && typeof t[kind] === 'object' ? t[kind] : {};
  const prevKey = actionKeyOf(prev);
  const prevInput = prevKey ? bindingType(prevKey)?.input : null;
  const nextInput = bindingType(type)?.input;
  // Carry the text over only between the free-text kinds — shell to script is a rename, shell to
  // "activate mode" is not.
  const textish = ['line', 'multiline', 'shortcut'];
  const carried = prevKey && textish.includes(prevInput) && textish.includes(nextInput) ? prev[prevKey] : '';
  const next = { [type]: defaultBindingValue(type, carried) };
  if (isHex6(prev.flash)) next.flash = prev.flash;
  t[kind] = next;
}

/* ---------------------------------------------------------------- rendering */

function renderBindingsPanel() {
  $('bind-scope').textContent = scopePath();
  renderCapsBox();
  renderControlPicker();

  const { control, index } = state.bind;
  const isKey = control === 'key';
  $('bind-label-field').hidden = !isKey;      // only `key` has a label in the schema
  $('btn-bind-clear').textContent = `Clear all ${controlName(control, index).toLowerCase()} bindings`;
  if (isKey && document.activeElement !== $('bind-label')) $('bind-label').value = keyEntry(index)?.label || '';

  const shEl = $('bind-shared');
  shEl.textContent = '';
  const note = isKey ? sharedCapBindNote(index) : '';
  shEl.hidden = !note;
  if (note) {
    shEl.append(note + ' ');
    const mates = capMateIndices(state.geom?.keys?.[index]?.pos?.join(',') || '');
    const btn = el('button', { type: 'button', class: 'ghost small', text: `Copy to index ${mates.join(' and ')}` });
    btn.addEventListener('click', () => {
      const src = triggersFor('key', index);
      if (!src || !Object.keys(src).length) { toast('Nothing bound on this half to copy', 'warn'); return; }
      for (const m of mates) {
        const dst = keyEntry(m, true);
        dst.on = clone(src);
      }
      touch(null);
      renderBindingsPanel();
      toast(`Both halves of the wide cap now do the same thing`, 'ok');
    });
    shEl.append(btn);
  }

  renderTriggerCards();
  renderTimingBox();
}

/* --------------------------------------------------------- the control picker
 *
 * Which control is being edited is chosen ON A MAP OF THE PAD, laid out as the real 4x4 grid, not
 * as a wrapped list of chips. Every position comes out of buildGeometry() — the same `caps`,
 * `cells` and `featureCells` the device view is drawn from, translated to CSS grid rows and
 * columns — so the picker and the board cannot drift apart: there is one source of physical
 * placement in this file and both views read it.
 *
 * It is deliberately NOT a second copy of the device view. No LED colour, no underglow, no live
 * preview, no status LEDs (those carry no bindings). What it does carry is binding density: a
 * control with nothing bound is drawn quieter than one with several, so where a config is thick
 * is visible without clicking through 16 controls.
 *
 * Two bindable things are not on the front grid and are not pretended into it:
 *   - the REAR BUTTON, which is physically on the back — its own strip below the grid, labelled;
 *   - the JOYSTICK's eight directions, which are one slot on the grid and a compass beneath it.
 */

/** Picker-sized glyph for a non-key control, drawn by the board's OWN featureGlyph() on a
 *  synthetic cell — so the marks here are literally the same drawing code as the device view's. */
function pickerGlyph(kind) {
  const svg = svgEl('svg', {
    class: 'ctlglyph', viewBox: '0 0 36 34', width: 28, height: 26,
    'aria-hidden': 'true', focusable: 'false',
  });
  // featureGlyph() reserves the bottom of its cell for a caption and centres the shape 6px above
  // the cell's middle; `label: ''` leaves the caption empty, since the button has real text.
  svg.append(featureGlyph({ kind, label: '', x: 0, y: 0, w: 36, h: 34, cx: 18, cy: 23 }));
  return svg;
}

/** The rear button, seen from the front: the body's outline dashed because it is on the far side. */
function rearGlyph() {
  const svg = svgEl('svg', {
    class: 'ctlglyph', viewBox: '0 0 36 34', width: 28, height: 26,
    'aria-hidden': 'true', focusable: 'false',
  });
  const g = svgEl('g', { class: 'feat feat-rear' });
  g.append(svgEl('rect', { class: 'feat-shape feat-dash', x: 6, y: 6, width: 24, height: 21, rx: 5 }));
  g.append(svgEl('circle', { class: 'feat-knob', cx: 18, cy: 16.5, r: 4.4 }));
  svg.append(g);
  return svg;
}

/** Total bindings across all eight joystick directions — what the stick's own cell counts. */
const joyTotalBindings = () => JOY_DIRS.reduce((s, _d, i) => s + boundKinds('joystick', i).length, 0);

/** Which direction selecting the stick lands on: the one being edited, else the first that has
 *  something bound, else north. */
function joyDefaultDir() {
  if (state.bind.control === 'joystick') return state.bind.index;
  const bound = JOY_DIRS.findIndex((_d, i) => boundKinds('joystick', i).length > 0);
  return bound === -1 ? JOY_DIRS.indexOf('n') : bound;
}

/** Set the single tab stop of a roving group. */
function rove(list, target) {
  for (const e of list) e.setAttribute('tabindex', e === target ? '0' : '-1');
}

function renderControlPicker() {
  const wrap = $('bind-controls');
  // Remember WHICH cell had focus, not merely that something did: selecting re-renders the picker,
  // and focus has to come back to the cell the user is standing on rather than jumping to the
  // selection. `data-fk` identifies a cell across a rebuild.
  const act = document.activeElement;
  const focusKey = wrap.contains(act) ? act.dataset?.fk || null : null;
  wrap.textContent = '';

  const g = state.geom || buildGeometry();

  /* How loudly to draw a control. A key with nothing bound must read quieter than one with three,
   * which is the whole reason the counts are on the map instead of in a table. */
  const density = (n) => (n === 0 ? '0' : n < 3 ? '1' : '2');

  const cell = (control, index, o) => {
    const n = o.count === undefined ? boundKinds(control, index).length : o.count;
    const b = el('button', {
      type: 'button', class: 'ctlcell' + (o.cls ? ' ' + o.cls : ''), tabindex: '-1',
      'aria-pressed': state.bind.control === control && state.bind.index === index ? 'true' : 'false',
      'data-control': control, 'data-index': String(index), 'data-fk': `map:${control}:${index}`,
      'data-dens': density(n), 'data-nr': String(o.nr), 'data-nc': String(o.nc),
      title: `${o.title} · ${n} binding${n === 1 ? '' : 's'}`,
      'aria-label': `${o.aria || o.title}, ${n} binding${n === 1 ? '' : 's'}`,
    }, [
      o.glyph || null,
      el('span', { class: 'cc-t', text: o.top }),
      o.sub ? el('span', { class: 'cc-s', text: o.sub }) : null,
      el('span', { class: 'cc-n', text: n ? String(n) : '', 'aria-hidden': 'true' }),
    ]);
    b.addEventListener('click', () => selectControl(control, index));
    return b;
  };

  const map = el('div', { class: 'ctlmap' });
  map.style.gridTemplateColumns = `repeat(${g.nCols}, minmax(0, 1fr))`;
  const place = (node, row, gcol, span = 1) => {
    node.style.gridRow = String(row + 1);
    node.style.gridColumn = `${gcol + 1} / span ${span}`;
    map.append(node);
  };

  /* Keycaps, straight off geom.caps: 13 switches under 12 caps, so the shared pair is ONE cap box
   * with two half-buttons in it — the same fact the board draws, at picker size. */
  for (const cap of g.caps) {
    const cols = cap.cells.map((c) => c.gcol);
    const gc0 = Math.min(...cols);
    const box = el('div', { class: 'ctlcap' + (cap.shared ? ' shared' : '') });
    for (const c of cap.cells.slice().sort((a, b) => a.gcol - b.gcol)) {
      const i = indexAtPos('keys', `${c.row},${c.col}`);
      if (i === null) continue;
      const label = keyLabelOf(i);
      const mates = capMateIndices(`${c.row},${c.col}`);
      box.append(cell('key', i, {
        nr: c.row, nc: c.gcol, cls: 'key' + (cap.shared ? ' half' : ''),
        top: String(i), sub: label,
        title: `key ${i}${label ? ' — ' + label : ''}`
          + (mates.length ? ` · one wide keycap shared with index ${mates.join(' and ')}` : ''),
        aria: `key ${i}${label ? `, ${label}` : ''}`
          + (mates.length ? `, one wide keycap shared with index ${mates.join(' and ')}` : ''),
      }));
    }
    place(box, cap.cells[0].row, gc0, Math.max(...cols) - gc0 + 1);
  }

  /* One cell per non-key control, keyed by kind so the same description serves whether the
   * geometry gave it a grid slot or not. All three are bindable; none of them is an LED, which is
   * why they are ghosts on the board and ordinary cells here. */
  const featCell = (kind, label, nr, nc) => {
    if (kind === 'joystick') {
      const dir = joyDefaultDir();
      return cell('joystick', dir, {
        nr, nc, cls: 'featcell joy', glyph: pickerGlyph(kind),
        top: label, count: joyTotalBindings(),
        sub: state.bind.control === 'joystick' ? JOY_LABEL[JOY_DIRS[dir]] : '8 directions',
        title: 'joystick — eight directions, each bound on its own',
        aria: 'joystick, eight directions each bound on its own',
      });
    }
    return cell(kind, 0, {
      nr, nc, cls: 'featcell', glyph: pickerGlyph(kind),
      top: label, sub: kind === 'encoder' ? 'cw / ccw / press' : 'profile-level',
      title: kind === 'encoder' ? 'encoder — rotation and its button' : 'capacitive touch pad',
    });
  };
  for (const f of g.featureCells) place(featCell(f.kind, f.label, f.row, f.gcol), f.row, f.gcol);
  wrap.append(map);

  /* The rear button is on the BACK of the device. There is no slot for it on the front grid and
   * dropping it into a spare one would be a lie about the hardware, so it lives just outside the
   * grid, on the other side of a dashed line, saying where it is. */
  const off = el('div', { class: 'ctloff' });
  let offCol = 0;
  off.append(cell('rear', 0, {
    nr: g.nRows, nc: offCol++, cls: 'featcell rear', glyph: rearGlyph(),
    top: 'rear button', sub: 'profile-level',
    title: 'rear button — on the back of the device, not on the front grid',
    aria: 'rear button, on the back of the device, off the front grid',
  }));
  off.append(el('span', { class: 'ctloff-t', text: 'on the back — no position on the front grid' }));

  /* A `layout.key_rows` the faceplate doesn't have can leave a non-key control with no slot — its
   * grid position is a key's in that layout, so buildGeometry() drops the ghost and the board
   * doesn't draw it. It is still bindable, so it joins the off-grid strip rather than becoming
   * unreachable: every bindable control must be selectable from here. */
  const drawn = new Set(g.featureCells.map((f) => f.kind));
  const missing = FEATURES.filter((f) => !drawn.has(f.kind));
  for (const f of missing) off.append(featCell(f.kind, f.label, g.nRows, offCol++));
  if (missing.length) {
    off.append(el('span', { class: 'ctloff-t', text:
      `layout.key_rows leaves no grid slot for the ${missing.map((f) => f.label).join(' or ')} in `
      + 'this layout, so it is listed here instead' }));
  }
  wrap.append(off);

  // The stick's eight sectors, as a compass: a direction IS a position, so picking one is the same
  // kind of act as picking a key. Only shown while the joystick is the control being edited.
  if (state.bind.control === 'joystick') wrap.append(joystickCompass());

  /* Roving tabindex: the whole map is ONE tab stop and arrow keys walk it, where the old flat list
   * was sixteen stops. The compass is a second stop while it is open. */
  const cells = [...wrap.querySelectorAll('[data-nr]')];
  rove(cells, cells.find((c) => c.getAttribute('aria-pressed') === 'true') || cells[0]);
  if (focusKey) {
    const back = [...wrap.querySelectorAll('[data-fk]')].find((c) => c.dataset.fk === focusKey);
    const home = back || wrap.querySelector('[aria-pressed="true"]');
    if (home) {
      rove(home.hasAttribute('data-jr') ? [...wrap.querySelectorAll('[data-jr]')] : cells, home);
      home.focus();
    }
  }
}

/** The joystick's eight directions, laid out as the compass they are. */
function joystickCompass() {
  const box = el('div', { class: 'ctljoy', role: 'group', 'aria-label': 'Joystick direction' });
  const grid = el('div', { class: 'ctljoy-grid' });
  for (const [name, [r, c]] of Object.entries(JOY_CELL)) {
    const i = JOY_DIRS.indexOf(name);
    const n = boundKinds('joystick', i).length;
    const on = state.bind.control === 'joystick' && state.bind.index === i;
    const b = el('button', {
      type: 'button', class: 'ctldir', tabindex: on ? '0' : '-1',
      'aria-pressed': on ? 'true' : 'false',
      'data-control': 'joystick', 'data-index': String(i), 'data-fk': `dir:joystick:${i}`,
      'data-dens': n === 0 ? '0' : n < 3 ? '1' : '2',
      'data-jr': String(r), 'data-jc': String(c),
      title: `joystick ${JOY_LABEL[name]} · ${n} binding${n === 1 ? '' : 's'}`,
      'aria-label': `joystick ${JOY_LABEL[name]}, ${n} binding${n === 1 ? '' : 's'}`,
    }, [
      el('span', { class: 'cc-t', text: name.toUpperCase() }),
      el('span', { class: 'cc-n', text: n ? String(n) : '', 'aria-hidden': 'true' }),
    ]);
    b.style.gridArea = `${r + 1} / ${c + 1}`;
    b.addEventListener('click', () => selectControl('joystick', i));
    grid.append(b);
  }
  // The middle of the compass is the stick at rest: no sector, so nothing to bind.
  const hub = el('span', { class: 'ctljoy-hub', 'aria-hidden': 'true' });
  hub.style.gridArea = '2 / 2';
  grid.append(hub);
  box.append(grid, el('div', { class: 'ctljoy-side' }, [
    el('span', { class: 'ctljoy-t', text: 'Joystick direction' }),
    el('p', { class: 'hint tight', text:
      'A free 360° disc cut into eight 45° sectors — the four marks on the faceplate are a '
      + 'convention, not a gate. Each direction binds on its own, with the full press / release / '
      + 'hold / double set.' }),
  ]));
  return box;
}

function selectControl(control, index) {
  state.bind = { control, index };
  if (control === 'key') state.sel = { zone: 'keys', index, pos: state.geom?.keys?.[index]?.pos?.join(',') || null };
  renderBindingsPanel();
  renderColorPanel();
  if (state.lastFrame) paint(state.lastFrame);
}

function renderTriggerCards() {
  const mount = $('bind-triggers');
  mount.textContent = '';
  const { control, index } = state.bind;
  for (const kind of controlTriggers(control)) mount.append(triggerCard(control, index, kind));
  // touch / rear live on the profile even while a mode is being edited; say so once, here,
  // rather than letting someone believe they made a mode-only override.
  if (state.scope && (control === 'touch' || control === 'rear')) {
    mount.append(el('p', { class: 'hint', text:
      `Modes do not override ${controlName(control, 0).toLowerCase()} bindings — the daemon resolves `
      + `them from the profile only, so these stay profiles.${state.profile}.${control} whatever the `
      + 'Editing selector says.' }));
  }
}

/** Replace one card in place, keeping the rest of the panel (and the caret) alone. */
function rebuildCard(control, index, kind, focus) {
  const old = $('bind-triggers').querySelector(`.trig[data-kind="${kind}"]`);
  const next = triggerCard(control, index, kind);
  if (old) old.replaceWith(next); else $('bind-triggers').append(next);
  if (focus) next.querySelector(focus)?.focus();
  refreshTriggerNotes();
}

/** Re-run just the interaction warnings across all cards — binding `double` changes what the
 *  `press` card has to say, and that must not wait for a full re-render. */
function refreshTriggerNotes() {
  const { control, index } = state.bind;
  for (const kind of controlTriggers(control)) {
    const card = $('bind-triggers').querySelector(`.trig[data-kind="${kind}"]`);
    if (!card) continue;
    const slot = card.querySelector('.trig-notes');
    if (slot) { slot.textContent = ''; for (const n of triggerNotes(control, index, kind)) slot.append(n); }
  }
  renderTimingBox();
  renderControlPicker();
}

function triggerCard(control, index, kind) {
  const b = bindingAt(control, index, kind);
  const type = actionKeyOf(b);
  const card = el('div', { class: 'trig', 'data-kind': kind, 'data-bound': b ? '1' : '0' });

  const head = el('div', { class: 'trig-head' }, [
    el('strong', { text: TRIGGER_LABEL[kind] || kind }),
    el('span', { class: 'pill', text: type ? bindingType(type).label : 'not bound' }),
    el('span', { class: 'spacer' }),
  ]);
  const test = el('button', {
    type: 'button', class: 'ghost small test', text: '▶ Test',
    title: b ? `Inject the ${kind} event for ${controlName(control, index)} — a simulated press, not a real one`
      : 'Nothing is bound to this trigger',
  });
  test.disabled = !b;
  test.addEventListener('click', () => testTrigger(control, index, kind));
  head.append(test);
  card.append(head);

  const body = el('div', { class: 'trig-body' });

  const sel = el('select', { class: 'trig-type', 'aria-label': `${TRIGGER_LABEL[kind] || kind} does` });
  sel.append(el('option', { value: '', text: '(nothing)' }));
  for (const t of BINDING_TYPES) sel.append(el('option', { value: t.key, text: t.label }));
  sel.value = type || '';
  sel.addEventListener('change', () => {
    setBindingType(control, index, kind, sel.value);
    touch(null);
    rebuildCard(control, index, kind, '.trig-type');
  });
  body.append(el('label', { class: 'field' }, [el('span', { text: 'Does' }), sel]));

  if (b && type) {
    body.append(bindingValueEditor(control, index, kind, type, b));
    body.append(el('p', { class: 'hint tight', html: bindingType(type).hint }));
    body.append(flashRow(control, index, kind, b));
  }
  card.append(body);

  const notes = el('div', { class: 'trig-notes' });
  for (const n of triggerNotes(control, index, kind)) notes.append(n);
  card.append(notes);
  return card;
}

/** The value control for a binding's single action key. */
function bindingValueEditor(control, index, kind, type, b) {
  const meta = bindingType(type);
  const write = (v) => {
    const t = triggersFor(control, index, true);
    if (!t || !t[kind]) return;
    t[kind][type] = v;
    touch(null);
  };
  const label = (kids) => el('label', { class: 'field' }, [el('span', { text: meta.label }), ...[].concat(kids)]);

  if (meta.input === 'multiline') {
    const ta = el('textarea', { rows: '2', spellcheck: 'false', placeholder: meta.ph, 'aria-label': meta.label });
    ta.value = String(b[type] ?? '');
    ta.addEventListener('input', () => { write(ta.value); markEmpty(ta); });
    markEmpty(ta);
    return label(ta);
  }
  if (meta.input === 'shortcut') return shortcutEditor(control, index, kind, b, write);
  if (meta.input === 'action') {
    const s = el('select', { 'aria-label': 'Built-in action' });
    const g1 = el('optgroup', { label: 'Media / volume / brightness — needs the helper' });
    for (const a of MEDIA_ACTIONS) g1.append(el('option', { value: a, text: a }));
    const g2 = el('optgroup', { label: 'Daemon-side — works with no helper' });
    for (const a of NATIVE_ACTIONS) g2.append(el('option', { value: a, text: a }));
    s.append(g1, g2);
    if (!ACTION_ENUM.includes(b[type])) s.append(el('option', { value: b[type], text: `${b[type] || '(empty)'} — not in the schema enum` }));
    s.value = b[type] ?? '';
    s.addEventListener('change', () => write(s.value));
    return label(s);
  }
  if (meta.input === 'mode' || meta.input === 'profile') {
    const names = meta.input === 'mode' ? Object.keys(profileModes()) : Object.keys(state.config?.profiles || {});
    const s = el('select', { 'aria-label': meta.label });
    if (meta.input === 'profile') {
      const g = el('optgroup', { label: 'Cycle' });
      g.append(el('option', { value: 'next', text: 'next' }), el('option', { value: 'prev', text: 'prev' }));
      s.append(g);
    }
    const g = el('optgroup', { label: meta.input === 'mode' ? 'Modes in this profile' : 'Profiles' });
    for (const n of names) g.append(el('option', { value: n, text: n }));
    if (!names.length) g.append(el('option', { value: '', text: meta.input === 'mode' ? '(this profile has no modes)' : '(none)' }));
    s.append(g);
    const known = names.includes(b[type]) || (meta.input === 'profile' && ['next', 'prev'].includes(b[type]));
    if (!known) s.append(el('option', { value: b[type] ?? '', text: `${b[type] || '(empty)'} — does not exist` }));
    s.value = b[type] ?? '';
    s.addEventListener('change', () => write(s.value));
    const wrap = label(s);
    if (!known) wrap.append(el('p', { class: 'hint tight warn', text: `There is no ${meta.input} named “${b[type]}” in this config — the daemon will refuse the trigger at runtime.` }));
    return wrap;
  }
  const inp = el('input', { type: 'text', spellcheck: 'false', autocomplete: 'off', placeholder: meta.ph, 'aria-label': meta.label });
  inp.value = String(b[type] ?? '');
  inp.addEventListener('input', () => { write(inp.value); markEmpty(inp); });
  markEmpty(inp);
  return label(inp);
}

/** An action key with an empty value is legal JSON and a useless binding; flag it in place. */
function markEmpty(node) { node.dataset.empty = node.value.trim() ? '0' : '1'; }

/* ------------------------------------------------------------ the recorder */

function shortcutEditor(control, index, kind, b, write) {
  const wrap = el('div', { class: 'field rec' });
  const inp = el('input', { type: 'text', class: 'rec-in', spellcheck: 'false', autocomplete: 'off', placeholder: 'cmd+shift+4', 'aria-label': 'Shortcut spec' });
  inp.value = String(b.shortcut ?? '');
  const btn = el('button', { type: 'button', class: 'ghost small', text: '● Record' });
  const status = el('p', { class: 'rec-status' });

  const describe = () => {
    const raw = inp.value.trim();
    if (!raw) { status.dataset.kind = 'warn'; status.textContent = 'No chord yet — press Record, or type one.'; return; }
    const p = parseSpec(raw);
    if (!p.ok) { status.dataset.kind = 'err'; status.textContent = 'Not a chord the daemon will accept: ' + p.error; return; }
    const res = reservedChord(p.spec);
    status.dataset.kind = res ? 'warn' : 'ok';
    status.textContent = (p.spec === raw.toLowerCase() ? `Normalised: ${p.spec}` : `“${raw}” normalises to ${p.spec}`)
      + (res ? ` — taken before a browser can see it (${res.who}), so this one has to be typed. Bound here it still works: the pad sends it and macOS acts on it.` : '');
  };

  inp.addEventListener('input', () => {
    write(inp.value);
    markEmpty(inp);
    describe();
  });
  inp.addEventListener('change', () => {
    const p = parseSpec(inp.value);
    if (p.ok) { inp.value = p.spec; write(p.spec); }   // store what the daemon stores
    describe();
  });
  markEmpty(inp);
  describe();

  btn.addEventListener('click', () => {
    if (state.rec) { stopRecording(); return; }
    startRecording({ btn, inp, status, commit: (spec) => { inp.value = spec; write(spec); markEmpty(inp); describe(); } });
  });

  wrap.append(el('span', {}, [el('span', { text: 'Keyboard shortcut' })]));
  wrap.append(el('div', { class: 'rec-row' }, [inp, btn]));
  wrap.append(status);
  wrap.append(el('details', { class: 'rec-help' }, [
    el('summary', { text: 'Chords a browser cannot capture' }),
    el('p', { class: 'hint tight', text:
      'macOS and the browser claim some chords before any page sees the keydown, and a page cannot '
      + 'opt out. Type those into the field instead — they still work as bindings, because the pad, '
      + 'not the browser, is what sends them. The fn modifier is invisible to browsers altogether, '
      + 'so fn chords always have to be typed.' }),
    el('ul', { class: 'rec-list' }, RESERVED_CHORDS.map((r) => el('li', {}, [
      el('code', { text: r.spec }), el('span', { class: 'hint tight', text: ' — ' + r.who }),
    ]))),
  ]));
  return wrap;
}

function startRecording(ui) {
  stopRecording();
  state.rec = ui;
  ui.btn.textContent = '■ Stop';
  ui.btn.classList.add('primary');
  ui.status.dataset.kind = 'rec';
  ui.status.textContent = 'Listening — press the chord now. Escape cancels.';
  window.addEventListener('keydown', onRecordKey, true);
  window.addEventListener('blur', onRecordBlur);
}

function stopRecording() {
  const ui = state.rec;
  state.rec = null;
  window.removeEventListener('keydown', onRecordKey, true);
  window.removeEventListener('blur', onRecordBlur);
  if (!ui) return;
  ui.btn.textContent = '● Record';
  ui.btn.classList.remove('primary');
}

function onRecordKey(ev) {
  const ui = state.rec;
  if (!ui) return;
  ev.preventDefault();
  ev.stopPropagation();

  // Escape alone gets out — a recorder you cannot leave with the key everyone reaches for is a
  // trap. Escape as a *binding* is typed, which the status line says.
  if (ev.code === 'Escape' && !ev.metaKey && !ev.ctrlKey && !ev.altKey && !ev.shiftKey) {
    ui.status.dataset.kind = 'warn';
    ui.status.textContent = 'Cancelled. To bind Escape itself, type “escape” in the field.';
    stopRecording();
    return;
  }

  const r = specFromEvent(ev);
  if (r.pending) {
    ui.status.dataset.kind = 'rec';
    ui.status.textContent = `Listening — ${r.mods.join('+')}+…`;
    return;
  }
  if (r.error) {
    ui.status.dataset.kind = 'err';
    ui.status.textContent = r.error + ' — type the name if you know it, or pick another key.';
    return;
  }
  stopRecording();
  const p = parseSpec(r.spec);
  ui.commit(p.ok ? p.spec : r.spec);
  toast(`Recorded ${p.ok ? p.spec : r.spec}`, 'ok', 2200);
}

function onRecordBlur() {
  const ui = state.rec;
  if (!ui) return;
  stopRecording();
  ui.status.dataset.kind = 'warn';
  ui.status.textContent = 'Something else took that chord before this page saw it — macOS or the '
    + 'browser owns it. Type it into the field instead; it still works as a binding.';
}

/* --------------------------------------------------------------- flash colour */

function flashRow(control, index, kind, b) {
  const on = el('input', { type: 'checkbox', 'aria-label': 'Flash the key as confirmation' });
  const col = el('input', { type: 'color', 'aria-label': 'Flash colour' });
  const hex = el('input', { type: 'text', class: 'ce-hex', spellcheck: 'false', maxlength: '7', 'aria-label': 'Flash colour hex' });
  const has = isHex6(b.flash);
  on.checked = has;
  const value = has ? b.flash.toLowerCase() : 'ffffff';
  col.value = '#' + value;
  hex.value = value;
  col.disabled = hex.disabled = !has;

  const write = (v) => {
    const t = triggersFor(control, index, true);
    if (!t || !t[kind]) return;
    if (v === null) delete t[kind].flash; else t[kind].flash = v;
    touch(null);
  };
  on.addEventListener('change', () => {
    col.disabled = hex.disabled = !on.checked;
    write(on.checked ? hex.value.replace('#', '').toLowerCase() : null);
  });
  col.addEventListener('input', () => { hex.value = col.value.replace('#', ''); write(hex.value); });
  hex.addEventListener('input', () => {
    const v = hex.value.replace('#', '').trim().toLowerCase();
    if (isHex6(v)) { col.value = '#' + v; write(v); }
  });

  return el('label', { class: 'field' }, [
    el('span', { text: 'Flash on fire (optional)' }),
    el('span', { class: 'swatchrow' }, [on, col, hex,
      el('span', { class: 'hint tight', text: control === 'key' ? 'Briefly lights this key.' : 'Briefly lights the pad.' })]),
  ]);
}

/* -------------------------------------------------- the interaction warnings */

/* The one genuinely surprising thing about trigger kinds, said where it is chosen rather than in
 * a help page: binding `double` costs `press` its immediacy, and a hold that fires eats the
 * press. Both come straight from events.py's Recognizer. */
function triggerNotes(control, index, kind) {
  const out = [];
  const bound = new Set(boundKinds(control, index));
  const note = (text, cls) => out.push(el('p', { class: 'trig-note' + (cls ? ' ' + cls : ''), text }));

  if (control === 'encoder') {
    if (kind === 'cw' || kind === 'ccw') {
      note('Fires once per detent, immediately — a dial that lags is unusable. Rotation has no hold or double.');
    } else {
      note('The encoder button. Its press fires when the button is released, and the schema gives the encoder only cw / ccw / press — no release, hold or double.');
    }
    return out;
  }

  /* The pad answers a capability query with the event kinds its build actually emits. A firmware
   * that doesn't list `joy` will never send one, so a joystick binding on that build is inert until
   * it is reflashed — said once, on the first card, rather than on all four. */
  if (control === 'joystick' && kind === 'press') {
    const evs = state.status?.firmware?.events;
    if (Array.isArray(evs) && evs.length && !evs.includes('joy')) {
      note(`The firmware on the pad reports ${evs.join(', ')} — not joy — so no joystick event `
        + 'reaches the daemon from hardware on this build, whatever is bound to any direction. '
        + 'Test below injects one, which does fire it.', 'warn');
    }
  }

  const tapOnly = control === 'touch' || control === 'rear';
  if (kind === 'press') {
    if (bound.has('double')) {
      note(`Because “double tap” is bound here, press cannot fire until the ${doubleMs()} ms `
        + 'double-tap window has closed — the daemon does not yet know whether a second tap is '
        + 'coming. This control will not feel instant. Unbind double to get press back immediately.', 'warn');
    } else {
      note('Fires the moment the control is released, with no delay — nothing is bound to double, so there is no window to wait out.');
    }
    if (bound.has('hold')) {
      note(`A press held past ${holdMs()} ms fires “hold” instead and press is suppressed, so one long press does one thing.`);
    }
  } else if (kind === 'release') {
    note('Independent of the rest: fires on every release, including one that already fired hold or double. Bind it for press-and-hold behaviour (walkie-talkie style) together with press.');
  } else if (kind === 'hold') {
    note(`Fires ${holdMs()} ms after the control goes down, while it is still held — and suppresses the press that the release would otherwise produce.`);
    if (tapOnly) {
      note(`Firmware v2 reports the ${controlName(control, 0).toLowerCase()} as one line with no `
        + 'down/up pair, and a tap has no duration — so hold can never fire for this control from '
        + 'real hardware. Test below sends a genuine down … up pair, which does fire it.', 'warn');
    }
  } else if (kind === 'double') {
    note(`Two taps inside ${doubleMs()} ms. Binding this is what costs latency: press on this `
      + 'control now waits out that window on every single tap. Controls with no double binding '
      + 'fire press instantly whatever double_ms says.', 'warn');
    if (bound.has('press')) note('A double tap fires double only — the deferred press is cancelled, so you never get press-then-double.');
  }
  return out;
}

function renderTimingBox() {
  $('rng-hold').value = String(holdMs());
  $('out-hold').textContent = holdMs() + ' ms';
  $('rng-double').value = String(doubleMs());
  $('out-double').textContent = doubleMs() + ' ms';
  $('timing-scope').textContent = 'device.hold_ms / device.double_ms';

  // Who is actually paying for double_ms, listed by name — the cost is invisible otherwise.
  const payers = [];
  for (const [pname, p] of Object.entries(state.config?.profiles || {})) {
    const scan = (owner, where) => {
      for (const k of owner?.keys || []) if (k?.on?.double) payers.push(`${where}key ${k.index}`);
      for (const c of ['touch', 'rear']) if (owner?.[c]?.double) payers.push(`${where}${c}`);
      for (const d of JOY_DIRS) if (owner?.joystick?.[d]?.double) payers.push(`${where}joystick ${d}`);
    };
    scan(p, state.config.profiles && Object.keys(state.config.profiles).length > 1 ? `${pname}: ` : '');
    for (const [mname, m] of Object.entries(p?.modes || {})) scan(m, `${pname}/${mname}: `);
  }
  $('timing-note').textContent =
    `Hold fires after ${holdMs()} ms; a second tap inside ${doubleMs()} ms is a double. These are `
    + 'global (device.hold_ms / device.double_ms), and double_ms only delays controls that '
    + (payers.length
      ? `actually bind double — currently ${payers.join(', ')}.`
      : 'actually bind double. Nothing in this config binds double, so nothing pays for it.');
}

/* ------------------------------------------------------------ capability box */

function renderCapsBox() {
  const box = $('caps-box');
  const caps = state.status.keys;
  const note = $('caps-note');
  if (!caps || typeof caps !== 'object') {
    box.hidden = state.daemonReachable !== false;
    box.dataset.level = 'info';
    note.textContent = 'No daemon, so keyboard-synthesis availability is unknown. shortcut, text and '
      + 'media action bindings all need the native helper on the machine that runs the daemon.';
    return;
  }
  box.hidden = false;
  const built = caps.built !== false && caps.helper !== null;
  const access = caps.accessibility;
  const bits = [];
  if (!built) {
    box.dataset.level = 'err';
    bits.push(`The ${'lmkey'} helper is not built (looked at ${caps.expected_at || 'host/swift/lmkey'}), so shortcut, text and the media half of action do nothing at all — silently.`);
    if (caps.hint) bits.push(caps.hint);
  } else if (access === false) {
    box.dataset.level = 'err';
    bits.push('The helper is built but not trusted for Accessibility, so synthesised keys are discarded silently.');
    bits.push(caps.hint || 'Grant Accessibility in System Settings › Privacy & Security › Accessibility.');
  } else if (access === null || access === undefined) {
    box.dataset.level = 'warn';
    bits.push('The helper is built, but whether it is trusted for Accessibility could not be determined. If shortcuts do nothing, that is the reason.');
  } else {
    box.dataset.level = 'ok';
    bits.push('Helper built and trusted for Accessibility — shortcut, text and media actions will land.');
  }
  if (!built || access !== true) {
    bits.push('macOS attributes that permission to the process that launched the daemon — your Terminal, iTerm or launchd job — not to the helper binary, so grant it there and restart the daemon.');
  }
  const users = countSynthBindings();
  if (users) bits.push(`${users} binding${users === 1 ? '' : 's'} in this config need it.`);
  // The daemon's hints don't end in a full stop, and two sentences running together read as one.
  note.textContent = bits.map((b) => (/[.!?]$/.test(b.trim()) ? b.trim() : b.trim() + '.')).join(' ');
}

/** How many bindings in the whole config depend on the native helper. */
function countSynthBindings() {
  let n = 0;
  const scan = (t) => {
    for (const b of Object.values(t || {})) {
      if (!b || typeof b !== 'object') continue;
      if (b.shortcut !== undefined || b.text !== undefined) n++;
      else if (typeof b.action === 'string' && MEDIA_ACTIONS.includes(b.action)) n++;
    }
  };
  const scanOwner = (o) => {
    for (const k of o?.keys || []) scan(k?.on);
    scan(o?.encoder); scan(o?.touch); scan(o?.rear);
    for (const d of JOY_DIRS) scan(o?.joystick?.[d]);
  };
  for (const p of Object.values(state.config?.profiles || {})) {
    scanOwner(p);
    for (const m of Object.values(p?.modes || {})) scanOwner(m);
  }
  return n;
}

/* ============================================ 16d. testing without hardware */

/* Firmware v2 emits real key events but is not flashed yet, so POST /api/simulate is how a
 * binding gets fired at all today — and afterwards it stays the way to test one without
 * reaching for the pad. Everything here goes through the daemon's real recogniser and
 * dispatcher: the only thing simulated is the event on the wire. */

const HOLD_MARGIN_MS = 160;
/** The daemon's own hold sleep is capped at 2 s (server.py), so past that we time it here. */
const SERVER_HOLD_CAP_MS = 1800;

async function inject(body) {
  const res = await api.simulate(body);
  if (!res.ok) {
    toast(res.reachable
      ? 'The daemon refused the injection: ' + ((res.data && (res.data.errors || []).join('; ')) || res.error)
      : 'No daemon — nothing to inject into. Start the daemon to test bindings.', 'err', 5500);
    return null;
  }
  return res.data || {};
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function testTrigger(control, index, kind) {
  const what = controlName(control, index).toLowerCase();
  const b = bindingAt(control, index, kind);
  const type = actionKeyOf(b);
  let ok = null;

  if (control === 'encoder') {
    if (kind === 'cw' || kind === 'ccw') ok = await inject({ line: `enc ${kind}` });
    else {
      ok = await inject({ line: 'enc press' });
      if (ok) ok = await inject({ line: 'enc release' });
    }
  } else if (control === 'key') {
    if (kind === 'hold') {
      const ms = holdMs() + HOLD_MARGIN_MS;
      if (ms <= SERVER_HOLD_CAP_MS) {
        // Let the daemon hold the key down for real: hold_s makes its recogniser watch the clock
        // rather than us pretending the trigger fired.
        ok = await inject({ key: index, hold_s: ms / 1000 });
      } else {
        ok = await inject({ line: `key ${index} down` });
        if (ok) { await wait(ms); ok = await inject({ line: `key ${index} up` }); }
      }
    } else if (kind === 'double') {
      ok = await inject({ key: index });
      if (ok) ok = await inject({ key: index });    // second tap inside double_ms
    } else {
      ok = await inject({ key: index });
    }
  } else if (control === 'joystick') {
    // A joystick direction is `joy <dir> down|up` — events.py takes no bare form for it, because a
    // sector has to open and close, so even a plain press is a pair.
    const dir = JOY_DIRS[index];
    const down = () => inject({ line: `joy ${dir} down` });
    const up = () => inject({ line: `joy ${dir} up` });
    if (kind === 'hold') {
      ok = await down();
      if (ok) { await wait(holdMs() + HOLD_MARGIN_MS); ok = await up(); }
    } else if (kind === 'double') {
      ok = await down();
      if (ok) ok = await up();
      if (ok) ok = await down();
      if (ok) ok = await up();
    } else {
      ok = await down();
      if (ok) ok = await up();
    }
  } else {
    // touch / rear: v2 sends one bare line per activation, but the daemon also accepts an
    // explicit down/up pair, which is the only way to exercise hold.
    if (kind === 'hold') {
      ok = await inject({ line: `${control} down` });
      if (ok) { await wait(holdMs() + HOLD_MARGIN_MS); ok = await inject({ line: `${control} up` }); }
    } else if (kind === 'double') {
      ok = await inject({ line: control });
      if (ok) ok = await inject({ line: control });
    } else {
      ok = await inject({ line: control });
    }
  }

  if (!ok) return;
  pollEvents();
  const also = kind === 'press' && boundKinds(control, index).includes('release')
    ? ' The bound release fired too — a real press always releases.' : '';
  const slow = kind === 'press' && boundKinds(control, index).includes('double')
    ? ` Watch for the ${doubleMs()} ms delay: double is bound here, so press waits out the window.` : '';
  toast(`Simulated ${kind} on ${what}${type ? ` → ${bindingType(type).label.toLowerCase()}` : ''}.`
    + ' This was injected, not a real press.' + also + slow, 'ok', 5200);
}

/* ================================================== 16e. panels: profiles */

function profileNames() { return Object.keys(state.config?.profiles || {}); }

function uniqueName(base, taken) {
  let n = base, i = 2;
  while (taken.includes(n)) n = `${base}-${i++}`;
  return n;
}

/** Rebuild `profiles` in a new key order. Object key order is what profile_next cycles in, so
 *  reordering is a real edit and not cosmetic. */
function reorderProfile(name, delta) {
  const names = profileNames();
  const i = names.indexOf(name);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= names.length) return;
  names.splice(j, 0, ...names.splice(i, 1));
  const next = {};
  for (const n of names) next[n] = state.config.profiles[n];
  state.config.profiles = next;
  touch(null);
  renderProfileSelect();
  renderProfilesPanel();
}

function renameProfile(from, to) {
  if (!from || !to || from === to) return false;
  if (profileNames().includes(to)) { toast(`A profile called “${to}” already exists`, 'warn'); return false; }
  const names = profileNames();
  const next = {};
  for (const n of names) next[n === from ? to : n] = state.config.profiles[n];
  state.config.profiles = next;
  if (state.config.active_profile === from) state.config.active_profile = to;
  // Keep any binding that switched to it pointing at it.
  for (const p of Object.values(state.config.profiles)) {
    const fix = (t) => { for (const b of Object.values(t || {})) if (b && b.profile === from) b.profile = to; };
    for (const k of p?.keys || []) fix(k?.on);
    fix(p?.encoder); fix(p?.touch); fix(p?.rear);
    for (const m of Object.values(p?.modes || {})) {
      for (const k of m?.keys || []) fix(k?.on);
      fix(m?.encoder);
    }
  }
  if (state.profile === from) state.profile = to;
  touch(null);
  return true;
}

function renderProfilesPanel() {
  const list = $('prof-list');
  list.textContent = '';
  const names = profileNames();
  const active = state.status.active_profile || state.config?.active_profile;

  names.forEach((n, i) => {
    const p = state.config.profiles[n];
    const row = el('div', { class: 'lrow', 'data-sel': n === state.profile ? '1' : '0' });
    const pick = el('button', { type: 'button', class: 'lrow-main', 'aria-pressed': n === state.profile ? 'true' : 'false' }, [
      el('span', { class: 'lrow-t', text: p?.label ? `${p.label}` : n }),
      el('span', { class: 'lrow-s', text: n + (p?.auto_activate_app ? ` · auto for ${p.auto_activate_app}` : '') }),
    ]);
    pick.addEventListener('click', () => { setProfileEditing(n); });
    row.append(pick);
    if (n === active) row.append(el('span', { class: 'tag ok', text: 'active' }));
    else if (n === state.config?.active_profile) row.append(el('span', { class: 'tag', text: 'startup' }));

    const up = el('button', { type: 'button', class: 'ghost small', text: '↑', 'aria-label': `Move ${n} earlier` });
    up.disabled = i === 0;
    up.addEventListener('click', () => reorderProfile(n, -1));
    const down = el('button', { type: 'button', class: 'ghost small', text: '↓', 'aria-label': `Move ${n} later` });
    down.disabled = i === names.length - 1;
    down.addEventListener('click', () => reorderProfile(n, 1));
    const now = el('button', { type: 'button', class: 'ghost small', text: 'Switch now', title: 'POST /api/profile — changes the running daemon without saving' });
    now.addEventListener('click', async () => {
      const res = await api.setProfile(n);
      if (res.ok && res.data && res.data.ok !== false) { toast(`Daemon switched to “${res.data.active_profile || n}”`, 'ok'); refreshStatus(); }
      else toast(res.reachable ? 'Daemon refused the switch: ' + ((res.data && (res.data.errors || []).join('; ')) || res.error) : 'No daemon — nothing to switch', 'warn');
    });
    const del = el('button', { type: 'button', class: 'ghost small danger', text: 'Delete', 'aria-label': `Delete profile ${n}` });
    del.disabled = names.length <= 1;
    del.addEventListener('click', () => {
      if (!confirm(`Delete profile “${n}” and everything in it?`)) return;
      delete state.config.profiles[n];
      if (state.config.active_profile === n) state.config.active_profile = profileNames()[0];
      if (state.profile === n) { state.profile = profileNames()[0]; state.scope = null; }
      touch(null);
      renderProfileSelect(); renderScopeSelect(); renderProfilesPanel();
      adoptScopeChange();
    });
    row.append(el('div', { class: 'lrow-acts' }, [up, down, now, del]));
    list.append(row);
  });

  const p = currentProfile();
  $('prof-name').textContent = state.profile || '—';
  if (document.activeElement !== $('prof-key')) $('prof-key').value = state.profile || '';
  if (document.activeElement !== $('prof-label')) $('prof-label').value = p?.label || '';
  if (document.activeElement !== $('prof-app')) $('prof-app').value = p?.auto_activate_app || '';
  for (const id of ['prof-key', 'prof-label', 'prof-app']) $(id).disabled = !p;

  renderModesPanel();
}

function setProfileEditing(n) {
  state.profile = n;
  state.scope = null;
  state.modeSel = null;
  renderProfileSelect();
  renderScopeSelect();
  adoptScopeChange();
  renderProfilesPanel();
}

/* --------------------------------------------------------------------- modes */

function renderModesPanel() {
  $('mode-owner').textContent = state.profile || '—';
  const modes = profileModes();
  const names = Object.keys(modes);
  if (state.modeSel && !names.includes(state.modeSel)) state.modeSel = null;

  const list = $('mode-list');
  list.textContent = '';
  if (!names.length) {
    list.append(el('p', { class: 'hint', text: 'No modes in this profile. A mode is optional — add one when you want the encoder to mean something different for a while.' }));
  }
  for (const n of names) {
    const m = modes[n];
    const row = el('div', { class: 'lrow', 'data-sel': n === state.modeSel ? '1' : '0' });
    const encBits = ENC_TRIGGERS.filter((k) => m?.encoder?.[k]).map((k) => `${k}: ${describeBinding(m.encoder[k])}`);
    const pick = el('button', { type: 'button', class: 'lrow-main', 'aria-pressed': n === state.modeSel ? 'true' : 'false' }, [
      el('span', { class: 'lrow-t', text: n }),
      el('span', { class: 'lrow-s', text: [
        Number.isInteger(m?.activate_key) ? `key ${m.activate_key}` : 'no activate key',
        encBits.length ? encBits.join(', ') : 'encoder unbound',
        m?.timeout_s ? `reverts after ${m.timeout_s}s` : 'stays until switched',
      ].join(' · ') }),
    ]);
    pick.addEventListener('click', () => { state.modeSel = n; renderModesPanel(); });
    row.append(pick);
    if (state.status.active_mode === n) row.append(el('span', { class: 'tag ok', text: 'active' }));
    const del = el('button', { type: 'button', class: 'ghost small danger', text: 'Delete', 'aria-label': `Delete mode ${n}` });
    del.addEventListener('click', () => {
      if (!confirm(`Delete mode “${n}”? Bindings that activate it will stop working.`)) return;
      delete currentProfile().modes[n];
      if (!Object.keys(currentProfile().modes).length) delete currentProfile().modes;
      if (state.scope === n) { state.scope = null; adoptScopeChange(); }
      if (state.modeSel === n) state.modeSel = null;
      touch(null);
      renderScopeSelect(); renderModesPanel(); renderBindingsPanel();
    });
    row.append(el('div', { class: 'lrow-acts' }, [del]));
    list.append(row);
  }

  const box = $('mode-edit');
  const m = state.modeSel ? modes[state.modeSel] : null;
  box.hidden = !m;
  if (!m) return;
  $('mode-name').textContent = state.modeSel;
  if (document.activeElement !== $('mode-key')) $('mode-key').value = state.modeSel;

  const sel = $('mode-actkey');
  if (!sel.options.length) {
    sel.append(el('option', { value: '', text: '(none)' }));
    for (let i = 0; i < KEY_COUNT; i++) sel.append(el('option', { value: String(i), text: `key ${i}` }));
  }
  sel.value = Number.isInteger(m.activate_key) ? String(m.activate_key) : '';

  const to = Number(m.timeout_s) || 0;
  $('rng-modeto').value = String(clamp(Math.round(to), 0, 60));
  $('out-modeto').textContent = to ? `${Math.round(to)} s of encoder silence` : 'never (stays until switched)';

  const has = isHex6(m.flash);
  $('mode-flash-on').checked = has;
  const v = has ? m.flash.toLowerCase() : 'ffffff';
  $('mode-flash').value = '#' + v;
  if (document.activeElement !== $('mode-flash-hex')) $('mode-flash-hex').value = v;
  $('mode-flash').disabled = $('mode-flash-hex').disabled = !has;

  const bits = [];
  const actKey = m.activate_key;
  if (Number.isInteger(actKey)) {
    const binding = (currentProfile()?.keys || []).find((k) => k?.index === actKey)?.on?.press;
    if (!binding || binding.mode !== state.modeSel) {
      bits.push(`activate_key says key ${actKey}, but that key's press is not bound to this mode — `
        + 'activate_key only tells the UI and the LED which key belongs to the mode. Bind the key '
        + `to “Activate mode → ${state.modeSel}” to reach it from the pad.`);
    }
  } else {
    bits.push('No activate_key, so nothing on the board is marked as this mode\'s key. Bind a key to “Activate mode” to reach it from the pad.');
  }
  if (!ENC_TRIGGERS.some((k) => m.encoder && m.encoder[k])) {
    bits.push('This mode rebinds nothing on the encoder yet, which is usually the point of a mode.');
  }
  $('mode-note').textContent = bits.join(' ');
}

/** One-line summary of a binding, for lists. */
function describeBinding(b) {
  const k = actionKeyOf(b);
  if (!k) return 'nothing';
  const v = String(b[k] ?? '');
  return `${k}${v ? ' ' + (v.length > 22 ? v.slice(0, 21) + '…' : v).replace(/\n/g, ' ⏎ ') : ''}`;
}

function mutateMode(fn) {
  const m = state.modeSel ? profileModes()[state.modeSel] : null;
  if (!m) return;
  fn(m);
  touch(null);
  renderModesPanel();
}

/* ==================================================== 16f. panels: events */

let eventTimer = null;
const EVENT_POLL_MS = 700;
const EVENT_POLL_IDLE_MS = 4000;
const EVENT_KEEP = 200;

async function pollEvents() {
  clearTimeout(eventTimer);
  if (!state.evPoll) { eventTimer = null; return; }
  const res = await api.getEvents(state.eventSeq);
  if (res.ok && res.data && Array.isArray(res.data.events)) {
    state.inputSeen = !!res.data.input_events_seen;
    if (res.data.events.length) {
      // The daemon keeps a bounded deque, so a long gap can leave holes. Ordering is all this
      // needs, and `seq` gives it.
      for (const e of res.data.events) {
        state.events.push(e);
        state.eventSeq = Math.max(state.eventSeq, Number(e.seq) || 0);
        flagHit(e);
      }
      if (state.events.length > EVENT_KEEP) state.events.splice(0, state.events.length - EVENT_KEEP);
      renderEventFeed();
    } else if (Number(res.data.latest_seq) > state.eventSeq) {
      state.eventSeq = Number(res.data.latest_seq);
    }
    renderEventState();
  }
  eventTimer = setTimeout(pollEvents, state.daemonReachable ? EVENT_POLL_MS : EVENT_POLL_IDLE_MS);
}

/** Light the control an event refers to, on the board. */
function flagHit(e) {
  const args = Array.isArray(e.args) ? e.args : [];
  if (e.event === 'key') {
    const i = Number(args[0]);
    if (Number.isInteger(i)) noteHit('key', i, e.source === 'device' ? 'device' : 'injected');
  } else if (e.event === 'enc') {
    noteHit('encoder', 0, e.source === 'device' ? 'device' : 'injected');
  } else if (e.event === 'touch') {
    noteHit('touch', 0, e.source === 'device' ? 'device' : 'injected');
  } else if (e.event === 'joy') {
    // One glyph for all eight sectors, so the stick lights whichever direction moved.
    noteHit('joystick', 0, e.source === 'device' ? 'device' : 'injected');
  }
}

/** What an event means, in the terms the config uses. */
function explainEvent(e) {
  const args = Array.isArray(e.args) ? e.args : [];
  if (e.event === 'key') {
    const i = Number(args[0]);
    const label = Number.isInteger(i) ? keyLabelOf(i) : '';
    const strip = Number.isInteger(i) ? state.geom?.keys?.[i]?.strip : null;
    if (!Number.isInteger(i) || i < 0 || i >= KEY_COUNT) return `key index ${args[0]} is outside 0–${KEY_COUNT - 1} — the firmware must report LOGICAL indices`;
    const kinds = boundKinds('key', i);
    return `logical key ${i}${label ? ` “${label}”` : ''}${strip === null || strip === undefined ? '' : ` (LED strip index ${strip})`}`
      + ` · ${args[1] || 'edge'}${kinds.length ? ` · bound: ${kinds.join(', ')}` : ' · nothing bound'}`;
  }
  if (e.event === 'enc') {
    const w = args[0] || '';
    if (w === 'cw' || w === 'ccw') return `encoder detent ${w}${bindingAt('encoder', 0, w) ? ` → ${describeBinding(bindingAt('encoder', 0, w))}` : ' · nothing bound'}`;
    return `encoder button ${w}`;
  }
  if (e.event === 'joy') {
    const d = args[0] || '';
    const i = JOY_DIRS.indexOf(d);
    if (i === -1) return `joystick direction "${args[0]}" is not one of ${JOY_DIRS.join(', ')}`;
    const kinds = boundKinds('joystick', i);
    return `joystick ${JOY_LABEL[d]} · ${args[1] || 'edge'}`
      + (kinds.length ? ` · bound: ${kinds.join(', ')}` : ' · nothing bound');
  }
  if (e.event === 'touch') return 'capacitive touch pad' + (args.length ? ` ${args.join(' ')}` : ' (a tap: one line, no down/up)');
  if (e.event === 'rear') return 'rear button' + (args.length ? ` ${args.join(' ')}` : ' (a tap: one line, no down/up)');
  if (e.event === 'batt') return `battery ${args[0]}%${args[1] === '1' ? ', charging' : ''}`;
  return '—';
}

function renderEventFeed() {
  const body = $('ev-feed');
  body.textContent = '';
  const list = state.events.slice(-60).reverse();
  if (!list.length) {
    body.append(el('tr', {}, [el('td', { colspan: '4', class: 'miss', text: 'nothing yet' })]));
  }
  for (const e of list) {
    const t = new Date((Number(e.at) || 0) * 1000);
    const hh = Number.isFinite(t.getTime()) ? t.toTimeString().slice(0, 8) + '.' + String(t.getMilliseconds()).padStart(3, '0') : '—';
    body.append(el('tr', { 'data-source': e.source === 'device' ? 'device' : 'injected' }, [
      el('td', { text: hh }),
      el('td', {}, [el('span', { class: 'tag ' + (e.source === 'device' ? 'ok' : 'inj'), text: e.source === 'device' ? 'pad' : 'injected' })]),
      el('td', { text: e.line || `${e.event} ${(e.args || []).join(' ')}` }),
      el('td', { class: 'wrap', text: explainEvent(e) }),
    ]));
  }
  const last = state.events[state.events.length - 1];
  $('ev-last-what').textContent = last ? (last.line || last.event) : 'no events yet';
  $('ev-last-what').dataset.source = last ? (last.source === 'device' ? 'device' : 'injected') : '';
  $('ev-last-note').textContent = last
    ? `${last.source === 'device' ? 'from the pad' : 'injected by this page'} — ${explainEvent(last)}`
    : 'Press a key on the pad, or use a Test button in the Bindings tab.';
}

function renderEventState() {
  const seen = state.inputSeen === null ? state.status.input_events : state.inputSeen;
  const pill = $('ev-state');
  const warn = $('ev-warn');
  warn.textContent = '';
  if (state.daemonReachable === false) {
    pill.textContent = 'no daemon';
    warn.hidden = false;
    warn.append(el('li', { text: 'No daemon is answering, so there are no events to read and no way to inject one. Start it with ./.venv/bin/libremicro.' }));
    return;
  }
  if (seen) {
    pill.textContent = 'pad is sending events';
    warn.hidden = true;
    return;
  }
  pill.textContent = 'no real events yet';
  warn.hidden = false;
  warn.append(el('li', { text:
    'This firmware has never sent an input event. The build on the pad is LED-out only — flash '
    + 'firmware v2 to get key events (and set LM_ENABLE_UNVERIFIED_INPUTS for the encoder, touch '
    + 'pad and rear button, whose pins are not confirmed yet).' }));
  warn.append(el('li', { text:
    'Until then, Test in the Bindings tab injects events through POST /api/simulate. They run the '
    + 'daemon\'s real recogniser and dispatcher — hold and double timing included — so a binding '
    + 'that works when injected will work when the pad sends the same line.' }));
}

/* ===================================================== 17. chrome: top bar */

function setDaemonReachable(on) {
  if (state.daemonReachable === on) return;
  state.daemonReachable = on;
  renderTop();
}

function renderTop() {
  const conn = $('stat-conn');
  const dot = conn.querySelector('.dot');
  const label = conn.querySelector('.v');
  const s = state.status;
  if (state.daemonReachable === false) {
    dot.dataset.state = 'off';
    label.textContent = 'no daemon';
  } else if (state.daemonReachable === null) {
    dot.dataset.state = 'unknown';
    label.textContent = 'connecting…';
  } else if (s.connected) {
    dot.dataset.state = 'on';
    label.textContent = 'device connected';
  } else {
    dot.dataset.state = 'warn';
    label.textContent = 'daemon up, no device';
  }
  $('stat-port').textContent = s.port || '—';
  $('stat-profile').textContent = s.active_profile || state.config?.active_profile || '—';
  $('stat-mode').textContent = s.active_mode || 'none';
  if (s.battery && typeof s.battery === 'object' && Number.isFinite(Number(s.battery.percent))) {
    $('stat-batt').textContent = `${Math.round(s.battery.percent)}%${s.battery.charging ? ' · charging' : ''}`;
  } else {
    $('stat-batt').textContent = 'unavailable';
  }
  $('dirty-flag').hidden = !state.dirty;
  $('btn-save').disabled = !state.dirty;
  // The daemon reports whether a preview currently owns the strips (it expires on its own).
  const stop = $('btn-stop-preview');
  stop.classList.toggle('primary', !!s.previewing);
  stop.title = s.previewing ? 'A preview is driving the pad — revert to the config lighting' : 'Revert the pad to the config lighting';
}

function renderBanner() {
  const slot = $('banner-slot');
  // Hold the banner back until the first load settles, so it appears once instead of
  // flashing in for the starter config and then out again for the real one.
  if (!state.bootDone) return;
  slot.textContent = '';
  renderKeysBanner(slot);
  // The shipped strip mapping is confirmed hardware, so silence is the normal state: this warns
  // only when the config explicitly says "I overrode this and haven't checked it".
  if (mappingVerified()) return;
  const g = state.geom;
  const which = [];
  if (!g?.defaultedKeys) which.push('per-key');
  if (!g?.defaultedUg) which.push('underglow');
  const btn = el('button', { type: 'button', class: 'ghost small', text: 'Open identify sweep' });
  btn.addEventListener('click', () => { showTab('identify'); $('btn-id-start').focus(); });
  slot.append(el('div', { class: 'banner', role: 'note' }, [
    el('span', { class: 'icon', text: '!', 'aria-hidden': 'true' }),
    el('div', {}, [
      el('p', { html: '<strong>This config marks its LED index mapping unverified.</strong> <code>layout.verified</code> is <code>false</code>, so which strip index lights which LED may be wrong on this unit and spatial effects may land on the wrong pixels.' }),
      el('p', {
        text: which.length
          ? `The ${which.join(' and ')} mapping comes from this config rather than the confirmed default. Run the sweep to check it against the pad, or clear the override to fall back to the shipped wiring order.`
          : 'The mapping shown is the confirmed default wiring order; nothing in this config overrides it. Run the sweep to confirm it on this unit, then tick verified.',
      }),
      el('div', { class: 'b-actions' }, [btn]),
    ]),
  ]));
}

/* A shortcut binding on a machine with no helper, or no Accessibility trust, does nothing at all
 * and says nothing about it — the single worst failure mode in this app. So it gets a banner, not
 * a line in a panel, and it explains where the permission actually attaches. */
function renderKeysBanner(slot) {
  if (state.capsDismissed) return;
  const caps = state.status.keys;
  if (!caps || typeof caps !== 'object') return;
  const built = caps.built !== false && caps.helper !== null;
  const access = caps.accessibility;
  if (built && access === true) return;
  const users = countSynthBindings();

  const head = !built
    ? '<strong>Keyboard shortcuts cannot work yet: the native helper is not built.</strong>'
    : access === false
      ? '<strong>Keyboard shortcuts cannot work yet: the daemon is not trusted for Accessibility.</strong>'
      : '<strong>Keyboard synthesis may not work — Accessibility trust could not be confirmed.</strong>';
  const body = !built
    ? `The <code>shortcut</code>, <code>text</code> and media <code>action</code> bindings all go through <code>host/swift/lmkey</code>, which is not built. Until it is, they fail silently. ${caps.hint || 'Build it with: cd host/swift &amp;&amp; swiftc -O -o lmkey lmkey.swift'}`
    : 'macOS attributes Accessibility to the process that <em>launched</em> the daemon — your Terminal, iTerm, or the launchd job — <em>not</em> to the helper binary. Grant it there, in System Settings › Privacy &amp; Security › Accessibility, then restart the daemon. Synthesised keys are discarded silently until you do.';

  const go = el('button', { type: 'button', class: 'ghost small', text: 'Open bindings' });
  go.addEventListener('click', () => { showTab('bindings'); $('btn-caps-recheck').focus(); });
  const recheck = el('button', { type: 'button', class: 'ghost small', text: 'Re-check' });
  recheck.addEventListener('click', () => refreshStatus());
  const hide = el('button', { type: 'button', class: 'ghost small', text: 'Dismiss' });
  hide.addEventListener('click', () => { state.capsDismissed = true; renderBanner(); });

  slot.append(el('div', { class: 'banner', 'data-level': built && access !== false ? 'warn' : 'err', role: 'note' }, [
    el('span', { class: 'icon', text: '!', 'aria-hidden': 'true' }),
    el('div', {}, [
      el('p', { html: head }),
      el('p', { html: body }),
      users ? el('p', { text: `${users} binding${users === 1 ? '' : 's'} in this config depend on it.` }) : null,
      el('div', { class: 'b-actions' }, [go, recheck, hide]),
    ]),
  ]));
}

function renderStageNote() {
  const g = state.geom;
  const bits = [];
  const total = g.cells.length;
  const unstripped = g.keys.filter((k) => k.pos && k.strip === null).map((k) => k.index);
  const capCount = g.caps.length;
  const odd = g.rows.some((n, r) => (KEY_GRID_COLS[r] || []).length !== n);
  if (total !== KEY_COUNT) bits.push(`layout.key_rows sums to ${total}, not ${KEY_COUNT}.`);
  if (odd) bits.push('layout.key_rows does not match the faceplate, so those rows are drawn left-aligned rather than at confirmed grid columns.');
  if (unstripped.length) bits.push(`no strip index maps to key index ${unstripped.join(', ')} — check layout.key_positions.`);
  if (!bits.length) {
    bits.push(`${total} switches under ${capCount} keycaps on a 4×4 grid (rows of ${g.rows.join('/')}), `
      + '8 underglow LEDs tiling the whole perimeter an eighth each (the corner four wrap their '
      + 'corner), 3 PWM status LEDs left of the touch pad. '
      + 'Encoder, joystick and touch pad are drawn for orientation and carry no LED.');
  }
  $('stage-note').textContent = bits.join(' ');
}

function renderProfileSelect() {
  const sel = $('sel-profile');
  const names = Object.keys(state.config?.profiles || {});
  sel.textContent = '';
  for (const n of names) {
    const p = state.config.profiles[n];
    sel.append(el('option', { value: n, text: p?.label ? `${p.label} — ${n}` : n }));
  }
  if (!state.profile || !names.includes(state.profile)) {
    state.profile = state.config?.active_profile && names.includes(state.config.active_profile)
      ? state.config.active_profile
      : names[0] || null;
  }
  if (state.profile) sel.value = state.profile;
  $('btn-make-active').disabled = !state.profile || state.config?.active_profile === state.profile;
  renderScopeSelect();
}

/** The editing-scope picker: the profile's own layer, or one of its modes. */
function renderScopeSelect() {
  const sel = $('sel-scope');
  const names = Object.keys(profileModes());
  if (state.scope && !names.includes(state.scope)) state.scope = null;
  sel.textContent = '';
  sel.append(el('option', { value: '', text: 'profile default' }));
  for (const n of names) sel.append(el('option', { value: n, text: `mode: ${n}` }));
  sel.value = state.scope || '';
  sel.disabled = !names.length;
  sel.title = names.length
    ? 'A mode\'s keys, encoder and lighting override the profile\'s while it is active'
    : 'This profile has no modes — add one in the Profiles tab';
}

/** Re-render everything that reads the editing scope. */
function adoptScopeChange() {
  renderScopeSelect();
  renderColorPanel();
  renderEffectPanel();
  renderBindingsPanel();
  syncUnderglowEditor();
  syncStatusSliders();
  if (state.lastFrame) paint(state.lastFrame);
}

/* ===================================================== 18. tabs & the JSON */

const TABS = ['color', 'palette', 'effect', 'bindings', 'profiles', 'events', 'identify', 'config'];

function showTab(name) {
  for (const t of TABS) {
    const btn = $('tab-' + t), panel = $('panel-' + t);
    const on = t === name;
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
    btn.tabIndex = on ? 0 : -1;
    panel.hidden = !on;
  }
  prefs.write('tab', name);
  if (name === 'config') syncJson();
  if (name === 'palette') renderPalettePanel();
  if (name === 'effect') renderEffectPanel();
  if (name === 'identify') renderIdentifyPanel();
  if (name === 'bindings') renderBindingsPanel();
  if (name === 'profiles') renderProfilesPanel();
  if (name === 'events') { renderEventFeed(); renderEventState(); }
  // Leaving the Bindings tab must not leave a key recorder swallowing every keystroke.
  if (name !== 'bindings') stopRecording();
}

function syncJson() {
  if (!state.jsonStale) return;
  const ta = $('json-view');
  if (document.activeElement === ta) return; // don't clobber someone mid-edit
  ta.value = JSON.stringify(state.config, null, 2);
  state.jsonStale = false;
}

function showErrors(list) {
  const ul = $('cfg-errors');
  ul.textContent = '';
  const errs = (list || []).map((e) => (typeof e === 'string' ? e : JSON.stringify(e)));
  ul.hidden = errs.length === 0;
  for (const e of errs) ul.append(el('li', { text: e }));
}

/** Cheap local sanity checks. The daemon's PUT is the real validator. */
function localCheck(cfg) {
  const errs = [];
  if (!cfg || typeof cfg !== 'object') return ['config is not an object'];
  if (cfg.version !== 2) errs.push('version must be 2');
  if (!cfg.profiles || typeof cfg.profiles !== 'object' || !Object.keys(cfg.profiles).length) errs.push('at least one profile is required');
  for (const [n, p] of Object.entries(cfg.palettes || {})) {
    if (!p || !Array.isArray(p.stops) || !p.stops.length) { errs.push(`palette ${n}: needs at least one stop`); continue; }
    for (const s of p.stops) {
      if (!isHex6(s.color)) errs.push(`palette ${n}: colour "${s.color}" is not rrggbb`);
      if (!(Number(s.pos) >= 0 && Number(s.pos) <= 1)) errs.push(`palette ${n}: pos ${s.pos} outside 0..1`);
    }
  }
  for (const [n, p] of Object.entries(cfg.profiles || {})) {
    for (const k of p?.keys || []) {
      if (!Number.isInteger(k?.index) || k.index < 0 || k.index > 12) errs.push(`profile ${n}: key index ${k?.index} outside 0..12`);
      if (k?.color !== undefined && !isHex6(k.color)) errs.push(`profile ${n}: key ${k.index} colour "${k.color}" is not rrggbb`);
    }
    const e = p?.lighting?.effect;
    if (e && !EFFECT_NAMES.includes(e.name)) errs.push(`profile ${n}: effect name "${e.name}" is not in the schema enum`);
    checkTriggers(errs, cfg, `profile ${n}`, p);
    for (const [mn, m] of Object.entries(p?.modes || {})) {
      if (!m || typeof m !== 'object') { errs.push(`profile ${n}: mode ${mn} is not an object`); continue; }
      if (!m.encoder || typeof m.encoder !== 'object') errs.push(`profile ${n}: mode ${mn} must have an encoder object (the schema requires it)`);
      if (m.activate_key !== undefined && (!Number.isInteger(m.activate_key) || m.activate_key < 0 || m.activate_key > 12)) {
        errs.push(`profile ${n}: mode ${mn} activate_key ${m.activate_key} outside 0..12`);
      }
      if (m.flash !== undefined && !isHex6(m.flash)) errs.push(`profile ${n}: mode ${mn} flash "${m.flash}" is not rrggbb`);
      if (m.timeout_s !== undefined && !(Number(m.timeout_s) >= 1)) errs.push(`profile ${n}: mode ${mn} timeout_s must be at least 1 (omit it to stay until switched)`);
      checkTriggers(errs, cfg, `profile ${n} mode ${mn}`, m);
    }
  }
  return errs;
}

/** Bindings a document would be rejected for — one action key each, and a value in it. */
function checkTriggers(errs, cfg, where, owner) {
  const one = (label, b) => {
    if (!b || typeof b !== 'object') { errs.push(`${where}: ${label} is not a binding object`); return; }
    const present = BINDING_KEYS.filter((k) => b[k] !== undefined);
    if (present.length === 0) { errs.push(`${where}: ${label} has no action — a binding needs exactly one of ${BINDING_KEYS.join(', ')}`); return; }
    if (present.length > 1) { errs.push(`${where}: ${label} has ${present.length} actions (${present.join(', ')}) — the schema allows exactly one`); return; }
    const key = present[0];
    const v = b[key];
    if (typeof v !== 'string' || !v.trim()) { errs.push(`${where}: ${label} ${key} is empty`); return; }
    if (key === 'shortcut') {
      const p = parseSpec(v);
      if (!p.ok) errs.push(`${where}: ${label} shortcut "${v}" — ${p.error}`);
    }
    if (key === 'action' && !ACTION_ENUM.includes(v)) errs.push(`${where}: ${label} action "${v}" is not in the schema enum`);
    if (b.flash !== undefined && !isHex6(b.flash)) errs.push(`${where}: ${label} flash "${b.flash}" is not rrggbb`);
  };
  const scan = (label, t, kinds) => {
    if (t === undefined) return;
    if (!t || typeof t !== 'object') { errs.push(`${where}: ${label} is not an object`); return; }
    for (const [k, b] of Object.entries(t)) {
      if (!kinds.includes(k)) { errs.push(`${where}: ${label} has no trigger kind "${k}" (one of ${kinds.join(', ')})`); continue; }
      one(`${label}.${k}`, b);
    }
  };
  for (const k of owner?.keys || []) scan(`key ${k?.index}.on`, k?.on, KEY_TRIGGERS);
  scan('encoder', owner?.encoder, ENC_TRIGGERS);
  scan('touch', owner?.touch, KEY_TRIGGERS);
  scan('rear', owner?.rear, KEY_TRIGGERS);
  const joy = owner?.joystick;
  if (joy !== undefined) {
    if (!joy || typeof joy !== 'object') errs.push(`${where}: joystick is not an object`);
    else {
      for (const [d, t] of Object.entries(joy)) {
        if (!JOY_DIRS.includes(d)) errs.push(`${where}: joystick has no direction "${d}" (one of ${JOY_DIRS.join(', ')})`);
        else scan(`joystick.${d}`, t, KEY_TRIGGERS);
      }
    }
  }
}

/* ============================================== 19. load / save / transfer */

function adoptConfig(cfg, { fromDaemon = false } = {}) {
  state.config = cfg;
  state.loadedFromDaemon = fromDaemon;
  paletteRev++;
  palCache.clear();
  renderProfileSelect();
  buildGeometry();
  buildDevice();
  seedIdentifyMap();
  state.jsonStale = true;
  renderBanner();
  renderStageNote();
  renderColorPanel();
  renderPalettePanel();
  renderEffectPanel();
  renderIdentifyPanel();
  renderBindingsPanel();
  renderProfilesPanel();
  syncDeviceSliders();
  syncUnderglowEditor();
  syncStatusSliders();
  renderTop();
  if (state.lastFrame) paint(state.lastFrame);
}

function syncDeviceSliders() {
  const d = state.config?.device || {};
  $('rng-bright').value = String(clamp(Math.round(Number(d.brightness ?? 200)), 0, 255));
  $('out-bright').textContent = $('rng-bright').value;
  $('rng-fps').value = String(clamp(Math.round(Number(d.fps ?? 30)), 1, 60));
  $('out-fps').textContent = $('rng-fps').value + ' fps';
}

function syncUnderglowEditor() {
  const c = effectiveLighting().underglow;
  ceUnder.show(isHex6(c) ? c : '000000');
}

async function loadAll() {
  const [cfgRes, palRes, schemaRes] = await Promise.all([api.getConfig(), api.getPalettes(), api.getSchema()]);
  if (palRes.ok && palRes.data && typeof palRes.data === 'object') {
    // The daemon's corpus REPLACES the embedded one rather than merging with it: offering a
    // name the daemon cannot resolve would produce configs that silently render wrong.
    // The embedded set in this file is the offline fallback only.
    const usable = Object.fromEntries(
      Object.entries(palRes.data).filter(([, v]) => v && Array.isArray(v.stops) && v.stops.length),
    );
    if (Object.keys(usable).length) {
      state.builtins = usable;
      paletteRev++; palCache.clear();
    }
  }
  if (schemaRes.ok && schemaRes.data) state.schema = schemaRes.data;
  if (cfgRes.ok && cfgRes.data && typeof cfgRes.data === 'object') {
    adoptConfig(cfgRes.data, { fromDaemon: true });
    state.dirty = false;
    renderTop();
  } else {
    adoptConfig(clone(OFFLINE_CONFIG));
    state.dirty = false;
    renderTop();
    toast(cfgRes.reachable
      ? 'Daemon has no usable config — editing a local starter config'
      : 'No daemon: editing a local starter config. Design work and export still work.', 'warn', 6000);
  }
  state.bootDone = true;
  renderBanner();
  showErrors([]);
}

async function saveConfig() {
  const errs = localCheck(state.config);
  if (errs.length) { showErrors(errs); showTab('config'); toast('Config has problems — see the Config tab', 'err'); return; }
  const res = await api.putConfig(state.config);
  if (res.ok && res.data && res.data.ok === false) {
    showErrors(res.data.errors || ['daemon rejected the config']);
    showTab('config');
    toast('Daemon rejected the config', 'err');
    return;
  }
  if (!res.ok) {
    showErrors([res.error || 'save failed']);
    toast(res.reachable ? 'Save failed: ' + res.error : 'No daemon — use Export to keep your work', 'err', 6000);
    return;
  }
  showErrors([]);
  state.dirty = false;
  renderTop();
  toast('Saved', 'ok');
  refreshStatus();
}

function download(name, text) {
  const blob = new Blob([text], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = el('a', { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function doExport() {
  const stamp = new Date().toISOString().slice(0, 10);
  const res = await api.exportCfg();
  if (res.ok && res.data) {
    download(`libremicro-bundle-${stamp}.json`, JSON.stringify(res.data, null, 2));
    toast('Exported the daemon bundle', 'ok');
    return;
  }
  download(`libremicro-config-${stamp}.json`, JSON.stringify(state.config, null, 2));
  toast(res.reachable ? 'Daemon export failed — exported this page\'s config instead' : 'Exported this page\'s config (no daemon)', 'warn', 5000);
}

async function doImport(file) {
  let parsed;
  try { parsed = JSON.parse(await file.text()); }
  catch (e) { toast('That file is not valid JSON', 'err'); return; }

  // Accept either a bare config (version 2) or a wrapper with a .config member.
  const cfg = parsed && parsed.version === 2 ? parsed
    : parsed && parsed.config && parsed.config.version === 2 ? parsed.config
      : null;
  if (!cfg) { toast('No version 2 config found in that file', 'err'); return; }

  const errs = localCheck(cfg);
  if (errs.length) { showErrors(errs); showTab('config'); toast('Imported config has problems — not sent to the daemon', 'err'); return; }

  const res = await api.importCfg(parsed);
  adoptConfig(cfg);
  if (res.ok && res.data && res.data.ok === false) {
    showErrors(res.data.errors || ['daemon rejected the bundle']);
    state.dirty = true;
    toast('Loaded into the editor, but the daemon rejected it', 'warn', 6000);
  } else if (res.ok) {
    state.dirty = false;
    toast('Imported', 'ok');
    const again = await api.getConfig();
    if (again.ok && again.data) adoptConfig(again.data, { fromDaemon: true });
    refreshStatus();
  } else {
    state.dirty = true;
    toast(res.reachable ? 'Loaded into the editor; daemon import failed' : 'Loaded into the editor (no daemon)', 'warn', 5000);
  }
  renderTop();
}

/* ======================================================== 20. status poll */

let statusTimer = null;
let lastStatusSig = '';
async function refreshStatus() {
  const res = await api.getStatus();
  if (res.ok && res.data && typeof res.data === 'object') {
    const d = res.data;
    state.status = {
      connected: !!d.connected,
      port: d.port ?? null,
      active_profile: d.active_profile ?? null,
      active_mode: d.active_mode ?? null,
      battery: d.battery ?? null,
      previewing: !!d.previewing,
      input_events: d.input_events ?? null,
      keys: d.keys ?? null,
      // The capability answer from the pad. Its `events` list is which event kinds this build
      // actually emits, which is how the joystick cards can say a binding is inert on this build.
      firmware: d.firmware ?? null,
    };
    if (state.inputSeen === null && typeof d.input_events === 'boolean') state.inputSeen = d.input_events;
  } else if (!res.reachable) {
    state.status = { connected: false, port: null, active_profile: null, active_mode: null, battery: null, previewing: false, input_events: null, keys: null, firmware: null };
  }
  renderTop();
  renderCapsBox();
  renderEventState();
  // The status poll runs every 3 s, and both of these rebuild DOM that can hold focus — so they
  // only re-render when something they actually show has changed. Otherwise tabbing through the
  // banner's buttons or the profile list would lose focus every three seconds.
  const sig = JSON.stringify([state.status.keys, state.status.active_profile, state.status.active_mode, state.capsDismissed]);
  if (sig !== lastStatusSig) {
    lastStatusSig = sig;
    renderBanner();
    if ($('tab-profiles').getAttribute('aria-selected') === 'true') renderProfilesPanel();
  }
  clearTimeout(statusTimer);
  statusTimer = setTimeout(() => { refreshStatus(); }, state.daemonReachable ? 3000 : 8000);
}

/* ============================================================== 21. wiring */

function wireDeviceView() {
  const svg = $('device');
  const activate = (g) => {
    const zone = g.dataset.zone;
    const posKey = g.dataset.pos;
    if (state.identify.active && (zone === 'keys' || zone === 'underglow')) { recordIdentify(zone, posKey); return; }
    if (zone === 'status') { selectLed('status', Number(posKey), null); showTab('color'); return; }
    const i = indexAtPos(zone, posKey);
    if (i === null) { toast('That position has no LED index — check layout.key_rows', 'warn'); return; }
    // Clicking a key means "this one" in whichever editor is open: stay on Bindings if that is
    // where the work is, rather than yanking the panel out from under the pointer.
    const onBindings = $('tab-bindings').getAttribute('aria-selected') === 'true';
    if (zone === 'keys') state.bind = { control: 'key', index: i };
    selectLed(zone, i, posKey);
    if (onBindings && zone === 'keys') renderBindingsPanel(); else showTab('color');
  };

  svg.addEventListener('click', (ev) => {
    const g = ev.target.closest('.cell-g');
    if (g) { activate(g); return; }
    // Pointer shortcut only: the encoder, joystick and touch-pad ghosts are not LEDs, so they stay
    // out of the tab order and out of the accessible tree — the Bindings panel's control map is
    // the keyboard-reachable way to the same thing.
    const feat = ev.target.closest('.feat');
    if (!feat) return;
    for (const [kind, ref] of svgRefs.feat) {
      if (ref.g !== feat) continue;
      // The joystick binds per direction, so clicking the stick lands on a direction — the one
      // already being edited, else the first with something bound. The compass picks another.
      selectControl(kind, kind === 'joystick' ? joyDefaultDir() : 0);
      showTab('bindings');
      return;
    }
  });

  svg.addEventListener('keydown', (ev) => {
    const g = ev.target.closest && ev.target.closest('.cell-g');
    if (!g) return;
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); activate(g); return; }
    const dirs = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const d = dirs[ev.key];
    if (!d) return;
    ev.preventDefault();
    const zone = g.dataset.zone;
    if (zone === 'keys') {
      // Move across the 4x4 grid, skipping the slots the non-key controls occupy: left/right
      // steps to the next switch in the row, up/down to the nearest grid column in the next row.
      const [r, o] = g.dataset.pos.split(',').map(Number);
      const cells = state.geom.cells;
      const cur = cells.find((c) => c.row === r && c.col === o);
      if (!cur) return;
      let target = null;
      if (d[0]) {
        const inRow = cells.filter((c) => c.row === r).sort((a, b) => a.gcol - b.gcol);
        target = inRow[inRow.indexOf(cur) + d[0]] || null;
      } else {
        const nr = clamp(r + d[1], 0, state.geom.rows.length - 1);
        target = cells.filter((c) => c.row === nr).reduce((best, c) => (
          !best || Math.abs(c.gcol - cur.gcol) < Math.abs(best.gcol - cur.gcol) ? c : best), null);
      }
      if (target) svgRefs.keys.get(`${target.row},${target.col}`)?.g.focus();
    } else if (zone === 'underglow') {
      const [gx, gy] = g.dataset.pos.split(',').map(Number);
      const cur = RING_ORDER.findIndex((p) => p[0] === gx && p[1] === gy);
      const step = d[0] !== 0 ? d[0] : d[1];
      const next = RING_ORDER[(cur + step + UG_COUNT) % UG_COUNT];
      svgRefs.ug.get(next.join(','))?.g.focus();
    } else {
      // The status LEDs are a vertical stack, so up/down is the natural axis; left/right also
      // works rather than trapping focus.
      const i = clamp(Number(g.dataset.pos) + (d[1] || d[0]), 0, STATUS_COUNT - 1);
      svgRefs.status[i]?.g.focus();
    }
  });
}

function wireTabs() {
  const btns = TABS.map((t) => $('tab-' + t));
  btns.forEach((b, i) => {
    b.addEventListener('click', () => showTab(TABS[i]));
    b.addEventListener('keydown', (ev) => {
      const d = ev.key === 'ArrowRight' ? 1 : ev.key === 'ArrowLeft' ? -1 : ev.key === 'Home' ? -99 : ev.key === 'End' ? 99 : 0;
      if (!d) return;
      ev.preventDefault();
      const n = d === -99 ? 0 : d === 99 ? TABS.length - 1 : (i + d + TABS.length) % TABS.length;
      btns[n].focus();
      showTab(TABS[n]);
    });
  });
}

function wireColorPanel() {
  ceLed = new ColorEditor($('ce-led'), (hex) => {
    const sel = state.sel;
    if (!sel || sel.zone !== 'keys') return;
    const k = keyEntry(sel.index, true);
    if (!k) return;
    k.color = hex;
    touch('frame');
  }, 'Key colour');
  ceLed.setEnabled(false);

  ceUnder = new ColorEditor($('ce-underglow'), (hex) => {
    const light = ensureLighting();
    if (!light) return;
    light.underglow = hex;
    touch('frame');
  }, 'Underglow base');

  $('btn-apply-all-keys').addEventListener('click', () => {
    const sel = state.sel;
    if (!sel || sel.zone !== 'keys') return;
    const hex = keyEntry(sel.index)?.color;
    if (!isHex6(hex)) { toast('Give this key a colour first', 'warn'); return; }
    for (let i = 0; i < KEY_COUNT; i++) keyEntry(i, true).color = hex;
    touch('frame');
    toast('Applied to all 13 keys', 'ok');
  });

  $('btn-spread-palette').addEventListener('click', () => {
    const name = state.palSel || currentEffect()?.palette;
    const cp = compiledPalette(name);
    if (!cp) { toast('Pick a palette in the Palettes tab first', 'warn'); return; }
    const g = state.geom;
    for (const k of g.keys) keyEntry(k.index, true).color = rgbToHex(samplePalette(cp, k.pos ? k.u : 0.5));
    touch('frame');
    toast(`Spread “${name}” left to right across the keys`, 'ok');
    renderColorPanel();
  });

  $('btn-clear-key').addEventListener('click', () => {
    const sel = state.sel;
    if (!sel || sel.zone !== 'keys') return;
    const p = currentProfile();
    const k = keyEntry(sel.index);
    if (!k) return;
    delete k.color;
    // drop the entry entirely if it now carries nothing but its index
    if (Object.keys(k).length === 1) p.keys = p.keys.filter((x) => x !== k);
    touch('frame');
    renderColorPanel();
  });

  $('btn-clear-underglow').addEventListener('click', () => {
    const light = currentLighting();
    if (!light) return;
    delete light.underglow;
    touch('frame');
    syncUnderglowEditor();
  });

  $('rng-bright').addEventListener('input', (ev) => {
    if (!state.config.device) state.config.device = {};
    state.config.device.brightness = Number(ev.target.value);
    $('out-bright').textContent = ev.target.value;
    touch('frame');
  });
  $('rng-fps').addEventListener('input', (ev) => {
    if (!state.config.device) state.config.device = {};
    state.config.device.fps = Number(ev.target.value);
    $('out-fps').textContent = ev.target.value + ' fps';
    touch(null);
  });
}

function wirePalettePanel() {
  ceStop = new ColorEditor($('ce-stop'), (hex) => {
    mutatePalette((pal) => { if (pal.stops[state.palStop]) pal.stops[state.palStop].color = hex; });
  }, 'Stop colour');

  $('sel-palette').addEventListener('change', (ev) => {
    state.palSel = ev.target.value;
    state.palStop = 0;
    renderPalettePanel();
  });

  $('pal-name').addEventListener('change', (ev) => {
    const from = state.palSel, to = ev.target.value.trim();
    if (!from || !isOwnPalette(from)) return;
    if (!to) { ev.target.value = from; return; }
    if (to !== from && allPalettes()[to]) { toast(`“${to}” already exists`, 'warn'); ev.target.value = from; return; }
    if (to === from) return;
    const pals = state.config.palettes;
    pals[to] = pals[from];
    delete pals[from];
    // keep any effect that referenced the old name pointing at it
    for (const p of Object.values(state.config.profiles || {})) {
      if (p?.lighting?.effect?.palette === from) p.lighting.effect.palette = to;
      for (const m of Object.values(p?.modes || {})) if (m?.lighting?.effect?.palette === from) m.lighting.effect.palette = to;
    }
    state.palSel = to;
    paletteRev++;
    touch('effect');
    renderPalettePanel();
    renderEffectPanel();
  });

  $('pal-cyclic').addEventListener('change', (ev) => {
    mutatePalette((pal) => { pal.cyclic = ev.target.checked; });
  });

  $('rng-stoppos').addEventListener('input', (ev) => {
    const v = clamp(Number(ev.target.value), 0, 1);
    $('out-stoppos').textContent = v.toFixed(3);
    mutatePalette((pal) => { if (pal.stops[state.palStop]) pal.stops[state.palStop].pos = v; });
  });

  $('btn-stop-add').addEventListener('click', () => {
    mutatePalette((pal) => {
      const sorted = [...pal.stops].sort((a, b) => a.pos - b.pos);
      let gap = -1, at = 0.5;
      for (let i = 0; i < sorted.length - 1; i++) {
        const d = sorted[i + 1].pos - sorted[i].pos;
        if (d > gap) { gap = d; at = sorted[i].pos + d / 2; }
      }
      if (sorted.length === 1) at = clamp(sorted[0].pos + 0.25, 0, 1);
      const cp = compiledPalette(state.palSel);
      pal.stops.push({ pos: Math.round(at * 1000) / 1000, color: rgbToHex(samplePalette(cp, at)) });
      state.palStop = pal.stops.length - 1;
    });
  });

  $('btn-stop-del').addEventListener('click', () => {
    const pal = selectedPalette();
    if (!pal || pal.stops.length <= 1) { toast('A palette needs at least one stop', 'warn'); return; }
    mutatePalette((p) => { p.stops.splice(state.palStop, 1); state.palStop = Math.max(0, state.palStop - 1); });
  });

  $('btn-stop-even').addEventListener('click', () => {
    mutatePalette((pal) => {
      pal.stops.sort((a, b) => a.pos - b.pos);
      const n = pal.stops.length;
      pal.stops.forEach((s, i) => { s.pos = n === 1 ? 0 : Math.round((i / (n - 1)) * 1000) / 1000; });
    });
  });

  $('gradbar').addEventListener('click', (ev) => {
    if (ev.target.closest('.phandle')) return;
    if (!state.palSel || !isOwnPalette(state.palSel)) return;
    const r = ev.currentTarget.getBoundingClientRect();
    const t = clamp((ev.clientX - r.left) / r.width, 0, 1);
    mutatePalette((pal) => {
      const cp = compiledPalette(state.palSel);
      pal.stops.push({ pos: Math.round(t * 1000) / 1000, color: rgbToHex(samplePalette(cp, t)) });
      state.palStop = pal.stops.length - 1;
    });
  });

  $('btn-pal-new').addEventListener('click', () => {
    if (!state.config.palettes) state.config.palettes = {};
    const name = newPaletteName('custom');
    state.config.palettes[name] = {
      label: 'Custom',
      stops: [{ pos: 0, color: '000000' }, { pos: 0.5, color: '7aa2f7' }, { pos: 1, color: 'ffffff' }],
      cyclic: false,
    };
    state.palSel = name; state.palStop = 0;
    paletteRev++;
    touch('effect');
    renderPalettePanel();
    $('pal-name').focus();
  });

  $('btn-pal-dup').addEventListener('click', () => {
    const pal = selectedPalette();
    if (!pal) return;
    if (!state.config.palettes) state.config.palettes = {};
    const name = newPaletteName((state.palSel || 'palette') + '-copy');
    state.config.palettes[name] = clone({ label: (pal.label || state.palSel) + ' copy', stops: pal.stops, cyclic: !!pal.cyclic });
    state.palSel = name; state.palStop = 0;
    paletteRev++;
    touch('effect');
    renderPalettePanel();
  });

  $('btn-pal-del').addEventListener('click', () => {
    if (!state.palSel || !isOwnPalette(state.palSel)) return;
    const users = Object.entries(state.config.profiles || {}).filter(([, p]) => p?.lighting?.effect?.palette === state.palSel).map(([n]) => n);
    if (users.length && !confirm(`“${state.palSel}” is used by profile(s) ${users.join(', ')}. Delete anyway?`)) return;
    delete state.config.palettes[state.palSel];
    state.palSel = null;
    paletteRev++;
    touch('effect');
    renderPalettePanel();
    renderEffectPanel();
  });

  $('btn-pal-use').addEventListener('click', () => {
    if (!state.palSel) return;
    mutateEffect((e) => { e.palette = state.palSel; });
    showTab('effect');
    toast(`Effect now uses “${state.palSel}”`, 'ok');
  });
}

function wireEffectPanel() {
  fillSelect($('eff-name'), [''].concat(EFFECT_NAMES), ['(no effect)'].concat(EFFECT_NAMES));
  fillSelect($('eff-direction'), DIRECTIONS);
  fillSelect($('eff-target'), TARGETS);
  fillSelect($('eff-blend'), BLENDS);

  $('eff-name').addEventListener('change', (ev) => {
    if (!ev.target.value) {
      const light = currentLighting();
      if (light) delete light.effect;
      touch('frame');
      renderEffectPanel();
      return;
    }
    mutateEffect((e) => { e.name = ev.target.value; });
  });
  $('eff-palette').addEventListener('change', (ev) => mutateEffect((e) => {
    if (ev.target.value) e.palette = ev.target.value; else delete e.palette;
  }));
  $('eff-speed').addEventListener('input', (ev) => {
    $('out-speed').textContent = Number(ev.target.value).toFixed(2) + ' cyc/s';
    mutateEffect((e) => { e.speed = Number(ev.target.value); });
  });
  $('eff-intensity').addEventListener('input', (ev) => {
    $('out-intensity').textContent = Number(ev.target.value).toFixed(2);
    mutateEffect((e) => { e.intensity = Number(ev.target.value); });
  });
  $('eff-direction').addEventListener('change', (ev) => mutateEffect((e) => { e.direction = ev.target.value; }));
  $('eff-target').addEventListener('change', (ev) => mutateEffect((e) => { e.target = ev.target.value; }));
  $('eff-blend').addEventListener('change', (ev) => mutateEffect((e) => { e.blend = ev.target.value; }));
  $('eff-reverse').addEventListener('change', (ev) => mutateEffect((e) => { e.reverse = ev.target.checked; }));

  $('btn-eff-send').addEventListener('click', async () => {
    const eff = currentEffect();
    if (!eff) { toast('No effect to send', 'warn'); return; }
    const res = await api.previewEffect(normalisedEffect(eff));
    if (res.ok) toast('Effect sent to the device', 'ok');
    else toast(res.reachable ? 'Daemon refused the effect: ' + res.error : 'No daemon — client-side preview only', 'warn');
  });

  $('btn-eff-remove').addEventListener('click', () => {
    const light = currentLighting();
    if (!light) return;
    delete light.effect;
    touch('frame');
    renderEffectPanel();
  });
}

function wireIdentifyPanel() {
  document.querySelectorAll('input[name="id-target"]').forEach((r) => {
    r.addEventListener('change', () => {
      if (!r.checked) return;
      state.identify.target = r.value;
      state.identify.index = 0;
      if (state.identify.active) runIdentify(); else renderIdentifyPanel();
    });
  });

  $('btn-id-start').addEventListener('click', () => {
    const id = state.identify;
    id.active = !id.active;
    id.index = 0;
    identifyWarned = false;
    if (id.active) {
      toast(`Sweeping ${id.target}: click the position that lights up on the pad`, 'info', 5000);
      runIdentify();
    } else {
      renderIdentifyPanel();
      if (state.lastFrame) paint(state.lastFrame);
      api.previewStop();
    }
  });
  $('btn-id-prev').addEventListener('click', () => identifyStep(-1));
  $('btn-id-next').addEventListener('click', () => identifyStep(1));
  $('btn-id-skip').addEventListener('click', () => {
    state.identify.map[state.identify.target][state.identify.index] = null;
    identifyStep(1);
  });
  $('btn-id-defaults').addEventListener('click', () => {
    const g = state.geom;
    state.identify.map[state.identify.target] = (state.identify.target === 'keys' ? g.defKeys : g.defUg)
      .slice(0, identifyCount()).map((p) => (p ? p.slice() : null));
    renderIdentifyPanel();
    toast('Reset to the confirmed default wiring order', 'ok');
  });
  $('btn-id-clear').addEventListener('click', () => {
    state.identify.map[state.identify.target] = new Array(identifyCount()).fill(null);
    renderIdentifyPanel();
  });
  $('btn-id-write').addEventListener('click', writeMapping);
  $('chk-verified').addEventListener('change', () => {
    // only meaningful once written; keep the checkbox as intent until then
    renderIdentifyPanel();
  });
}

function wireBindingsPanel() {
  /* Arrow keys walk the control map, exactly as they walk the board: focus moves, Enter/Space
   * selects. Two independent grids live in here — the pad map (data-nr / data-nc, in grid rows and
   * columns, with the off-grid rear button one row below the last) and the joystick compass
   * (data-jr / data-jc) — and a step stays inside whichever one the focus is in. */
  $('bind-controls').addEventListener('keydown', (ev) => {
    const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[ev.key];
    if (!step) return;
    const cur = ev.target.closest && ev.target.closest('[data-nr], [data-jr]');
    if (!cur) return;
    const [kr, kc] = cur.hasAttribute('data-jr') ? ['jr', 'jc'] : ['nr', 'nc'];
    const all = [...$('bind-controls').querySelectorAll(`[data-${kr}]`)];
    const at = (e) => [Number(e.dataset[kr]), Number(e.dataset[kc])];
    const [r, c] = at(cur);
    const nearestCol = (list) => list.sort((a, b) => Math.abs(at(a)[1] - c) - Math.abs(at(b)[1] - c))[0] || null;
    let target = null;
    if (step[0]) {
      // Along the row to the next occupied column — which is what steps between the two halves of
      // the wide cap, since they are two columns of one cap.
      target = nearestCol(all.filter((e) => at(e)[0] === r && Math.sign(at(e)[1] - c) === step[0]));
    } else {
      const rows = [...new Set(all.map((e) => at(e)[0]))]
        .filter((rr) => Math.sign(rr - r) === step[1])
        .sort((a, b) => Math.abs(a - r) - Math.abs(b - r));
      // Straight ahead wins: the nearest row in that direction that HAS this column. That is what
      // takes north to south across the compass's empty middle instead of sliding off to west, and
      // on the pad map it steps over the slot a non-key control holds rather than veering.
      for (const rr of rows) {
        const ahead = all.find((e) => at(e)[0] === rr && at(e)[1] === c);
        if (ahead) { target = ahead; break; }
      }
      if (!target && rows.length) target = nearestCol(all.filter((e) => at(e)[0] === rows[0]));
    }
    if (!target) return;
    ev.preventDefault();
    rove(all, target);
    target.focus();
  });

  $('bind-label').addEventListener('input', (ev) => {
    const { control, index } = state.bind;
    if (control !== 'key') return;
    const v = ev.target.value;
    if (v) keyEntry(index, true).label = v;
    else { const k = keyEntry(index); if (k) { delete k.label; pruneKeyEntry(index); } }
    touch(null);
    renderControlPicker();
    if (state.lastFrame) paint(state.lastFrame);
  });

  $('btn-bind-clear').addEventListener('click', () => {
    const { control, index } = state.bind;
    const t = triggersFor(control, index);
    if (!t || !Object.keys(t).length) { toast('Nothing bound here already', 'warn'); return; }
    if (!confirm(`Remove every binding on ${controlName(control, index).toLowerCase()}?`)) return;
    for (const k of Object.keys(t)) delete t[k];
    cleanupTriggers(control, index);
    touch(null);
    renderBindingsPanel();
  });

  $('btn-caps-recheck').addEventListener('click', () => { refreshStatus(); toast('Asked the daemon to re-check the helper', 'info'); });

  $('rng-hold').addEventListener('input', (ev) => {
    if (!state.config.device) state.config.device = {};
    state.config.device.hold_ms = Number(ev.target.value);
    $('out-hold').textContent = ev.target.value + ' ms';
    touch(null);
    refreshTriggerNotes();
  });
  $('rng-double').addEventListener('input', (ev) => {
    if (!state.config.device) state.config.device = {};
    state.config.device.double_ms = Number(ev.target.value);
    $('out-double').textContent = ev.target.value + ' ms';
    touch(null);
    refreshTriggerNotes();
  });
}

function wireProfilesPanel() {
  $('btn-prof-new').addEventListener('click', () => {
    const name = uniqueName('profile', profileNames());
    state.config.profiles[name] = { label: 'New profile' };
    setProfileEditing(name);
    touch(null);
    renderProfilesPanel();
    $('prof-key').focus();
    $('prof-key').select();
  });
  $('btn-prof-dup').addEventListener('click', () => {
    const p = currentProfile();
    if (!p) return;
    const name = uniqueName((state.profile || 'profile') + '-copy', profileNames());
    state.config.profiles[name] = clone(p);
    if (state.config.profiles[name].label) state.config.profiles[name].label += ' copy';
    setProfileEditing(name);
    touch(null);
    renderProfilesPanel();
  });
  for (const [id, which] of [['btn-prof-next', 'next'], ['btn-prof-prev', 'prev']]) {
    $(id).addEventListener('click', async () => {
      const res = await api.setProfile(which);
      if (res.ok && res.data && res.data.ok !== false) { toast(`Daemon switched to “${res.data.active_profile}”`, 'ok'); refreshStatus(); }
      else toast(res.reachable ? 'Daemon refused: ' + ((res.data && (res.data.errors || []).join('; ')) || res.error) : 'No daemon — nothing to switch', 'warn');
    });
  }

  $('prof-key').addEventListener('change', (ev) => {
    const to = ev.target.value.trim();
    if (!to || !state.profile) { ev.target.value = state.profile || ''; return; }
    if (!renameProfile(state.profile, to)) { ev.target.value = state.profile; return; }
    renderProfileSelect();
    renderProfilesPanel();
    renderBindingsPanel();
  });
  $('prof-label').addEventListener('input', (ev) => {
    const p = currentProfile();
    if (!p) return;
    if (ev.target.value) p.label = ev.target.value; else delete p.label;
    touch(null);
    renderProfileSelect();
  });
  $('prof-app').addEventListener('input', (ev) => {
    const p = currentProfile();
    if (!p) return;
    if (ev.target.value.trim()) p.auto_activate_app = ev.target.value.trim(); else delete p.auto_activate_app;
    touch(null);
  });

  $('btn-mode-new').addEventListener('click', () => {
    const p = currentProfile();
    if (!p) return;
    if (!p.modes || typeof p.modes !== 'object') p.modes = {};
    const name = uniqueName('mode', Object.keys(p.modes));
    // `encoder` is required by the schema, so a new mode gets one even though it is empty.
    p.modes[name] = { encoder: {} };
    state.modeSel = name;
    touch(null);
    renderScopeSelect();
    renderModesPanel();
    $('mode-key').focus();
    $('mode-key').select();
  });
  $('btn-mode-clear').addEventListener('click', async () => {
    const res = await api.setMode(null);
    if (res.ok && res.data && res.data.ok !== false) { toast('Daemon left the active mode', 'ok'); refreshStatus(); }
    else toast(res.reachable ? 'Daemon refused: ' + ((res.data && (res.data.errors || []).join('; ')) || res.error) : 'No daemon', 'warn');
  });
  $('btn-mode-activate').addEventListener('click', async () => {
    if (!state.modeSel) return;
    const res = await api.setMode(state.modeSel);
    if (res.ok && res.data && res.data.ok !== false) { toast(`Daemon activated mode “${res.data.active_mode}”`, 'ok'); refreshStatus(); }
    else toast(res.reachable
      ? 'Daemon refused: ' + ((res.data && (res.data.errors || []).join('; ')) || res.error) + ' — a mode only exists for the daemon once the config is saved'
      : 'No daemon', 'warn', 6000);
  });
  $('btn-mode-bind').addEventListener('click', () => {
    if (!state.modeSel) return;
    state.scope = state.modeSel;
    state.bind = { control: 'encoder', index: 0 };
    adoptScopeChange();
    showTab('bindings');
  });
  $('btn-mode-light').addEventListener('click', () => {
    if (!state.modeSel) return;
    state.scope = state.modeSel;
    adoptScopeChange();
    showTab('effect');
  });

  $('mode-key').addEventListener('change', (ev) => {
    const from = state.modeSel, to = ev.target.value.trim();
    const modes = currentProfile()?.modes;
    if (!from || !to || !modes) { ev.target.value = from || ''; return; }
    if (to !== from && modes[to]) { toast(`A mode called “${to}” already exists`, 'warn'); ev.target.value = from; return; }
    if (to === from) return;
    const next = {};
    for (const n of Object.keys(modes)) next[n === from ? to : n] = modes[n];
    currentProfile().modes = next;
    // keep bindings that activate it pointing at it
    for (const owner of [currentProfile(), ...Object.values(next)]) {
      const fix = (t) => { for (const b of Object.values(t || {})) if (b && b.mode === from) b.mode = to; };
      for (const k of owner?.keys || []) fix(k?.on);
      fix(owner?.encoder); fix(owner?.touch); fix(owner?.rear);
    }
    if (state.scope === from) state.scope = to;
    state.modeSel = to;
    touch(null);
    renderScopeSelect();
    renderModesPanel();
    renderBindingsPanel();
  });
  $('mode-actkey').addEventListener('change', (ev) => mutateMode((m) => {
    if (ev.target.value === '') delete m.activate_key; else m.activate_key = Number(ev.target.value);
  }));
  $('rng-modeto').addEventListener('input', (ev) => {
    const v = Number(ev.target.value);
    $('out-modeto').textContent = v ? `${v} s of encoder silence` : 'never (stays until switched)';
    const m = state.modeSel ? profileModes()[state.modeSel] : null;
    if (!m) return;
    // The schema's minimum is 1; 0 is this slider's way of saying "omit it".
    if (v < 1) delete m.timeout_s; else m.timeout_s = v;
    touch(null);
  });
  $('mode-flash-on').addEventListener('change', (ev) => mutateMode((m) => {
    if (ev.target.checked) m.flash = $('mode-flash-hex').value.replace('#', '').toLowerCase() || 'ffffff';
    else delete m.flash;
  }));
  $('mode-flash').addEventListener('input', (ev) => mutateMode((m) => { m.flash = ev.target.value.replace('#', '').toLowerCase(); }));
  $('mode-flash-hex').addEventListener('input', (ev) => {
    const v = ev.target.value.replace('#', '').trim().toLowerCase();
    if (!isHex6(v)) return;
    const m = state.modeSel ? profileModes()[state.modeSel] : null;
    if (!m) return;
    m.flash = v;
    $('mode-flash').value = '#' + v;
    touch(null);
  });
}

function wireEventsPanel() {
  $('chk-ev-poll').addEventListener('change', (ev) => {
    state.evPoll = ev.target.checked;
    prefs.write('evPoll', state.evPoll);
    if (state.evPoll) pollEvents(); else clearTimeout(eventTimer);
  });
  $('chk-ev-flash').addEventListener('change', (ev) => {
    state.evFlash = ev.target.checked;
    prefs.write('evFlash', state.evFlash);
  });
  $('btn-ev-clear').addEventListener('click', () => {
    state.events = [];
    state.hits.clear();
    renderEventFeed();
  });
}

function wireConfigPanel() {
  $('btn-export').addEventListener('click', () => { doExport(); });
  $('btn-import').addEventListener('click', () => $('file-import').click());
  $('file-import').addEventListener('change', (ev) => {
    const f = ev.target.files && ev.target.files[0];
    ev.target.value = '';
    if (f) doImport(f);
  });
  $('btn-json-apply').addEventListener('click', () => {
    let parsed;
    try { parsed = JSON.parse($('json-view').value); }
    catch (e) { showErrors(['not valid JSON: ' + e.message]); toast('That JSON does not parse', 'err'); return; }
    const errs = localCheck(parsed);
    if (errs.length) { showErrors(errs); toast('Fix the listed problems first', 'err'); return; }
    showErrors([]);
    adoptConfig(parsed);
    state.dirty = true;
    renderTop();
    toast('Applied — remember to Save', 'ok');
  });
}

function wireChrome() {
  $('btn-save').addEventListener('click', () => { saveConfig(); });
  $('btn-reload').addEventListener('click', () => {
    if (state.dirty && !confirm('Discard unsaved changes and reload from the daemon?')) return;
    loadAll();
  });
  $('sel-profile').addEventListener('change', (ev) => {
    state.profile = ev.target.value;
    state.sel = null;
    state.scope = null;
    state.modeSel = null;
    renderProfileSelect();
    adoptScopeChange();
    renderProfilesPanel();
    renderTop();
    touch(null);
  });
  $('sel-scope').addEventListener('change', (ev) => {
    state.scope = ev.target.value || null;
    adoptScopeChange();
    toast(state.scope
      ? `Editing mode “${state.scope}” — keys, encoder and lighting here override the profile's while it is active`
      : 'Editing the profile\'s own layer', 'info', 4200);
  });
  $('btn-make-active').addEventListener('click', () => {
    if (!state.profile) return;
    state.config.active_profile = state.profile;
    renderProfileSelect();
    touch(null);
    toast(`active_profile set to “${state.profile}”`, 'ok');
  });

  $('chk-indices').addEventListener('change', (ev) => { prefs.write('showIdx', ev.target.checked); if (state.lastFrame) paint(state.lastFrame); });
  $('chk-labels').addEventListener('change', (ev) => { prefs.write('showLab', ev.target.checked); if (state.lastFrame) paint(state.lastFrame); });
  /* Where the board's picture comes from: device / preview / off. Changing it kicks the frame poll
   * rather than waiting out the current back-off, so picking "Device" is answered immediately. */
  for (const r of document.querySelectorAll('input[name="view-source"]')) {
    r.addEventListener('change', () => {
      if (!r.checked) return;
      state.view.source = r.value;
      prefs.write('viewSource', state.view.source);
      renderViewSource();
      pollFrame();
    });
  }

  $('chk-3d').addEventListener('change', (ev) => {
    state.view.threeD = ev.target.checked;
    prefs.write('view3d', state.view.threeD);
    applyViewMode();
    if (state.lastFrame) paint(state.lastFrame);
  });

  for (const b of document.querySelectorAll('#dv-cam [data-cam]')) {
    b.addEventListener('click', () => {
      Object.assign(R3.cam, CAM_PRESETS[b.dataset.cam] || CAM_PRESETS.three);
      prefs.write('cam', { ...R3.cam });
      if (view3dActive() && state.lastFrame) draw3d(state.lastFrame, false);
    });
  }

  /* Focus inside the SVG is the ONLY focus there is in the 3D view — the canvas is aria-hidden and
   * the focusable cells are the invisible SVG groups underneath. Tracking it here is what lets the
   * 3D scene draw a ring for the focused cap, so tabbing stays visible. */
  const dev = $('device');
  dev.addEventListener('focusin', (ev) => {
    const g = ev.target.closest && ev.target.closest('.cell-g');
    if (!g) return;
    const zone = g.dataset.zone;
    const index = zone === 'status' ? Number(g.dataset.pos) : indexAtPos(zone, g.dataset.pos);
    state.view.focusLed = index === null ? null : { zone, index };
  });
  dev.addEventListener('focusout', () => { state.view.focusLed = null; });

  // The 3D scene reads its board / desk / accent colours out of the stylesheet, so a theme change
  // has to drop the cached ones.
  const mq = window.matchMedia('(prefers-color-scheme: light)');
  const dropTheme = () => { R3.theme = null; };
  if (mq.addEventListener) mq.addEventListener('change', dropTheme); else if (mq.addListener) mq.addListener(dropTheme);
  window.addEventListener('resize', () => { if (view3dActive() && state.lastFrame) draw3d(state.lastFrame, false); });

  /* Back off to almost nothing when nobody is looking. Two independent reasons: the whole tab is
   * hidden, and the board has been scrolled out of view — at 900 px the layout is one column, so
   * working in the editor puts the device view off screen for minutes at a time and there is no
   * reason to make the daemon compose a frame 18 times a second for it. */
  document.addEventListener('visibilitychange', () => { if (!document.hidden) pollFrame(); });
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver((entries) => {
      const on = entries.some((e) => e.isIntersecting);
      if (on === state.view.onscreen) return;
      state.view.onscreen = on;
      if (on) pollFrame();
    }, { threshold: 0.02 });
    io.observe($('device'));
  }
  $('chk-live').addEventListener('change', (ev) => {
    setLive(ev.target.checked, ev.target.checked ? 'Live preview on — edits stream to the device' : null);
  });
  $('btn-stop-preview').addEventListener('click', stopPreview);

  document.addEventListener('keydown', (ev) => {
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 's') { ev.preventDefault(); saveConfig(); }
  });
  window.addEventListener('beforeunload', (ev) => {
    if (!state.dirty) return;
    ev.preventDefault();
    ev.returnValue = '';
  });
  window.addEventListener('unhandledrejection', (ev) => {
    console.error('unhandled rejection', ev.reason);
    ev.preventDefault();
    toast('Something failed in the background — see the console', 'err');
  });
}

/* ================================================================ 22. init */

function init() {
  $('chk-indices').checked = prefs.read('showIdx', true);
  $('chk-labels').checked = prefs.read('showLab', false);

  /* 3D is OFF by default, and it is a toggle. The 3D view is the better picture of what the pad
   * will physically look like, but the flat view is the better instrument: it is what you aim at
   * when binding keys and reading indices, it needs no GPU, and it is the one that cannot fail.
   * A studio's default should be the dependable instrument with the nicer picture one click away,
   * not the other way round — and the choice persists, so anyone who prefers 3D sets it once.
   * The identify sweep forces the flat view for its duration whatever this says. */
  state.view.threeD = prefs.read('view3d', false);
  $('chk-3d').checked = state.view.threeD;
  const src = prefs.read('viewSource', 'device');
  // 'auto' is the name an earlier build used for what is now just 'device'.
  state.view.source = ['device', 'preview', 'off'].includes(src) ? src : 'device';
  const radio = document.querySelector(`input[name="view-source"][value="${state.view.source}"]`);
  if (radio) radio.checked = true;
  const cam = prefs.read('cam', null);
  if (cam && Number.isFinite(cam.yaw) && Number.isFinite(cam.pitch)) {
    R3.cam.yaw = cam.yaw;
    R3.cam.pitch = clamp(cam.pitch, 0.12, 1.5533);
    // An earlier build stored an absolute distance; anything without a zoom just gets the default.
    if (Number.isFinite(cam.zoom)) R3.cam.zoom = clamp(cam.zoom, 0.55, 2.4);
  }
  state.evPoll = prefs.read('evPoll', true);
  state.evFlash = prefs.read('evFlash', true);
  $('chk-ev-poll').checked = state.evPoll;
  $('chk-ev-flash').checked = state.evFlash;

  wireTabs();
  wireColorPanel();
  wirePalettePanel();
  wireEffectPanel();
  wireBindingsPanel();
  wireProfilesPanel();
  wireEventsPanel();
  wireIdentifyPanel();
  wireConfigPanel();
  wireChrome();
  wireDeviceView();

  // Something sensible on screen before the first fetch resolves: no empty frame, no jump.
  adoptConfig(clone(OFFLINE_CONFIG));
  state.dirty = false;
  renderTop();
  applyViewMode();
  renderViewSource();

  const tab = prefs.read('tab', 'color');
  showTab(TABS.includes(tab) ? tab : 'color');

  requestAnimationFrame(tick);
  loadAll();
  refreshStatus();
  pollEvents();
  pollFrame();
}

init();
