// The 3D orb on the idle screen: a sphere of points, projected with a real perspective camera on a
// 2D canvas (no WebGL, no library, no network), that leans toward the pointer and changes with the
// voice state (body[data-voice]):
//   idle      slow turn, a faint breath
//   listening faster turn, a ripple travelling up the sphere
//   thinking  fast turn and three orbiting rings
//   speaking  a pulse on every spoken word (the "wf:beat" event from orb.ts)
//   error     warm colours and a tremor
// It stays honest about cost: it stops when hidden or off screen, thins itself out if frames run
// slow, and under reduced motion (system setting or the "Calm" preference) draws one still frame.
// Every state is also written out in text (#voice-state), so this is decoration, never the signal.

import { settings } from "./store";

type State = "idle" | "listening" | "thinking" | "speaking" | "error";

interface Look {
  spin: number; // rad/s
  amp: number; // breathing depth
  wave: number; // ripple strength
  rings: number; // 0..1 orbit rings
  glow: number; // brightness
  hot: number; // 0..1 toward the warning colours
}

const LOOKS: Record<State, Look> = {
  idle: { spin: 0.14, amp: 0.018, wave: 0, rings: 0, glow: 0.78, hot: 0 },
  listening: { spin: 0.34, amp: 0.05, wave: 1, rings: 0, glow: 1, hot: 0 },
  thinking: { spin: 1.1, amp: 0.03, wave: 0.35, rings: 1, glow: 0.95, hot: 0 },
  speaking: { spin: 0.4, amp: 0.035, wave: 0.3, rings: 0, glow: 0.9, hot: 0 },
  error: { spin: 0.05, amp: 0.045, wave: 0, rings: 0, glow: 0.75, hot: 1 },
};

type RGB = [number, number, number];

/** "#4fe0c8", "#4fc" or "rgb(79 224 200)" → [79, 224, 200]; a fallback teal if it can't be read. */
export function parseColor(value: string): RGB {
  const v = value.trim();
  let m = /^#([0-9a-f]{6})$/i.exec(v);
  if (m) return [parseInt(m[1].slice(0, 2), 16), parseInt(m[1].slice(2, 4), 16), parseInt(m[1].slice(4, 6), 16)];
  m = /^#([0-9a-f]{3})$/i.exec(v);
  if (m) return [...m[1]].map((c) => parseInt(c + c, 16)) as RGB;
  const rgb = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i.exec(v);
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
  return [79, 224, 200];
}

const mix = (a: RGB, b: RGB, t: number): RGB => [
  a[0] + (b[0] - a[0]) * t,
  a[1] + (b[1] - a[1]) * t,
  a[2] + (b[2] - a[2]) * t,
];
const rgbText = (c: RGB, alpha?: number): string =>
  `rgb(${Math.round(c[0])} ${Math.round(c[1])} ${Math.round(c[2])}${alpha === undefined ? "" : ` / ${alpha}`})`;

export interface Orb3D {
  /** Re-read the theme colours and the Calm preference (call after either changes). */
  refresh(): void;
  destroy(): void;
}

const BUCKETS = 24;
const FOCAL = 3; // camera distance in sphere radii: a mild perspective, no fisheye
const RING_POINTS = 72;

interface P {
  x: number;
  y: number;
  z: number;
  lon: number;
  phase: number;
}

/** n points spread evenly over a unit sphere (a Fibonacci spiral: equal-area, no clumped poles). */
export function sphere(n: number): P[] {
  const golden = Math.PI * (3 - Math.sqrt(5));
  return Array.from({ length: n }, (_, i) => {
    const y = 1 - (i / Math.max(1, n - 1)) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const lon = golden * i;
    return { x: Math.cos(lon) * r, y, z: Math.sin(lon) * r, lon, phase: (i * 0.618) % 1 };
  });
}

const RINGS: { x: number; z: number }[][] = [0, 1, 2].map(() =>
  Array.from({ length: RING_POINTS }, (_, i) => {
    const a = (i / RING_POINTS) * Math.PI * 2;
    return { x: Math.cos(a), z: Math.sin(a) };
  }),
);

export function mountOrb3d(canvas: HTMLCanvasElement): Orb3D | null {
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const c2d: CanvasRenderingContext2D = ctx;
  const host = canvas.parentElement ?? canvas;

  const reduceQuery = matchMedia("(prefers-reduced-motion: reduce)");
  const darkQuery = matchMedia("(prefers-color-scheme: dark)");
  let count = matchMedia("(pointer: coarse)").matches ? 340 : 560;
  let points = sphere(count);

  let palette: RGB[] = [];
  let hotPalette: RGB[] = [];
  let bucketColor: string[] = [];
  let bucketHot = -1;
  let dark = true;
  let glowRgb: RGB = [79, 224, 200];

  const readTheme = (): void => {
    const style = getComputedStyle(document.documentElement);
    const token = (name: string): RGB => parseColor(style.getPropertyValue(name));
    const [c1, c2, c3] = [token("--orb-1"), token("--orb-2"), token("--orb-3")];
    const [warn, crit] = [token("--warn"), token("--crit")];
    const bg = token("--bg");
    dark = bg[0] * 0.299 + bg[1] * 0.587 + bg[2] * 0.114 < 128;
    glowRgb = c1;
    // The orb tokens are tuned for a dark stage; on a light one the dots are deepened toward ink so
    // the sphere keeps its form instead of washing out.
    const ink: RGB = [16, 36, 64];
    const deepen = (c: RGB): RGB => (dark ? c : mix(c, ink, 0.42));
    palette = Array.from({ length: BUCKETS }, (_, i) => {
      const u = i / (BUCKETS - 1);
      return deepen(u < 0.5 ? mix(c1, c2, u * 2) : mix(c2, c3, (u - 0.5) * 2));
    });
    hotPalette = palette.map((_, i) => deepen(mix(warn, crit, i / (BUCKETS - 1))));
    bucketHot = -1; // the cached strings are stale
  };

  const still = (): boolean => reduceQuery.matches || settings().motion === "calm";

  let dpr = 1;
  let cssSide = 1;
  const size = (): void => {
    const box = host.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    cssSide = Math.max(1, Math.floor(Math.min(box.width, box.height || box.width)));
    canvas.width = Math.max(1, Math.round(cssSide * dpr));
    canvas.height = canvas.width;
  };

  // Eased state so a change of voice state glides instead of snapping.
  const look: Look = { ...LOOKS.idle };
  let yaw = 0;
  let tilt = 0.28;
  let lean = 0;
  let aimYaw = 0;
  let aimTilt = 0;
  let pulse = 0;
  let clock = 0;

  const onPointer = (e: PointerEvent): void => {
    const box = canvas.getBoundingClientRect();
    if (!box.width) return;
    const nx = (e.clientX - (box.left + box.width / 2)) / Math.max(window.innerWidth / 2, 1);
    const ny = (e.clientY - (box.top + box.height / 2)) / Math.max(window.innerHeight / 2, 1);
    aimYaw = Math.max(-1, Math.min(1, nx)) * 0.45;
    aimTilt = Math.max(-1, Math.min(1, ny)) * 0.3;
  };
  const onBeat = (): void => {
    pulse = 1;
  };

  function colors(): string[] {
    if (Math.abs(look.hot - bucketHot) > 0.01) {
      bucketColor = palette.map((c, i) => rgbText(look.hot > 0.01 ? mix(c, hotPalette[i], look.hot) : c));
      bucketHot = look.hot;
    }
    return bucketColor;
  }

  function frame(dt: number, snap: boolean): void {
    const raw = document.body.dataset.voice ?? "idle";
    const target = LOOKS[raw in LOOKS ? (raw as State) : "idle"];
    const ease = snap ? 1 : 1 - Math.exp(-dt * 4.5);
    for (const key of Object.keys(target) as (keyof Look)[]) look[key] += (target[key] - look[key]) * ease;
    clock += dt;
    yaw += look.spin * dt;
    const follow = snap ? 1 : 1 - Math.exp(-dt * 3);
    lean += ((snap ? 0 : aimYaw) - lean) * follow;
    tilt += (0.28 + (snap ? 0 : aimTilt) - tilt) * follow;
    pulse = Math.max(0, pulse - dt * 6);
    draw();
  }

  function draw(): void {
    const w = canvas.width;
    const R = w * 0.3;
    const cx = w / 2;
    const cy = w / 2;
    c2d.clearRect(0, 0, w, w);

    // The glow behind the sphere.
    const glow = c2d.createRadialGradient(cx, cy, R * 0.2, cx, cy, R * 1.55);
    const a = (dark ? 0.2 : 0.14) * look.glow * (1 + pulse * 0.6);
    const g = look.hot > 0.5 ? mix(glowRgb, hotPalette[BUCKETS >> 1], look.hot) : glowRgb;
    glow.addColorStop(0, rgbText(g, a));
    glow.addColorStop(1, rgbText(g, 0));
    c2d.globalCompositeOperation = "source-over";
    c2d.fillStyle = glow;
    c2d.fillRect(0, 0, w, w);

    c2d.globalCompositeOperation = dark ? "lighter" : "source-over";
    const fills = colors();
    const yawNow = yaw + lean;
    const cosY = Math.cos(yawNow);
    const sinY = Math.sin(yawNow);
    const cosP = Math.cos(tilt);
    const sinP = Math.sin(tilt);
    const dot = (x: number, y: number, z: number, hue: number, weight: number): void => {
      // Rotate about the vertical axis (yaw), then the horizontal one (tilt), then project.
      const x1 = x * cosY + z * sinY;
      const z1 = -x * sinY + z * cosY;
      const y2 = y * cosP - z1 * sinP;
      const z2 = y * sinP + z1 * cosP;
      const scale = FOCAL / (FOCAL - z2 * 0.9);
      const depth = (z2 + 1) / 2; // 0 far side … 1 near side
      const alpha = Math.min(1, (0.14 + 0.86 * depth ** 1.5) * look.glow * weight);
      if (alpha < 0.02) return;
      const bucket = Math.min(BUCKETS - 1, Math.max(0, Math.floor(hue * BUCKETS)));
      // Dot radius in device pixels: bigger when nearer, bigger on a bigger sphere, bigger on a beat.
      const r = (0.7 + 1.9 * depth) * dpr * (0.8 + pulse * 0.25) * (cssSide / 520 + 0.55);
      c2d.globalAlpha = alpha;
      c2d.fillStyle = fills[bucket];
      c2d.beginPath();
      c2d.arc(cx + x1 * R * scale, cy - y2 * R * scale, r, 0, Math.PI * 2);
      c2d.fill();
    };

    const breath = 1 + look.amp * Math.sin(clock * 1.7) + pulse * 0.1;
    for (const p of points) {
      // Radial displacement: breathing, the ripple that climbs the sphere, a tremor in the error state.
      const ripple = look.wave * 0.07 * Math.sin(p.y * 6.5 - clock * 3.4);
      const tremor = look.hot * 0.03 * Math.sin(clock * 38 + p.phase * 9);
      const rr = breath + ripple + tremor;
      const hue = 0.5 + 0.4 * p.y + 0.2 * Math.sin(p.lon + clock * 0.5);
      dot(p.x * rr, p.y * rr, p.z * rr, hue, 1);
    }

    if (look.rings > 0.03) {
      RINGS.forEach((ring, i) => {
        const spin = clock * (1.6 + i * 0.7) * (i % 2 ? -1 : 1);
        const cs = Math.cos(spin);
        const sn = Math.sin(spin);
        const slope = 0.5 + i * 0.55;
        for (const q of ring) {
          // A circle in its own plane, turned about the vertical axis, then tipped by the ring's slope.
          const px = q.x * cs - q.z * sn;
          const pz = q.x * sn + q.z * cs;
          dot(px * Math.cos(slope) * 1.18, px * Math.sin(slope) * 1.18, pz * 1.18, (i + 1) / (RINGS.length + 1), look.rings);
        }
      });
    }
    c2d.globalAlpha = 1;
    c2d.globalCompositeOperation = "source-over";
  }

  // --- the loop ---
  let raf = 0;
  let last = 0;
  let visible = true;
  let slow = 0;
  let thinned = false;

  const wanted = (): boolean => visible && !document.hidden && !still();

  const tick = (now: number): void => {
    raf = 0;
    if (!wanted()) return;
    const gap = last ? now - last : 16;
    const dt = Math.min(0.05, gap / 1000);
    // Quality: if frames keep taking over 26 ms, thin the sphere once rather than stutter.
    if (!thinned) {
      slow = gap > 26 ? slow + 1 : Math.max(0, slow - 1);
      if (slow > 45) {
        thinned = true;
        count = Math.round(count * 0.55);
        points = sphere(count);
      }
    }
    last = now;
    frame(dt, false);
    raf = requestAnimationFrame(tick);
  };

  const kick = (): void => {
    if (raf || !wanted()) return;
    last = 0;
    raf = requestAnimationFrame(tick);
  };

  const paintStill = (): void => {
    if (!visible) return;
    clock = 1.2;
    frame(0, true);
  };

  const redraw = (): void => {
    if (still()) paintStill();
    else kick();
  };

  const refresh = (): void => {
    readTheme();
    redraw();
  };

  const resize = new ResizeObserver(() => {
    size();
    redraw();
  });
  const view = new IntersectionObserver((entries) => {
    visible = entries.some((e) => e.isIntersecting);
    redraw();
  });
  const themeWatch = new MutationObserver(refresh);
  const voiceWatch = new MutationObserver(() => {
    if (still()) paintStill();
  });
  const onVisibility = (): void => redraw();

  readTheme();
  size();
  resize.observe(host);
  view.observe(canvas);
  themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  voiceWatch.observe(document.body, { attributes: true, attributeFilter: ["data-voice"] });
  reduceQuery.addEventListener("change", redraw);
  darkQuery.addEventListener("change", refresh);
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("pointermove", onPointer, { passive: true });
  window.addEventListener("wf:beat", onBeat);
  redraw();

  return {
    refresh,
    destroy() {
      if (raf) cancelAnimationFrame(raf);
      resize.disconnect();
      view.disconnect();
      themeWatch.disconnect();
      voiceWatch.disconnect();
      reduceQuery.removeEventListener("change", redraw);
      darkQuery.removeEventListener("change", refresh);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pointermove", onPointer);
      window.removeEventListener("wf:beat", onBeat);
    },
  };
}
