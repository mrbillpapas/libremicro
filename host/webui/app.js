/* LibreMicro Lighting Studio — Phase 1 web UI.
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

/* The 3x3-minus-centre underglow ring, listed clockwise from the top-left corner.
 * This order is also the PROVISIONAL strip-index -> position guess (index i lights ring[i])
 * and the traversal order used by ring effects. Unverified: see docs/HARDWARE.md. */
const RING_ORDER = [[0, 0], [1, 0], [2, 0], [2, 1], [2, 2], [1, 2], [0, 2], [0, 1]];

const EFFECT_NAMES = ['solid', 'gradient', 'rainbow', 'breathe', 'chase', 'ripple', 'sparkle', 'wipe', 'comet', 'off'];
const DIRECTIONS = ['horizontal', 'vertical', 'radial', 'ring'];
const TARGETS = ['keys', 'underglow', 'all'];
const BLENDS = ['replace', 'multiply', 'screen', 'overlay'];

const EFFECT_DEFAULTS = { speed: 0.3, intensity: 0.5, direction: 'horizontal', reverse: false, target: 'all', blend: 'replace' };

/* SVG geometry. The underglow is drawn as a frame of 8 tiles in a 3x3 grid whose centre cell
 * holds the key cluster — the bands are deliberately narrower than a true third so the keys
 * fit inside without covering the ring. Grid topology (x,y in 0..2) is what the config stores. */
const GEO = {
  board: { x: 8, y: 8, w: 384, h: 324, r: 18 },
  xEdges: [8, 66, 334, 392],
  yEdges: [8, 58, 282, 332],
  keyArea: { x: 66, y: 58, w: 268, h: 224 },
  // Sized so the key cluster leaves the dashed "no centre LED" outline visible around it.
  key: { w: 52, h: 40, gap: 9, r: 8 },
  status: { y: 344, h: 24, w: 44, gap: 14, r: 6 },
  vbW: 400, vbH: 386,
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
    // any static file server on the port reads as a healthy daemon.
    const isDaemon = res.status !== 404;
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
  previewFrame: (f) => req('POST', '/api/preview/frame', f),
  previewEffect: (e) => req('POST', '/api/preview/effect', { effect: e }),
  previewStop: () => req('POST', '/api/preview/stop'),
  identify: (target, index) => req('POST', '/api/identify', { target, index }),
  exportCfg: () => req('GET', '/api/export'),
  importCfg: (b) => req('POST', '/api/import', b),
};

/* =============================================================== 6. state */

const OFFLINE_CONFIG = {
  version: 2,
  device: { port: 'auto', brightness: 200, fps: 30 },
  layout: { key_rows: DEFAULT_KEY_ROWS.slice(), verified: false },
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
  status: { connected: false, port: null, active_profile: null, active_mode: null, battery: null, previewing: false },
  loadedFromDaemon: false,
  dirty: false,
  profile: null,                 // profile name being edited
  sel: null,                     // {zone:'keys'|'underglow'|'status', index, pos}
  palSel: null,                  // palette name selected in the designer
  palStop: 0,
  geom: null,
  geomSig: '',
  anim: { playing: true, t: 0, lastTs: 0, lastPaint: 0 },
  live: false,
  identify: { active: false, target: 'keys', index: 0, map: { keys: [], underglow: [] } },
  lastFrame: null,
  jsonStale: true,
  previewChannel: null,          // which preview endpoint currently owns the device
  lastEffectPush: 0,
};

const currentProfile = () => (state.config?.profiles || {})[state.profile] || null;
const currentLighting = () => currentProfile()?.lighting || null;
const currentEffect = () => currentLighting()?.effect || null;
const deviceFps = () => clamp(Number(state.config?.device?.fps) || 30, 1, 60);

function ensureLighting() {
  const p = currentProfile();
  if (!p) return null;
  if (!p.lighting || typeof p.lighting !== 'object') p.lighting = {};
  return p.lighting;
}

function keyEntry(index, create = false) {
  const p = currentProfile();
  if (!p) return null;
  if (!Array.isArray(p.keys)) { if (!create) return null; p.keys = []; }
  let k = p.keys.find((x) => x && x.index === index);
  if (!k && create) { k = { index }; p.keys.push(k); p.keys.sort((a, b) => a.index - b.index); }
  return k || null;
}

/* ============================================================ 7. geometry */

function keyRows() {
  const r = state.config?.layout?.key_rows;
  return Array.isArray(r) && r.length && r.every((n) => Number.isInteger(n) && n > 0) ? r : DEFAULT_KEY_ROWS;
}

function readPositions(arr, count, fallback, max) {
  let provisional = false;
  const out = [];
  for (let i = 0; i < count; i++) {
    const v = Array.isArray(arr) ? arr[i] : undefined;
    const good = Array.isArray(v) && v.length === 2 && v.every((n) => Number.isInteger(n) && n >= 0 && (max === undefined || n <= max));
    if (good) out.push([v[0], v[1]]);
    else { out.push(fallback[i] ? fallback[i].slice() : null); provisional = true; }
  }
  return { positions: out, provisional };
}

function buildGeometry() {
  const rows = keyRows();
  const { keyArea, key } = GEO;
  const maxCols = Math.max(...rows);

  // Physical key cells. Short rows (the 2-key and 3-key rows) are laid out CENTRED, which is a
  // provisional choice: which matrix columns those rows populate is not verified.
  const align = prefs.read('shortRowAlign', 'center');
  const cells = [];
  const rowH = key.h, rowGap = key.gap;
  const blockH = rows.length * rowH + (rows.length - 1) * rowGap;
  const y0 = keyArea.y + (keyArea.h - blockH) / 2;
  rows.forEach((n, r) => {
    const rowW = n * key.w + (n - 1) * key.gap;
    const fullW = maxCols * key.w + (maxCols - 1) * key.gap;
    const pad = fullW - rowW;
    const offset = align === 'left' ? 0 : align === 'right' ? pad : pad / 2;
    const x0 = keyArea.x + (keyArea.w - fullW) / 2 + offset;
    for (let c = 0; c < n; c++) {
      const x = x0 + c * (key.w + key.gap);
      cells.push({ row: r, col: c, x, y: y0 + r * (rowH + rowGap), w: key.w, h: rowH, cx: x + key.w / 2, cy: y0 + r * (rowH + rowGap) + rowH / 2 });
    }
  });

  // Underglow cells: the 8 populated positions of the 3x3 grid, centre excluded.
  const ugCells = RING_ORDER.map(([gx, gy], ring) => {
    const x = GEO.xEdges[gx], w = GEO.xEdges[gx + 1] - GEO.xEdges[gx];
    const y = GEO.yEdges[gy], h = GEO.yEdges[gy + 1] - GEO.yEdges[gy];
    return { gx, gy, ring, x: x + 3, y: y + 3, w: w - 6, h: h - 6, cx: x + w / 2, cy: y + h / 2 };
  });

  // index -> position mappings, from config where present, otherwise provisional defaults
  const provKeys = cells.map((c) => [c.row, c.col]);           // strip order == reading order
  const provUg = RING_ORDER.map((p) => p.slice());             // strip order == clockwise ring
  const kp = readPositions(state.config?.layout?.key_positions, KEY_COUNT, provKeys);
  const up = readPositions(state.config?.layout?.underglow_positions, UG_COUNT, provUg, 2);

  const cellAt = (r, c) => cells.find((x) => x.row === r && x.col === c) || null;
  const ugAt = (gx, gy) => ugCells.find((x) => x.gx === gx && x.gy === gy) || null;

  const cx0 = GEO.board.x + GEO.board.w / 2, cy0 = GEO.board.y + GEO.board.h / 2;
  const maxR = Math.hypot(GEO.board.w / 2, GEO.board.h / 2);
  const spatial = (cell) => {
    const dx = cell.cx - cx0, dy = cell.cy - cy0;
    let ang = Math.atan2(dx, -dy) / (2 * Math.PI); // 0 at top, increasing clockwise
    if (ang < 0) ang += 1;
    return {
      u: (cell.cx - GEO.board.x) / GEO.board.w,
      v: (cell.cy - GEO.board.y) / GEO.board.h,
      rad: Math.hypot(dx, dy) / maxR,
      angN: ang,
    };
  };

  const keys = kp.positions.map((pos, i) => {
    const cell = pos ? cellAt(pos[0], pos[1]) : null;
    const sp = cell ? spatial(cell) : { u: 0, v: 0, rad: 0, angN: 0 };
    return { index: i, pos: cell ? pos : null, cell, ...sp, ringN: sp.angN };
  });
  const ug = up.positions.map((pos, i) => {
    const cell = pos ? ugAt(pos[0], pos[1]) : null;
    const sp = cell ? spatial(cell) : { u: 0, v: 0, rad: 0, angN: 0 };
    return { index: i, pos: cell ? pos : null, cell, ...sp, ringN: cell ? cell.ring / UG_COUNT : 0 };
  });

  const keyPosToIndex = new Map();
  keys.forEach((k) => { if (k.pos) keyPosToIndex.set(k.pos.join(','), k.index); });
  const ugPosToIndex = new Map();
  ug.forEach((u) => { if (u.pos) ugPosToIndex.set(u.pos.join(','), u.index); });

  const statusCells = [];
  {
    const total = STATUS_COUNT * GEO.status.w + (STATUS_COUNT - 1) * GEO.status.gap;
    const sx = (GEO.vbW - total) / 2;
    for (let i = 0; i < STATUS_COUNT; i++) {
      const x = sx + i * (GEO.status.w + GEO.status.gap);
      statusCells.push({ i, x, y: GEO.status.y, w: GEO.status.w, h: GEO.status.h, cx: x + GEO.status.w / 2, cy: GEO.status.y + GEO.status.h / 2 });
    }
  }

  state.geom = {
    rows, cells, ugCells, statusCells, keys, ug, keyPosToIndex, ugPosToIndex,
    provisionalKeys: kp.provisional, provisionalUg: up.provisional,
    provKeys, provUg, align,
  };
  return state.geom;
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

/** Composite base layer + effect layer into per-strip-index colours. */
function computeFrame(t) {
  const geom = state.geom || buildGeometry();
  const light = currentLighting() || {};
  const eff = light.effect && light.effect.name ? light.effect : null;
  const cp = eff ? (compiledPalette(eff.palette) || compiledPalette('rainbow')) : null;

  const baseKeys = geom.keys.map((k) => {
    const e = keyEntry(k.index);
    return e && isHex6(e.color) ? hexToRgb(e.color) : [0, 0, 0];
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
  return { keys, ug, status };
}

const frameToWire = (f) => ({
  keys: f.keys.map(rgbToHex),
  underglow: f.ug.map(rgbToHex),
  status: f.status,
});

/* ========================================================== 9. device SVG */

const svgRefs = { keys: new Map(), ug: new Map(), status: [], glow: new Map() };

function buildDevice() {
  const geom = buildGeometry();
  const sig = JSON.stringify([geom.rows, geom.align]);
  const svg = $('device');
  if (sig === state.geomSig && svg.childNodes.length) return;
  state.geomSig = sig;

  svg.textContent = '';
  svgRefs.keys.clear(); svgRefs.ug.clear(); svgRefs.glow.clear(); svgRefs.status = [];

  svg.append(svgEl('rect', { class: 'board', x: GEO.board.x, y: GEO.board.y, width: GEO.board.w, height: GEO.board.h, rx: GEO.board.r }));

  // blurred duplicates of every lit surface, painted underneath for a soft glow
  const glow = svgEl('g', { class: 'glowlayer' });
  svg.append(glow);

  const addGlow = (kind, id, attrs) => {
    const r = svgEl('rect', attrs);
    glow.append(r);
    svgRefs.glow.set(kind + ':' + id, r);
  };

  // underglow first so it reads as sitting behind/around the keys
  for (const c of geom.ugCells) {
    const id = `${c.gx},${c.gy}`;
    addGlow('ug', id, { x: c.x, y: c.y, width: c.w, height: c.h, rx: 12 });
    const g = svgEl('g', { class: 'cell-g', tabindex: 0, role: 'button', 'data-zone': 'underglow', 'data-pos': id });
    const rect = svgEl('rect', { class: 'ug-cell', x: c.x, y: c.y, width: c.w, height: c.h, rx: 12 });
    const idx = svgEl('text', { class: 'cell-idx', x: c.cx, y: c.cy + 4 });
    g.append(rect, idx);
    svg.append(g);
    svgRefs.ug.set(id, { g, rect, idx, cell: c });
  }

  // the missing centre of the 3x3 grid, called out explicitly
  const cw = GEO.xEdges[2] - GEO.xEdges[1], ch = GEO.yEdges[2] - GEO.yEdges[1];
  svg.append(svgEl('rect', { class: 'nocentre', x: GEO.xEdges[1] + 3, y: GEO.yEdges[1] + 3, width: cw - 6, height: ch - 6, rx: 12 }));
  const nc = svgEl('text', { class: 'nocentre-t', x: GEO.vbW / 2, y: GEO.yEdges[2] - 6 });
  nc.textContent = 'no centre underglow LED';
  svg.append(nc);

  for (const c of geom.cells) {
    const id = `${c.row},${c.col}`;
    addGlow('key', id, { x: c.x, y: c.y, width: c.w, height: c.h, rx: GEO.key.r });
    const g = svgEl('g', { class: 'cell-g', tabindex: 0, role: 'button', 'data-zone': 'keys', 'data-pos': id });
    const rect = svgEl('rect', { class: 'key-cell', x: c.x, y: c.y, width: c.w, height: c.h, rx: GEO.key.r });
    const idx = svgEl('text', { class: 'cell-idx', x: c.cx, y: c.cy + 4 });
    const lab = svgEl('text', { class: 'cell-lab', x: c.cx, y: c.cy + 12 });
    g.append(rect, idx, lab);
    svg.append(g);
    svgRefs.keys.set(id, { g, rect, idx, lab, cell: c });
  }

  for (const c of geom.statusCells) {
    addGlow('st', String(c.i), { x: c.x, y: c.y, width: c.w, height: c.h, rx: GEO.status.r });
    const g = svgEl('g', { class: 'cell-g', tabindex: 0, role: 'button', 'data-zone': 'status', 'data-pos': String(c.i) });
    const rect = svgEl('rect', { class: 'st-cell', x: c.x, y: c.y, width: c.w, height: c.h, rx: GEO.status.r });
    const idx = svgEl('text', { class: 'cell-idx', x: c.cx, y: c.cy + 4 });
    g.append(rect, idx);
    svg.append(g);
    svgRefs.status.push({ g, rect, idx, cell: c });
  }
  const st = svgEl('text', { class: 'zone-t', x: GEO.vbW / 2, y: GEO.vbH - 4 });
  st.textContent = 'status LEDs — PWM duty 0-255, single colour';
  svg.append(st);
}

/** Strip index shown on a physical position — the pending sweep map wins while identifying. */
function indexAtPos(zone, posKey) {
  if (state.identify.active && state.identify.target === zone) {
    const list = state.identify.map[zone];
    const i = list.findIndex((p) => Array.isArray(p) && p.join(',') === posKey);
    return i === -1 ? null : i;
  }
  const m = zone === 'keys' ? state.geom.keyPosToIndex : state.geom.ugPosToIndex;
  const v = m.get(posKey);
  return v === undefined ? null : v;
}

function paint(frame) {
  const showIdx = $('chk-indices').checked;
  const showLab = $('chk-labels').checked;
  const sel = state.sel;

  for (const [id, ref] of svgRefs.keys) {
    const i = indexAtPos('keys', id);
    const rgb = i === null ? [0.06, 0.07, 0.09] : frame.keys[i] || [0, 0, 0];
    const hex = '#' + rgbToHex(rgb);
    ref.rect.setAttribute('fill', hex);
    svgRefs.glow.get('key:' + id).setAttribute('fill', hex);
    ref.idx.textContent = showIdx ? (i === null ? '—' : String(i)) : '';
    ref.idx.setAttribute('fill', inkFor(rgb));
    const entry = i === null ? null : keyEntry(i);
    ref.lab.textContent = showLab && entry && entry.label ? entry.label.slice(0, 12) : '';
    ref.lab.setAttribute('fill', inkFor(rgb));
    // keep the pair optically centred whether or not a label is showing
    ref.idx.setAttribute('y', ref.lab.textContent ? ref.cell.cy - 1 : ref.cell.cy + 4);
    ref.g.dataset.unmapped = i === null ? '1' : '0';
    ref.g.dataset.sel = sel && sel.zone === 'keys' && sel.index === i ? '1' : '0';
    ref.g.setAttribute('aria-label',
      `key at row ${ref.cell.row + 1} column ${ref.cell.col + 1}` +
      (i === null ? ', no strip index mapped' : `, strip index ${i}`) +
      (entry && entry.label ? `, ${entry.label}` : '') +
      `, colour ${rgbToHex(rgb)}`);
  }

  for (const [id, ref] of svgRefs.ug) {
    const i = indexAtPos('underglow', id);
    const rgb = i === null ? [0.06, 0.07, 0.09] : frame.ug[i] || [0, 0, 0];
    const hex = '#' + rgbToHex(rgb);
    ref.rect.setAttribute('fill', hex);
    svgRefs.glow.get('ug:' + id).setAttribute('fill', hex);
    ref.idx.textContent = showIdx ? (i === null ? '—' : String(i)) : '';
    ref.idx.setAttribute('fill', inkFor(rgb));
    ref.g.dataset.unmapped = i === null ? '1' : '0';
    ref.g.dataset.sel = sel && sel.zone === 'underglow' && sel.index === i ? '1' : '0';
    ref.g.setAttribute('aria-label',
      `underglow at grid x ${ref.cell.gx} y ${ref.cell.gy}` +
      (i === null ? ', no strip index mapped' : `, strip index ${i}`) +
      `, colour ${rgbToHex(rgb)}`);
  }

  svgRefs.status.forEach((ref, i) => {
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
}

/* ==================================================== 10. animation loop */

function tick(ts) {
  const a = state.anim;
  if (!a.lastTs) a.lastTs = ts;
  const dt = (ts - a.lastTs) / 1000;
  a.lastTs = ts;
  if (a.playing) a.t += dt;
  const interval = 1000 / deviceFps();
  if (ts - a.lastPaint >= interval - 1) {
    a.lastPaint = ts;
    try {
      state.lastFrame = computeFrame(a.t);
      paint(state.lastFrame);
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
  const f = state.lastFrame || computeFrame(state.anim.t);
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

function renderColorPanel() {
  const sel = state.sel;
  const nameEl = $('sel-name'), hintEl = $('sel-hint');
  const keyBox = $('key-extra'), stBox = $('status-led-box');

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
    nameEl.textContent = `key · strip index ${sel.index}`;
    hintEl.textContent = entry && entry.label
      ? `“${entry.label}” — writes profiles.${state.profile}.keys[index ${sel.index}].color`
      : `writes profiles.${state.profile}.keys[index ${sel.index}].color`;
    ceLed.setEnabled(true);
    ceLed.show(entry && isHex6(entry.color) ? entry.color : '000000');
    keyBox.hidden = false; stBox.hidden = true;
  } else if (sel.zone === 'underglow') {
    nameEl.textContent = `underglow · strip index ${sel.index}`;
    hintEl.textContent = 'The config stores one shared underglow base colour — edit it under “Base layer” below. Per-LED underglow colour comes from an effect.';
    ceLed.setEnabled(false);
    ceLed.show(isHex6(currentLighting()?.underglow) ? currentLighting().underglow : '000000');
    keyBox.hidden = true; stBox.hidden = true;
  } else {
    nameEl.textContent = `status LED ${sel.index}`;
    hintEl.textContent = 'Single-colour LED: 8-bit PWM duty, no hue.';
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
  const duty = currentLighting()?.status_leds || [];
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
  $('eff-scope').textContent = `profiles.${state.profile || '?'}.lighting.effect`;
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
  if (!eff) notes.push('This profile has no effect — only the base colours render. Pick a name to add one.');
  else {
    if (e.palette && !allPalettes()[e.palette]) notes.push(`Palette “${e.palette}” is not in this config or the built-in corpus — the preview falls back to rainbow.`);
    if (!e.palette && !['rainbow', 'off'].includes(e.name)) notes.push('No palette set — the preview falls back to rainbow.');
    if (e.direction === 'ring' && e.target === 'keys') notes.push('Ring direction on the keys uses the angle around the pad centre.');
    if (e.direction === 'ring' && state.geom?.provisionalUg) notes.push('Ring order is provisional until the underglow mapping is verified.');
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

function seedIdentifyMap() {
  state.identify.map = {
    keys: (state.config?.layout?.key_positions || []).slice(0, KEY_COUNT).map((p) => (Array.isArray(p) && p.length === 2 ? [p[0], p[1]] : null)),
    underglow: (state.config?.layout?.underglow_positions || []).slice(0, UG_COUNT).map((p) => (Array.isArray(p) && p.length === 2 ? [p[0], p[1]] : null)),
  };
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
  $('chk-verified').checked = !!state.config?.layout?.verified;

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
  const bits = [];
  if (missing) bits.push(`${missing} of ${count} indices unrecorded`);
  if (dupes) bits.push(`${dupes} position(s) claimed by more than one index`);
  if (!state.daemonReachable) bits.push('No daemon: the sweep cannot light the pad, but you can still record a mapping by hand.');
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
    toast(`Mapping written to layout${cfg.layout.verified ? ' and marked verified' : ''} — remember to Save`, 'ok');
  }
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
  const layout = state.config?.layout || {};
  if (layout.verified === true) return;
  const g = state.geom;
  const which = [];
  if (g?.provisionalKeys) which.push('per-key');
  if (g?.provisionalUg) which.push('underglow');
  const btn = el('button', { type: 'button', class: 'ghost small', text: 'Run identify sweep' });
  btn.addEventListener('click', () => { showTab('identify'); $('btn-id-start').focus(); });
  slot.append(el('div', { class: 'banner', role: 'note' }, [
    el('span', { class: 'icon', text: '!', 'aria-hidden': 'true' }),
    el('div', {}, [
      el('p', { html: '<strong>LED index mapping is not verified.</strong> <code>layout.verified</code> is false, so the positions shown here are a guess and spatial effects may land on the wrong LEDs.' }),
      el('p', {
        text: which.length
          ? `Using provisional defaults for the ${which.join(' and ')} mapping: per-key strip index 0→12 in reading order (rows of ${keyRows().join('/')}), underglow strip index 0→7 clockwise from the top-left. Short rows are drawn centred — which columns they occupy is also unknown.`
          : 'A mapping is present in the config but has not been marked verified.',
      }),
      el('div', { class: 'b-actions' }, [btn]),
    ]),
  ]));
}

function renderStageNote() {
  const g = state.geom;
  const bits = [];
  const unplacedK = g.keys.filter((k) => !k.pos).map((k) => k.index);
  const unplacedU = g.ug.filter((u) => !u.pos).map((u) => u.index);
  const total = g.cells.length;
  if (total !== KEY_COUNT) bits.push(`layout.key_rows sums to ${total}, not ${KEY_COUNT}.`);
  if (unplacedK.length) bits.push(`per-key indices with no position: ${unplacedK.join(', ')}.`);
  if (unplacedU.length) bits.push(`underglow indices with no position: ${unplacedU.join(', ')}.`);
  if (!bits.length) bits.push(`${total} keys in rows of ${g.rows.join('/')}, 8 underglow positions in a 3x3 grid with no centre, 3 status LEDs.`);
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
}

/* ===================================================== 18. tabs & the JSON */

const TABS = ['color', 'palette', 'effect', 'identify', 'config'];

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
  }
  return errs;
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
  const c = currentLighting()?.underglow;
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
    };
  } else if (!res.reachable) {
    state.status = { connected: false, port: null, active_profile: null, active_mode: null, battery: null, previewing: false };
  }
  renderTop();
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
    if (i === null) { toast('That position has no strip index mapped — use the identify sweep', 'warn'); return; }
    selectLed(zone, i, posKey);
    showTab('color');
  };

  svg.addEventListener('click', (ev) => {
    const g = ev.target.closest('.cell-g');
    if (g) activate(g);
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
      const [r, c] = g.dataset.pos.split(',').map(Number);
      const rows = state.geom.rows;
      let nr = clamp(r + d[1], 0, rows.length - 1);
      let nc = clamp(c + d[0], 0, rows[nr] - 1);
      if (d[1] !== 0) nc = clamp(c, 0, rows[nr] - 1);
      svgRefs.keys.get(`${nr},${nc}`)?.g.focus();
    } else if (zone === 'underglow') {
      const [gx, gy] = g.dataset.pos.split(',').map(Number);
      const cur = RING_ORDER.findIndex((p) => p[0] === gx && p[1] === gy);
      const step = d[0] !== 0 ? d[0] : d[1];
      const next = RING_ORDER[(cur + step + UG_COUNT) % UG_COUNT];
      svgRefs.ug.get(next.join(','))?.g.focus();
    } else {
      const i = clamp(Number(g.dataset.pos) + (d[0] || 0), 0, STATUS_COUNT - 1);
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
  $('btn-id-provisional').addEventListener('click', () => {
    const g = state.geom;
    state.identify.map[state.identify.target] = (state.identify.target === 'keys' ? g.provKeys : g.provUg)
      .slice(0, identifyCount()).map((p) => (p ? p.slice() : null));
    renderIdentifyPanel();
    toast('Filled with the provisional (unverified) mapping', 'warn');
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
    renderProfileSelect();
    renderColorPanel();
    renderEffectPanel();
    syncUnderglowEditor();
    syncStatusSliders();
    renderTop();
    touch(null);
    if (state.lastFrame) paint(state.lastFrame);
  });
  $('btn-make-active').addEventListener('click', () => {
    if (!state.profile) return;
    state.config.active_profile = state.profile;
    renderProfileSelect();
    touch(null);
    toast(`active_profile set to “${state.profile}”`, 'ok');
  });

  $('sel-align').addEventListener('change', (ev) => {
    // Purely a view preference: the config has nowhere to record it, and the col ordinals
    // recorded by the identify sweep do not depend on it.
    prefs.write('shortRowAlign', ev.target.value);
    buildGeometry();
    buildDevice();
    renderStageNote();
    if (state.lastFrame) paint(state.lastFrame);
  });

  $('chk-indices').addEventListener('change', (ev) => { prefs.write('showIdx', ev.target.checked); if (state.lastFrame) paint(state.lastFrame); });
  $('chk-labels').addEventListener('change', (ev) => { prefs.write('showLab', ev.target.checked); if (state.lastFrame) paint(state.lastFrame); });
  $('chk-anim').addEventListener('change', (ev) => { state.anim.playing = ev.target.checked; prefs.write('anim', ev.target.checked); });
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
  $('chk-anim').checked = prefs.read('anim', true);
  state.anim.playing = $('chk-anim').checked;
  $('sel-align').value = prefs.read('shortRowAlign', 'center');

  wireTabs();
  wireColorPanel();
  wirePalettePanel();
  wireEffectPanel();
  wireIdentifyPanel();
  wireConfigPanel();
  wireChrome();
  wireDeviceView();

  // Something sensible on screen before the first fetch resolves: no empty frame, no jump.
  adoptConfig(clone(OFFLINE_CONFIG));
  state.dirty = false;
  renderTop();

  const tab = prefs.read('tab', 'color');
  showTab(TABS.includes(tab) ? tab : 'color');

  requestAnimationFrame(tick);
  loadAll();
  refreshStatus();
}

init();
