// The chart below the page (ADR-035): the numbers and choices behind src/backdrop.ts, kept free of
// the DOM and WebGL so `npm test` can check them. The scene is a drowned coast seen from a low
// bridge: the sea floor in depth contours, drifting toward the viewer, under water tinted by depth
// and lit by a pool of the lamp's colour that wanders slowly (it never turns: the scope's sweep is
// the only thing that rotates). It carries no data. Its area colours are capped in luminance so
// every text colour keeps its contrast over it.

export type Page = "watch" | "pipeline" | "orders" | "activity" | "guide";
export type Voice = "idle" | "listening" | "thinking" | "speaking" | "error";
export type Rgb = readonly [number, number, number]; // 0 to 1, sRGB

const rgb = (r: number, g: number, b: number): Rgb => [r, g, b];

/** "#ece6d8", "#abc", "rgb(236 230 216)", "rgb(236, 230, 216, 0.5)" → [r, g, b] in 0..1; null if none of those. */
export function parseColor(css: string): Rgb | null {
  const s = css.trim().toLowerCase();
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/.exec(s);
  if (hex) {
    const h = hex[1].length === 3 ? [...hex[1]].map((c) => c + c).join("") : hex[1];
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
    return rgb(r, g, b);
  }
  const fn = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)\s*(?:[,/]\s*[\d.]+%?\s*)?\)$/.exec(s);
  if (fn) {
    const [r, g, b] = [fn[1], fn[2], fn[3]].map((n) => Math.min(255, Number(n)) / 255);
    return [r, g, b].every(Number.isFinite) ? rgb(r, g, b) : null;
  }
  return null;
}

// --- contrast: the scene may only move the page's background so far ---

const linear = (c: number): number => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);

export const luminance = (c: Rgb): number => 0.2126 * linear(c[0]) + 0.7152 * linear(c[1]) + 0.0722 * linear(c[2]);

export function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** `top` laid over `base` at `alpha` (what the canvas does to the page behind it). */
export const over = (base: Rgb, top: Rgb, alpha: number): Rgb =>
  rgb(base[0] + (top[0] - base[0]) * alpha, base[1] + (top[1] - base[1]) * alpha, base[2] + (top[2] - base[2]) * alpha);

/** How far the scene may move the page, per theme; the shaders clamp to these.
 *  `tint` is the brightest (dark) or darkest (light) luminance any area of water may reach. Every
 *  area of the scene (the sea, the lit slopes, the caustics, the shafts, the horizon) is the lamp's
 *  hue at or below that luminance, laid at any alpha up to `wash`, so text can sit anywhere on it: a
 *  test proves the faintest text colour keeps 4.5:1 over the tint itself, which no blend of it with
 *  the page can exceed. `line` is the contours, the datum grid and the motes: hairlines a pixel or so
 *  wide in the lamp's or the ink's own colour, like the CSS graticule they replace (which already
 *  crossed text at its own opacity), never a field behind a word. `sea` is how hard the lamp's hue is
 *  pushed before it is laid down as open water (the glints keep the lamp's own saturation, 1): more
 *  makes the deeper, clearer blue. */
export const CAPS = {
  dark: { wash: 1, line: 0.2, tint: 0.0165, sea: 3 },
  light: { wash: 1, line: 0.15, tint: 0.726, sea: 1.4 },
} as const;

/** The lamp's hue as an area colour of luminance `target`: its channels raised to `saturation`
 *  (so a pale lamp becomes a clear blue), then slid along the line toward `toward` (black in the
 *  dark theme, white in the light) until the luminance is the target. */
export function tintOf(hue: Rgb, target: number, toward: Rgb, saturation = 1): Rgb {
  const sat = rgb(hue[0] ** saturation, hue[1] ** saturation, hue[2] ** saturation);
  const at = (t: number): Rgb => over(toward, sat, t);
  // Luminance runs one way along the line, so the target is found by halving.
  const rising = luminance(sat) > luminance(toward);
  let [lo, hi] = [0, 1];
  for (let i = 0; i < 40; i++) {
    const mid = (lo + hi) / 2;
    if (luminance(at(mid)) < target === rising) lo = mid;
    else hi = mid;
  }
  return at((lo + hi) / 2);
}

// --- the camera: one framing per page, eased between them ---

export interface Camera {
  height: number; // above the mean sea floor, in scene units
  pitch: number; // radians looking down
  yaw: number; // radians to the right
  fov: number; // vertical field of view, radians
}

const DEG = Math.PI / 180;

/** Watch looks out to a horizon; Pipeline leans over the chart table (no horizon); the rest sit between. */
export const CAMERAS: Readonly<Record<Page, Camera>> = {
  watch: { height: 2.3, pitch: 0.16, yaw: 0, fov: 46 * DEG },
  pipeline: { height: 3.6, pitch: 0.46, yaw: -0.06, fov: 46 * DEG },
  orders: { height: 1.8, pitch: 0.12, yaw: 0.1, fov: 46 * DEG },
  activity: { height: 2.8, pitch: 0.26, yaw: -0.12, fov: 46 * DEG },
  guide: { height: 2.6, pitch: 0.2, yaw: 0.14, fov: 46 * DEG },
};

/** Exponential approach: `tau` is how long (ms) it takes to cover about 63% of the way. */
export const approach = (from: number, to: number, dt: number, tau: number): number =>
  tau <= 0 ? to : to + (from - to) * Math.exp(-Math.max(0, dt) / tau);

export const approachCamera = (from: Camera, to: Camera, dt: number, tau: number): Camera => ({
  height: approach(from.height, to.height, dt, tau),
  pitch: approach(from.pitch, to.pitch, dt, tau),
  yaw: approach(from.yaw, to.yaw, dt, tau),
  fov: approach(from.fov, to.fov, dt, tau),
});

/** Where the horizon sits, in clip space (-1 bottom, 1 top); above 1 it is off the top of the screen. */
export const horizonY = (cam: Camera): number => Math.tan(cam.pitch) / Math.tan(cam.fov / 2);

/** How wide the ground mesh fans out per unit of distance, so it always fills the screen (with yaw and parallax to spare). */
export const spread = (cam: Camera, aspect: number): number =>
  Math.tan(cam.fov / 2) * aspect * 1.25 + Math.abs(cam.yaw) * 1.5 + 0.12;

export const NEAR = 0.6;
export const FAR = 46;
const CLIP_FAR = FAR * 1.5; // past the farthest ground, so tall hills on the horizon are never clipped

/** Column-major 4×4: perspective × pitch × yaw × lift. The ground's forward distance d sits at z = -d. */
export function viewProjection(cam: Camera, aspect: number): Float32Array {
  const f = 1 / Math.tan(cam.fov / 2);
  const nf = 1 / (NEAR - CLIP_FAR);
  const p = [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (CLIP_FAR + NEAR) * nf, -1, 0, 0, 2 * CLIP_FAR * NEAR * nf, 0];
  const cp = Math.cos(cam.pitch);
  const sp = Math.sin(cam.pitch);
  const cy = Math.cos(cam.yaw);
  const sy = Math.sin(cam.yaw);
  // View = Rx(pitch) · Ry(yaw) · T(0, -height, 0), written out column by column.
  const v = [
    cy, sp * sy, -cp * sy, 0,
    0, cp, sp, 0,
    sy, -sp * cy, cp * cy, 0,
    0, -cam.height * cp, -cam.height * sp, 1,
  ]; // prettier-ignore
  const out = new Float32Array(16);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += p[k * 4 + r] * v[c * 4 + k];
      out[c * 4 + r] = s;
    }
  }
  return out;
}

/** A point (x, y, z) through a column-major matrix, divided by w: clip space, for tests. */
export function project(m: Float32Array, x: number, y: number, z: number): [number, number, number] {
  const w = m[3] * x + m[7] * y + m[11] * z + m[15];
  return [
    (m[0] * x + m[4] * y + m[8] * z + m[12]) / w,
    (m[1] * x + m[5] * y + m[9] * z + m[13]) / w,
    (m[2] * x + m[6] * y + m[10] * z + m[14]) / w,
  ];
}

// --- the assistant's state, as a mood the scene eases into (never a beat per word) ---

export interface Mood {
  drift: number; // how fast the floor comes toward you
  pool: number; // how bright the lamp's pool is
  relief: number; // how tall the floor stands
}

export const MOODS: Readonly<Record<Voice, Mood>> = {
  idle: { drift: 1, pool: 1, relief: 1 },
  listening: { drift: 0.6, pool: 1.35, relief: 1.06 }, // a hush, and the lamp turned up
  thinking: { drift: 2.1, pool: 1.15, relief: 1 },
  speaking: { drift: 1, pool: 1.2, relief: 1 },
  error: { drift: 0.4, pool: 0.55, relief: 0.96 },
};

export const approachMood = (from: Mood, to: Mood, dt: number, tau: number): Mood => ({
  drift: approach(from.drift, to.drift, dt, tau),
  pool: approach(from.pool, to.pool, dt, tau),
  relief: approach(from.relief, to.relief, dt, tau),
});

/** Where the lamp's pool lies on the floor at `t` seconds: a slow wander (minutes per loop), never a turn. */
export function poolAt(t: number): { x: number; z: number; radius: number } {
  return {
    x: Math.sin(t * 0.027) * 4.5 + Math.sin(t * 0.0113 + 1.3) * 1.5,
    z: 8.5 + Math.cos(t * 0.019) * 3,
    radius: 5.5 + Math.sin(t * 0.041) * 0.7,
  };
}

/** One slow swell across the floor when the assistant starts listening: the ring's radius and height. */
export const SWELL_MS = 3600;

export function swellAt(elapsed: number): { radius: number; strength: number } | null {
  if (!(elapsed >= 0) || elapsed >= SWELL_MS) return null;
  const k = elapsed / SWELL_MS;
  return { radius: 1 + k * 20, strength: 0.14 * (1 - k) ** 2 };
}

// --- pacing: the scene never runs faster than it needs to ---

export const ACTIVE_MS = 1000 / 30;
export const IDLE_MS = 100; // ten frames a second once nothing has happened for a while
export const IDLE_AFTER_MS = 20_000;

export interface Pace {
  hidden: boolean;
  still: boolean; // reduced motion or Calm: one frame, drawn on demand
  sinceActivity: number; // ms since the pointer, a key or a scroll
  voice: Voice;
}

/** The time between frames, or null when the loop should not run at all. The assistant at work keeps
 *  full pace; idle and error are resting states (a signed-out page can sit in error for hours). */
export function frameInterval(p: Pace): number | null {
  if (p.hidden || p.still) return null;
  if (p.voice === "listening" || p.voice === "thinking" || p.voice === "speaking") return ACTIVE_MS;
  return p.sinceActivity >= IDLE_AFTER_MS ? IDLE_MS : ACTIVE_MS;
}

/** Steps the render resolution down when frames keep arriving late, by time rather than by count (so a
 *  very slow machine adapts in seconds, not minutes); below the last step it says "rest", and the scene
 *  holds still. It never steps back up on its own (a resize starts it over), so it settles. */
export const LATE_BUDGET_MS = 1500;

export function createGovernor(steps: readonly number[] = [1, 0.75, 0.55, 0.4]) {
  let i = 0;
  let late = 0; // ms of late frames, less the on-time ones since
  return {
    scale: (): number => steps[i],
    /** One frame arrived `interval` ms after the last, aiming for `target`. */
    frame(interval: number, target: number): "lower" | "rest" | null {
      if (interval > 1000) return null; // the tab was throttled or asleep, not slow
      late = interval > target * 1.5 ? late + interval : Math.max(0, late - interval);
      if (late < LATE_BUDGET_MS) return null;
      late = 0;
      if (i < steps.length - 1) {
        i += 1;
        return "lower";
      }
      return "rest";
    },
    reset(): void {
      i = 0;
      late = 0;
    },
  };
}

/** Renderers that draw on the CPU: the scene holds still on them from the start. */
export const isSoftwareRenderer = (name: string): boolean =>
  /swiftshader|llvmpipe|softpipe|software|basic render|warp/i.test(name);

// --- geometry ---

/** The ground mesh as a fan: u across (-1..1), t into the distance (0..1); the shader places it. */
export function buildGrid(cols: number, rows: number): { vertices: Float32Array; indices: Uint16Array } {
  if (cols < 2 || rows < 2 || cols * rows > 65_536) throw new RangeError("grid size");
  const vertices = new Float32Array(cols * rows * 2);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const k = (r * cols + c) * 2;
      vertices[k] = (c / (cols - 1)) * 2 - 1;
      vertices[k + 1] = r / (rows - 1);
    }
  }
  const indices = new Uint16Array((cols - 1) * (rows - 1) * 6);
  let n = 0;
  for (let r = 0; r < rows - 1; r++) {
    for (let c = 0; c < cols - 1; c++) {
      const a = r * cols + c;
      const d = a + cols;
      indices.set([a, d, a + 1, a + 1, d, d + 1], n);
      n += 6;
    }
  }
  return { vertices, indices };
}

/** A fine pointer gets the full mesh; a phone or tablet a lighter one. */
export const gridFor = (coarse: boolean): { cols: number; rows: number; motes: number } =>
  coarse ? { cols: 72, rows: 60, motes: 70 } : { cols: 132, rows: 104, motes: 150 };

/** Motes in the water (seeded, so every visit has the same ones): across, above the floor, depth, phase; each 0..1 except across (-1..1). */
export function buildMotes(n: number, seed = 0x5eed): Float32Array {
  let s = seed >>> 0;
  const rand = (): number => {
    // mulberry32
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const out = new Float32Array(n * 4);
  for (let i = 0; i < n; i++) out.set([rand() * 2 - 1, rand(), rand(), rand()], i * 4);
  return out;
}
