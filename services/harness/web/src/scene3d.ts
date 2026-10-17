// The watch floor: the page's background, a real perspective scene on a 2D canvas (no WebGL, no
// library, no network). A floor of grid lines runs out to a glowing horizon while the camera drifts
// forward, data motes float past, and after a briefing the user's own deals stand on the floor as
// glowing columns: coloured by band, taller the lower the deal's health, each in a lane chosen by its
// id so a deal is always in the same place.
//
//   idle      a slow drift
//   listening faster, with ripples spreading across the floor
//   thinking  a fast glide, a brighter horizon
//   speaking  a ripple on every spoken word (the "wf:beat" event)
//   error     nearly still, warm
// The horizon warms as more of the pipeline is at risk, and takes a faint tint from the hour. The
// pointer moves the camera a little and scrolling lowers the horizon. It is decoration: nothing here
// carries meaning that the words on the page don't. It stops when hidden, thins itself if frames run
// slow, and draws one still frame under reduced motion or the "Calm" preference.

import { parseColor } from "./orb3d";
import { skyline, type Pillar, type SkylineDeal } from "./logic";
import { settings } from "./store";

type RGB = [number, number, number];
type State = "idle" | "listening" | "thinking" | "speaking" | "error";

interface Pace {
  speed: number; // world units per second
  glow: number;
  heat: number; // 0..1 toward the warning colours
}

const PACE: Record<State, Pace> = {
  idle: { speed: 0.45, glow: 1, heat: 0 },
  listening: { speed: 0.9, glow: 1.25, heat: 0 },
  thinking: { speed: 2.4, glow: 1.5, heat: 0 },
  speaking: { speed: 0.8, glow: 1.2, heat: 0 },
  error: { speed: 0.15, glow: 0.8, heat: 1 },
};

const mix = (a: RGB, b: RGB, t: number): RGB => [
  a[0] + (b[0] - a[0]) * t,
  a[1] + (b[1] - a[1]) * t,
  a[2] + (b[2] - a[2]) * t,
];
const clamp01 = (n: number): number => Math.max(0, Math.min(1, n));
const rgba = (c: RGB, a: number): string =>
  `rgb(${Math.round(c[0])} ${Math.round(c[1])} ${Math.round(c[2])} / ${clamp01(a).toFixed(3)})`;

export interface Scene3D {
  /** Stand the user's deals on the floor (an empty list clears it). */
  setDeals(deals: readonly SkylineDeal[]): void;
  /** Re-read the theme and the Calm preference. */
  refresh(): void;
  destroy(): void;
}

const Z_FAR = 46;
const CAM_H = 1.5;
const RUNG = 1.7;

interface Mote {
  x: number;
  y: number;
  z: number;
  p: number;
}

export function mountScene(canvas: HTMLCanvasElement): Scene3D | null {
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const c: CanvasRenderingContext2D = ctx;
  const reduceQuery = matchMedia("(prefers-reduced-motion: reduce)");
  const darkQuery = matchMedia("(prefers-color-scheme: dark)");
  const coarse = matchMedia("(pointer: coarse)").matches;

  // --- theme ---
  let dark = true;
  let line: RGB = [79, 224, 200];
  let mote: RGB = [124, 140, 255];
  let warn: RGB = [241, 178, 95];
  let crit: RGB = [255, 143, 132];
  let ok: RGB = [98, 207, 149];
  let tod: RGB = [0, 0, 0];
  let todAmount = 0;

  const readTheme = (): void => {
    const style = getComputedStyle(document.documentElement);
    const token = (name: string): RGB => parseColor(style.getPropertyValue(name));
    const bg = token("--bg");
    dark = bg[0] * 0.299 + bg[1] * 0.587 + bg[2] * 0.114 < 128;
    const ink: RGB = [16, 36, 64];
    const deepen = (col: RGB): RGB => (dark ? col : mix(col, ink, 0.2));
    line = deepen(token("--signal"));
    mote = deepen(token("--signal-3"));
    warn = deepen(token("--warn"));
    crit = deepen(token("--crit"));
    ok = deepen(token("--ok"));
    // A faint tint from the hour: warm in the morning, violet in the evening, none at night.
    const hour = new Date().getHours();
    if (hour >= 5 && hour < 12) {
      tod = warn;
      todAmount = 0.07;
    } else if (hour >= 18 && hour < 22) {
      tod = mote;
      todAmount = 0.09;
    } else {
      tod = line;
      todAmount = 0;
    }
  };

  const still = (): boolean => reduceQuery.matches || settings().motion === "calm";

  // --- size ---
  let W = 1;
  let H = 1;
  let dpr = 1;
  const size = (): void => {
    dpr = Math.min(window.devicePixelRatio || 1, coarse ? 1 : 1.5);
    W = Math.max(1, Math.round(window.innerWidth * dpr));
    H = Math.max(1, Math.round(window.innerHeight * dpr));
    canvas.width = W;
    canvas.height = H;
  };

  // --- the world ---
  const seeded = (n: number): number => {
    const x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
    return x - Math.floor(x);
  };
  const stars = Array.from({ length: coarse ? 36 : 80 }, (_, i) => ({ x: seeded(i), y: seeded(i + 99), p: seeded(i + 7) }));
  let moteCount = coarse ? 40 : 90;
  const motes: Mote[] = Array.from({ length: 140 }, (_, i) => ({
    x: (seeded(i + 3) - 0.5) * 30,
    y: 0.3 + seeded(i + 13) * 6,
    z: 3 + seeded(i + 23) * (Z_FAR - 3),
    p: seeded(i + 31),
  }));
  let pillars: { p: Pillar; z: number }[] = [];
  let rings: { r: number; a: number }[] = [];
  let ringTimer = 0;
  let riskTarget = 0;
  let risk = 0;
  const pace: Pace = { ...PACE.idle };
  let travel = 0;
  let clock = 0;
  let camX = 0;
  let aimX = 0;
  let aimY = 0;
  let camY = 0;

  const onPointer = (e: PointerEvent): void => {
    aimX = Math.max(-1, Math.min(1, (e.clientX / Math.max(window.innerWidth, 1)) * 2 - 1)) * 1.4;
    aimY = Math.max(-1, Math.min(1, (e.clientY / Math.max(window.innerHeight, 1)) * 2 - 1));
  };
  const onBeat = (): void => {
    if (rings.length < 6) rings.push({ r: 0.6, a: 1 });
  };

  function setDeals(deals: readonly SkylineDeal[]): void {
    const list = skyline(deals);
    pillars = list.map((p, i) => ({ p, z: 9 + i * 6.5 }));
    canvas.dataset.pillars = String(list.length);
    riskTarget = list.length ? clamp01(list.filter((d) => d.band === "at_risk").length / list.length) : 0;
    if (still()) paintStill();
  }

  // --- drawing ---
  function draw(): void {
    const w = W;
    const h = H;
    c.clearRect(0, 0, w, h);
    const scrollY = Math.min(window.scrollY || 0, 900);
    const hz = h * 0.4 - scrollY * 0.03 * dpr + camY * h * 0.02;
    const cx = w / 2 - camX * w * 0.012 + Math.sin(clock * 0.12) * w * 0.008;
    const F = h * 0.95;
    const zNear = (F * CAM_H) / Math.max(1, h - hz + 4 * dpr);
    const floorY = (z: number): number => hz + (F * CAM_H) / z;
    const sx = (x: number, z: number): number => cx + (F * (x - camX)) / z;
    const heat = pace.heat;
    const warm = clamp01(risk * 0.7);
    const glowCol = mix(mix(line, warn, warm), crit, heat);

    // The hour's tint over the sky.
    if (todAmount > 0) {
      const sky = c.createLinearGradient(0, 0, 0, hz);
      sky.addColorStop(0, rgba(tod, todAmount));
      sky.addColorStop(1, rgba(tod, 0));
      c.fillStyle = sky;
      c.fillRect(0, 0, w, hz);
    }

    // Stars, only on a dark sky.
    if (dark) {
      c.fillStyle = rgba(mote, 1);
      for (const s of stars) {
        const tw = 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(clock * (0.6 + s.p) + s.p * 40));
        c.globalAlpha = tw * 0.55;
        const px = s.x * w - camX * 3 * dpr * (0.3 + s.p);
        const py = s.y * hz * 0.92;
        c.fillRect(px, py, (0.7 + s.p) * dpr, (0.7 + s.p) * dpr);
      }
      c.globalAlpha = 1;
    }

    // The floor: rungs that glide toward the camera, and lines that meet at the vanishing point.
    c.lineWidth = Math.max(1, dpr * 0.9);
    const phase = travel % RUNG;
    const lineAlpha = dark ? 1 : 1.35;
    for (let k = 1; ; k++) {
      const z = k * RUNG - phase;
      if (z > Z_FAR) break;
      if (z < zNear) continue;
      const y = floorY(z);
      c.strokeStyle = rgba(line, (0.05 + 0.3 * Math.pow(1 - z / Z_FAR, 1.2)) * lineAlpha);
      c.beginPath();
      c.moveTo(0, y);
      c.lineTo(w, y);
      c.stroke();
    }
    const yFar = floorY(Z_FAR);
    const zEdge = zNear * 0.9;
    for (const accent of [false, true]) {
      c.strokeStyle = rgba(line, (accent ? 0.34 : 0.17) * lineAlpha);
      c.beginPath();
      for (let j = -16; j <= 16; j++) {
        if ((j % 4 === 0) !== accent) continue;
        const x = j * 1.7;
        c.moveTo(sx(x, Z_FAR), yFar);
        c.lineTo(sx(x, zEdge), floorY(zEdge));
      }
      c.stroke();
    }
    // The floor melts into the horizon: erase the lines with a gradient rather than painting fog,
    // so the page's own gradients show through.
    c.globalCompositeOperation = "destination-out";
    const fog = c.createLinearGradient(0, hz, 0, hz + (h - hz) * 0.5);
    fog.addColorStop(0, "rgb(0 0 0 / 1)");
    fog.addColorStop(1, "rgb(0 0 0 / 0)");
    c.fillStyle = fog;
    c.fillRect(0, hz - 2, w, (h - hz) * 0.5 + 2);
    c.globalCompositeOperation = dark ? "lighter" : "source-over";

    // The horizon's glow.
    c.save();
    c.translate(cx, hz);
    c.scale(1, 0.16);
    const glowA = (dark ? 0.3 : 0.2) * pace.glow * (0.85 + 0.15 * Math.sin(clock * 0.8));
    const g = c.createRadialGradient(0, 0, 0, 0, 0, w * 0.62);
    g.addColorStop(0, rgba(glowCol, glowA));
    g.addColorStop(1, rgba(glowCol, 0));
    c.fillStyle = g;
    c.fillRect(-w * 0.62, -w * 0.62, w * 1.24, w * 1.24);
    c.restore();
    const rim = c.createLinearGradient(0, 0, w, 0);
    rim.addColorStop(0, rgba(glowCol, 0));
    rim.addColorStop(0.5, rgba(glowCol, (dark ? 0.55 : 0.4) * Math.min(1.2, pace.glow)));
    rim.addColorStop(1, rgba(glowCol, 0));
    c.fillStyle = rim;
    c.fillRect(0, hz - dpr, w, 2 * dpr);

    // Ripples across the floor (listening, and on each spoken word).
    c.lineWidth = Math.max(1.5, dpr * 1.6);
    for (const ring of rings) {
      c.strokeStyle = rgba(glowCol, ring.a * 0.5);
      c.beginPath();
      let open = false;
      for (let a = 0; a <= Math.PI * 2 + 0.01; a += 0.18) {
        const z = 7 + ring.r * Math.sin(a) * 0.9;
        if (z < zNear * 1.05 || z > Z_FAR) {
          open = false;
          continue;
        }
        const px = sx(ring.r * Math.cos(a), z);
        if (open) c.lineTo(px, floorY(z));
        else c.moveTo(px, floorY(z));
        open = true;
      }
      c.stroke();
    }

    // Data motes drifting past.
    for (let i = 0; i < moteCount; i++) {
      const m = motes[i];
      const sy = hz + (F * (CAM_H - m.y)) / m.z;
      const px = cx + (F * (m.x - camX * 1.3)) / m.z;
      if (sy > h || px < -20 || px > w + 20) continue;
      const fade = clamp01((m.z - zNear) / 3) * clamp01((Z_FAR - m.z) / 10);
      const a = fade * 0.55 * (0.6 + 0.4 * Math.sin(clock * 1.5 + m.p * 6.28));
      if (a < 0.02) continue;
      c.fillStyle = rgba(m.p > 0.5 ? mote : line, a);
      c.beginPath();
      c.arc(px, sy, (0.7 + 14 / m.z) * dpr, 0, Math.PI * 2);
      c.fill();
    }

    // The user's deals, far to near.
    const bandColour = (band: Pillar["band"]): RGB => (band === "at_risk" ? crit : band === "watch" ? warn : ok);
    for (const col of [...pillars].sort((a, b) => b.z - a.z)) {
      const z = col.z;
      if (z < zNear * 1.2 || z > Z_FAR) continue;
      // Close to the camera a column would become a fat slab across the page: it thins and fades first.
      const fade = clamp01((z - zNear * 1.2) / 11) * clamp01((Z_FAR - z) / 8);
      if (fade < 0.02) continue;
      const base = floorY(z);
      const top = hz + (F * (CAM_H - col.p.height)) / z;
      const px = sx(col.p.x, z);
      const width = Math.min(26 * dpr, Math.max(1.5 * dpr, (F * 0.28) / z));
      const tone = bandColour(col.p.band);
      const body = c.createLinearGradient(0, top, 0, base);
      body.addColorStop(0, rgba(tone, 0));
      body.addColorStop(1, rgba(tone, (dark ? 0.5 : 0.34) * fade));
      c.fillStyle = body;
      c.fillRect(px - width / 2, top, width, base - top);
      // Its glow on the floor and a bright cap.
      c.save();
      c.translate(px, base);
      c.scale(1, 0.3);
      const pool = c.createRadialGradient(0, 0, 0, 0, 0, width * 5);
      pool.addColorStop(0, rgba(tone, 0.4 * fade));
      pool.addColorStop(1, rgba(tone, 0));
      c.fillStyle = pool;
      c.fillRect(-width * 5, -width * 5, width * 10, width * 10);
      c.restore();
      c.fillStyle = rgba(tone, (dark ? 0.85 : 0.55) * fade);
      c.beginPath();
      c.arc(px, top, Math.max(1, width * (dark ? 0.7 : 0.5)), 0, Math.PI * 2);
      c.fill();
    }
    c.globalCompositeOperation = "source-over";
  }

  function frame(dt: number, snap: boolean): void {
    const raw = document.body.dataset.voice ?? "idle";
    const state: State = raw in PACE ? (raw as State) : "idle";
    const target = PACE[state];
    const ease = snap ? 1 : 1 - Math.exp(-dt * 3);
    pace.speed += (target.speed - pace.speed) * ease;
    pace.glow += (target.glow - pace.glow) * ease;
    pace.heat += (target.heat - pace.heat) * ease;
    risk += (riskTarget - risk) * (snap ? 1 : 1 - Math.exp(-dt * 1.2));
    const follow = snap ? 1 : 1 - Math.exp(-dt * 2.2);
    camX += ((snap ? 0 : aimX) - camX) * follow;
    camY += ((snap ? 0 : aimY) - camY) * follow;
    clock += dt;
    travel += pace.speed * dt;
    for (const col of pillars) {
      col.z -= pace.speed * dt;
      if (col.z < 2) col.z += Z_FAR + 4; // behind us: back to the far end of the floor
    }
    for (let i = 0; i < moteCount; i++) {
      const m = motes[i];
      m.z -= pace.speed * 1.15 * dt;
      if (m.z < 1.5) {
        m.z = Z_FAR;
        m.x = (seeded(clock * 7 + i) - 0.5) * 30;
      }
    }
    if (state === "listening" && !snap) {
      ringTimer -= dt;
      if (ringTimer <= 0 && rings.length < 6) {
        rings.push({ r: 0.6, a: 1 });
        ringTimer = 0.8;
      }
    }
    for (const ring of rings) {
      ring.r += dt * 4.2;
      ring.a = clamp01(1 - ring.r / 11);
    }
    rings = rings.filter((ring) => ring.a > 0.02);
    draw();
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
    // If frames keep taking over 30 ms, do less once rather than stutter.
    if (!thinned) {
      slow = gap > 30 ? slow + 1 : Math.max(0, slow - 1);
      if (slow > 40) {
        thinned = true;
        moteCount = Math.round(moteCount * 0.5);
        if (dpr > 1) {
          dpr = 1;
          W = Math.max(1, window.innerWidth);
          H = Math.max(1, window.innerHeight);
          canvas.width = W;
          canvas.height = H;
        }
      }
    }
    last = now;
    frame(Math.min(0.05, gap / 1000), false);
    raf = requestAnimationFrame(tick);
  };
  const kick = (): void => {
    if (raf || !wanted()) return;
    last = 0;
    raf = requestAnimationFrame(tick);
  };
  function paintStill(): void {
    if (!visible) return;
    clock = 3;
    travel = 5;
    frame(0, true);
  }
  const redraw = (): void => (still() ? paintStill() : kick());
  const refresh = (): void => {
    readTheme();
    redraw();
  };

  const view = new IntersectionObserver((entries) => {
    visible = entries.some((e) => e.isIntersecting);
    redraw();
  });
  const themeWatch = new MutationObserver(refresh);
  const voiceWatch = new MutationObserver(() => {
    if (still()) paintStill();
  });
  const onResize = (): void => {
    size();
    redraw();
  };
  const onVisibility = (): void => redraw();
  const hourTimer = window.setInterval(readTheme, 10 * 60_000);

  readTheme();
  size();
  view.observe(canvas);
  themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  voiceWatch.observe(document.body, { attributes: true, attributeFilter: ["data-voice"] });
  window.addEventListener("resize", onResize);
  reduceQuery.addEventListener("change", redraw);
  darkQuery.addEventListener("change", refresh);
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("pointermove", onPointer, { passive: true });
  window.addEventListener("wf:beat", onBeat);
  redraw();

  return {
    setDeals,
    refresh,
    destroy() {
      if (raf) cancelAnimationFrame(raf);
      view.disconnect();
      themeWatch.disconnect();
      voiceWatch.disconnect();
      window.clearInterval(hourTimer);
      window.removeEventListener("resize", onResize);
      reduceQuery.removeEventListener("change", redraw);
      darkQuery.removeEventListener("change", refresh);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pointermove", onPointer);
      window.removeEventListener("wf:beat", onBeat);
    },
  };
}
