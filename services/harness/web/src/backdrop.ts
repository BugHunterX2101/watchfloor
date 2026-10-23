// The chart below the page (ADR-035): an imaginary sea floor in depth contours, behind every page,
// drawn with WebGL2 and no library. The floor drifts toward the viewer; nearer banks hide the
// contours behind them; a pool of the lamp's colour wanders across it and lights the motes in the
// water. It is decoration only: aria-hidden, no pointer events, no data, and its brightness is
// capped (CAPS in backdrop-logic.ts) so text keeps its contrast over it.
//
// It eases into the assistant's state (body[data-voice]) rather than beating with words, frames
// each page differently, leans a little toward a fine pointer, holds one still frame under reduced
// motion or Calm, stops in a hidden tab, slows to ten frames a second when nothing is happening,
// steps its resolution down on a slow machine, and gives the page back to the CSS chart room
// (body[data-backdrop="off"]) whenever WebGL is missing, fails or is lost.

import {
  CAMERAS,
  CAPS,
  FAR,
  MOODS,
  NEAR,
  approachCamera,
  approachMood,
  buildGrid,
  buildMotes,
  createGovernor,
  frameInterval,
  gridFor,
  horizonY,
  isSoftwareRenderer,
  parseColor,
  poolAt,
  project,
  spread,
  swellAt,
  viewProjection,
  type Camera,
  type Mood,
  type Page,
  type Rgb,
  type Voice,
} from "./backdrop-logic";
import { current, onRoute } from "./router";

// --- shaders ---

// The floor: gradient noise (an integer hash, lowbias32, so it is the same on every GPU), four
// octaves, warped by a slower noise so the banks change shape over minutes, with folded ridges so
// contours bunch on the crests as soundings do on a bank. Shared by the floor and the motes.
const FLOOR = `
uniform vec2 u_flow;
uniform float u_time;
uniform float u_relief;
uniform vec4 u_swell;

uint lowbias(uvec2 p) {
  uint h = (p.x * 0x8da6b343u) ^ (p.y * 0xd8163841u);
  h ^= h >> 16; h *= 0x7feb352du; h ^= h >> 15; h *= 0x846ca68bu; h ^= h >> 16;
  return h;
}
vec2 gradient(ivec2 c) {
  float a = float(lowbias(uvec2(c + 32768))) * 1.4629180792671596e-9;
  return vec2(cos(a), sin(a));
}
float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = p - i;
  ivec2 c = ivec2(i);
  vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
  float a = dot(gradient(c), f);
  float b = dot(gradient(c + ivec2(1, 0)), f - vec2(1.0, 0.0));
  float d = dot(gradient(c + ivec2(0, 1)), f - vec2(0.0, 1.0));
  float e = dot(gradient(c + ivec2(1, 1)), f - vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(d, e, u.x), u.y) * 1.41;
}
float fbm(vec2 p) {
  float s = 0.0;
  float a = 0.55;
  for (int k = 0; k < 4; k++) {
    s += a * noise(p);
    p = mat2(0.8, -0.6, 0.6, 0.8) * p * 2.02 + vec2(17.3, -9.1);
    a *= 0.47;
  }
  return s;
}
// The floor's height under a point of the ground: x across, z ahead of the camera.
float floorAt(vec2 g) {
  vec2 p = (g + u_flow) * 0.1;
  vec2 warp = vec2(noise(p * 0.5 + vec2(0.0, u_time * 0.011)), noise(p * 0.5 + vec2(5.2, 1.3) - u_time * 0.009));
  float h = fbm(p + warp * 0.9);
  h = h * 0.7 + (0.35 - abs(noise(p * 0.6 + 3.7))) * 0.45;
  float shelf = 0.6 + 0.4 * smoothstep(1.0, 10.0, g.y);
  float d = length(g - u_swell.xy) - u_swell.z;
  return h * u_relief * shelf * 1.6 + u_swell.w * exp(-d * d * 0.6);
}
`;

const FLOOR_VS = `#version 300 es
precision highp float;
precision highp int;
in vec2 a_grid;
uniform mat4 u_viewProj;
uniform float u_spread;
uniform float u_near;
uniform float u_far;
${FLOOR}
out vec3 v_ground;
out vec3 v_normal;
void main() {
  float z = u_near + (u_far - u_near) * pow(a_grid.y, 1.75);
  vec2 g = vec2(a_grid.x * (z * u_spread + 2.0), z);
  float h = floorAt(g);
  float e = 0.05 + z * 0.012;
  v_normal = normalize(vec3(h - floorAt(g + vec2(e, 0.0)), e, h - floorAt(g + vec2(0.0, e))));
  v_ground = vec3(g.x, h, z);
  gl_Position = u_viewProj * vec4(g.x, h, -z, 1.0);
}`;

// The contours come from the floor's height at each pixel, not the mesh's (which is straight between
// vertices), so they stay smooth curves however close the camera is.
const FLOOR_FS = `#version 300 es
precision highp float;
precision highp int;
in vec3 v_ground;
in vec3 v_normal;
uniform vec3 u_ink;
uniform vec3 u_lamp;
uniform vec3 u_ground;
uniform vec2 u_cap;
uniform float u_far;
uniform vec3 u_pool;
uniform float u_poolGain;
${FLOOR}
out vec4 color;

// A contour at every whole value of v, about "width" pixels wide and anti-aliased; it fades away
// where the lines crowd together toward the horizon.
float contour(float v, float width) {
  float w = fwidth(v);
  float d = abs(fract(v - 0.5) - 0.5) / max(w, 1e-5);
  return (1.0 - smoothstep(width * 0.5 - 0.5, width * 0.5 + 0.5, d)) * (1.0 - smoothstep(0.07, 0.2, w));
}

void main() {
  float v = floorAt(v_ground.xz) * 12.0;
  float minor = contour(v, 0.9);
  float index = contour(v / 5.0, 1.25);
  // Light from the upper left, behind the viewer: slopes facing it read lighter, so the banks have form.
  float lit = clamp(dot(normalize(v_normal), normalize(vec3(-0.45, 0.75, -0.5))), 0.0, 1.0);
  vec2 q = (v_ground.xz - u_pool.xy) / u_pool.z;
  float pool = exp(-dot(q, q) * 1.4) * u_poolGain;
  float ring = 0.0;
  if (u_swell.w > 0.0) {
    float r = length(v_ground.xz - u_swell.xy) - u_swell.z;
    ring = exp(-r * r * 0.5) * u_swell.w * 6.0;
  }
  float dist = v_ground.z;
  float fog = 1.0 - smoothstep(u_far * 0.42, u_far * 0.98, dist);
  float lines = (minor * 0.62 + index) * (0.4 + 0.6 * lit);
  float aLine = clamp(lines * (0.62 + 0.7 * pool + ring) * fog, 0.0, 1.0) * u_cap.y;
  // The wash: the lamp's pool and the swell in the lamp's colour, and the lit slopes in the ink's, all
  // within the wash cap together.
  float lampWash = clamp(pool * 0.75 + ring * 0.6, 0.0, 1.0);
  float slope = smoothstep(0.55, 0.95, lit) * (1.0 - lampWash);
  float aWash = clamp((lampWash + slope * 0.7) * fog, 0.0, 1.0) * u_cap.x;
  vec3 washColor = mix(u_ink, u_lamp, lampWash / max(lampWash + slope * 0.7, 1e-4));
  vec3 lineColor = mix(u_ink, u_lamp, clamp(pool * 0.8 + ring, 0.0, 1.0));
  float fill = 0.94 * (1.0 - smoothstep(u_far * 0.5, u_far, dist));
  float rest = (1.0 - aLine) * (1.0 - aWash);
  color = vec4(lineColor * aLine + washColor * aWash * (1.0 - aLine) + u_ground * fill * rest,
               aLine + aWash * (1.0 - aLine) + fill * rest);
}`;

// A haze along the horizon, brighter above the pool; the floor's own fill hides it below the line.
const HAZE_VS = `#version 300 es
const vec2 P[3] = vec2[3](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
out vec2 v_clip;
void main() {
  v_clip = P[gl_VertexID];
  gl_Position = vec4(P[gl_VertexID], 0.0, 1.0);
}`;

const HAZE_FS = `#version 300 es
precision highp float;
in vec2 v_clip;
uniform float u_horizon;
uniform float u_poolX;
uniform float u_poolGain;
uniform vec3 u_lamp;
uniform vec2 u_cap;
out vec4 color;
void main() {
  float d = v_clip.y - u_horizon;
  float haze = d > 0.0 ? exp(-d * 4.0) : exp(d * 12.0);
  float dx = v_clip.x - u_poolX;
  float glow = exp(-dx * dx * 1.1);
  float a = clamp(haze * (0.45 + 0.4 * glow * u_poolGain), 0.0, 1.0) * u_cap.x;
  color = vec4(u_lamp * a, a);
}`;

// Motes in the water: dark until the lamp's pool reaches them.
const MOTE_VS = `#version 300 es
precision highp float;
precision highp int;
in vec4 a_mote;
uniform mat4 u_viewProj;
uniform float u_spread;
uniform float u_near;
uniform float u_far;
uniform float u_px;
uniform vec3 u_pool;
uniform float u_poolGain;
${FLOOR}
out float v_light;
void main() {
  float span = u_far * 0.55;
  float travel = mod(a_mote.z * span - u_flow.y * 1.15, span);
  float z = u_near + 0.8 + travel;
  float x = a_mote.x * (z * u_spread * 0.8 + 1.0);
  vec2 g = vec2(x, z);
  float y = floorAt(g) + 0.15 + a_mote.y * 1.1 + sin(u_time * 0.35 + a_mote.w * 6.2831) * 0.08;
  vec4 clip = u_viewProj * vec4(x, y, -z, 1.0);
  gl_Position = clip;
  vec2 q = (g - u_pool.xy) / u_pool.z;
  float edge = smoothstep(0.0, 2.0, travel) * (1.0 - smoothstep(span * 0.8, span, travel));
  float twinkle = 0.6 + 0.4 * sin(u_time * (0.7 + a_mote.w) + a_mote.w * 40.0);
  v_light = exp(-dot(q, q) * 1.6) * u_poolGain * edge * twinkle;
  gl_PointSize = clamp(u_px * 26.0 / max(clip.w, 0.1), 1.0, 4.0 * u_px);
}`;

const MOTE_FS = `#version 300 es
precision highp float;
in float v_light;
uniform vec3 u_lamp;
uniform vec2 u_cap;
out vec4 color;
void main() {
  vec2 c = gl_PointCoord * 2.0 - 1.0;
  float r = dot(c, c);
  if (r > 1.0) discard;
  float a = (1.0 - r) * clamp(v_light, 0.0, 1.0) * u_cap.y;
  color = vec4(u_lamp * a, a);
}`;

// --- small WebGL helpers ---

interface Program {
  program: WebGLProgram;
  u(name: string): WebGLUniformLocation | null;
}

function compile(gl: WebGL2RenderingContext, vs: string, fs: string): Program | null {
  const shader = (type: number, src: string): WebGLShader | null => {
    const s = gl.createShader(type);
    if (!s) return null;
    gl.shaderSource(s, src);
    gl.compileShader(s);
    return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
  };
  const v = shader(gl.VERTEX_SHADER, vs);
  const f = shader(gl.FRAGMENT_SHADER, fs);
  const program = gl.createProgram();
  if (!v || !f || !program) return null;
  gl.attachShader(program, v);
  gl.attachShader(program, f);
  gl.linkProgram(program);
  gl.deleteShader(v);
  gl.deleteShader(f);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  const cache = new Map<string, WebGLUniformLocation | null>();
  return {
    program,
    u(name) {
      if (!cache.has(name)) cache.set(name, gl.getUniformLocation(program, name));
      return cache.get(name) ?? null;
    },
  };
}

interface Scene {
  gl: WebGL2RenderingContext;
  floor: Program;
  haze: Program;
  motes: Program;
  floorVao: WebGLVertexArrayObject;
  hazeVao: WebGLVertexArrayObject;
  moteVao: WebGLVertexArrayObject;
  indexCount: number;
  moteCount: number;
}

function buildScene(gl: WebGL2RenderingContext, coarse: boolean): Scene | null {
  const floor = compile(gl, FLOOR_VS, FLOOR_FS);
  const haze = compile(gl, HAZE_VS, HAZE_FS);
  const motes = compile(gl, MOTE_VS, MOTE_FS);
  const floorVao = gl.createVertexArray();
  const hazeVao = gl.createVertexArray();
  const moteVao = gl.createVertexArray();
  if (!floor || !haze || !motes || !floorVao || !hazeVao || !moteVao) return null;

  const size = gridFor(coarse);
  const grid = buildGrid(size.cols, size.rows);
  gl.bindVertexArray(floorVao);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, grid.vertices, gl.STATIC_DRAW);
  const aGrid = gl.getAttribLocation(floor.program, "a_grid");
  gl.enableVertexAttribArray(aGrid);
  gl.vertexAttribPointer(aGrid, 2, gl.FLOAT, false, 0, 0);
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, grid.indices, gl.STATIC_DRAW);

  gl.bindVertexArray(moteVao);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, buildMotes(size.motes), gl.STATIC_DRAW);
  const aMote = gl.getAttribLocation(motes.program, "a_mote");
  gl.enableVertexAttribArray(aMote);
  gl.vertexAttribPointer(aMote, 4, gl.FLOAT, false, 0, 0);
  gl.bindVertexArray(null);

  return { gl, floor, haze, motes, floorVao, hazeVao, moteVao, indexCount: grid.indices.length, moteCount: size.motes };
}

/** The GPU's name. Firefox gives it plainly (and warns about the debug extension); Chromium needs the extension. */
function rendererName(gl: WebGL2RenderingContext): string {
  const plain = String(gl.getParameter(gl.RENDERER) ?? "");
  if (plain && !/^webkit webgl$/i.test(plain)) return plain;
  const info = gl.getExtension("WEBGL_debug_renderer_info");
  return info ? String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL) ?? "") : plain;
}

// --- the controller ---

export interface Backdrop {
  /** The "Show the chart behind the pages" setting. */
  setEnabled(on: boolean): void;
}

interface Colors {
  ink: Rgb;
  lamp: Rgb;
  ground: Rgb;
  cap: [number, number]; // wash, line
}

const DRIFT = 0.42; // scene units a second toward the viewer, at an idle mood
const CAMERA_TAU = 650; // ms; a page change settles in about two seconds
const MOOD_TAU = 900;

export function mountBackdrop(host: HTMLElement): Backdrop {
  const root = document.documentElement;
  const body = document.body;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const fine = matchMedia("(pointer: fine)");
  const dark = matchMedia("(prefers-color-scheme: dark)");
  const coarse = !fine.matches;
  const governor = createGovernor();

  let enabled = false;
  let canvas: HTMLCanvasElement | null = null;
  let scene: Scene | null = null;
  let colors: Colors | null = null;
  let raf = 0;
  let lastFrame = 0;
  let lastActivity = performance.now();
  let swellStart = -Infinity;

  let page: Page = current();
  let voice: Voice = (body.dataset.voice as Voice | undefined) ?? "idle";
  let cam: Camera = { ...CAMERAS[page] };
  let mood: Mood = { ...MOODS[voice] };
  const aim = { x: 0, y: 0 }; // where the pointer is, -1..1
  const lean = { x: 0, y: 0 }; // how far the camera has leaned toward it
  // A start that differs by the hour, so the floor is not the same each visit.
  let clock = (Date.now() / 1000) % 3600;
  const flow = { x: clock * 0.05, z: clock * DRIFT };

  // Resting: the machine draws on the CPU, or could not keep up even at the lowest resolution. The
  // scene then holds still as it does for Calm, for the rest of the visit.
  let resting = false;
  const still = (): boolean => reduced.matches || root.dataset.motion === "calm" || resting;
  // "on" animates, "still" shows one frame, "off" leaves the page to the CSS chart room.
  const state = (s: "on" | "still" | "off"): void => {
    if (body.dataset.backdrop !== s) body.dataset.backdrop = s;
  };

  function readColors(): void {
    const css = getComputedStyle(root);
    const scheme = css.getPropertyValue("color-scheme").includes("light") ? "light" : "dark";
    const ink = parseColor(css.getPropertyValue("--text"));
    const lamp = parseColor(css.getPropertyValue("--signal"));
    const ground = parseColor(css.getPropertyValue("--bg"));
    colors = ink && lamp && ground ? { ink, lamp, ground, cap: [CAPS[scheme].wash, CAPS[scheme].line] } : null;
  }

  function resize(): void {
    if (!canvas || !scene) return;
    const ratio = Math.max(0.5, Math.min(window.devicePixelRatio || 1, 1.5) * governor.scale());
    const w = Math.max(1, Math.round(canvas.clientWidth * ratio));
    const h = Math.max(1, Math.round(canvas.clientHeight * ratio));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    scene.gl.viewport(0, 0, w, h);
  }

  function target(): Camera {
    const base = CAMERAS[page];
    return { ...base, yaw: base.yaw + lean.x * 0.035, pitch: base.pitch + lean.y * 0.022 };
  }

  function draw(now: number): void {
    if (!scene || !canvas || !colors) return;
    const { gl, floor, haze, motes } = scene;
    const tint = colors;
    const aspect = canvas.width / Math.max(1, canvas.height);
    const vp = viewProjection(cam, aspect);
    const pool = poolAt(clock);
    const swell = swellAt(now - swellStart);
    const sp = spread(cam, aspect);
    const ratio = canvas.width / Math.max(1, canvas.clientWidth);

    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA); // premultiplied

    // The haze, behind everything.
    gl.disable(gl.DEPTH_TEST);
    gl.useProgram(haze.program);
    gl.uniform1f(haze.u("u_horizon"), horizonY(cam));
    gl.uniform1f(haze.u("u_poolX"), project(vp, pool.x, 0, -pool.z)[0]);
    gl.uniform1f(haze.u("u_poolGain"), mood.pool);
    gl.uniform3fv(haze.u("u_lamp"), tint.lamp);
    gl.uniform2fv(haze.u("u_cap"), tint.cap);
    gl.bindVertexArray(scene.hazeVao);
    gl.drawArrays(gl.TRIANGLES, 0, 3);

    const shared = (p: Program): void => {
      gl.uniformMatrix4fv(p.u("u_viewProj"), false, vp);
      gl.uniform1f(p.u("u_spread"), sp);
      gl.uniform1f(p.u("u_near"), NEAR);
      gl.uniform1f(p.u("u_far"), FAR);
      gl.uniform2f(p.u("u_flow"), flow.x, flow.z);
      gl.uniform1f(p.u("u_time"), clock);
      gl.uniform1f(p.u("u_relief"), mood.relief);
      gl.uniform4f(p.u("u_swell"), pool.x, pool.z, swell?.radius ?? 0, swell?.strength ?? 0);
      gl.uniform3f(p.u("u_pool"), pool.x, pool.z, pool.radius);
      gl.uniform1f(p.u("u_poolGain"), mood.pool);
      gl.uniform3fv(p.u("u_lamp"), tint.lamp);
      gl.uniform2fv(p.u("u_cap"), tint.cap);
    };

    // The floor, near rows first, so each bank hides what lies behind it.
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LESS);
    gl.depthMask(true);
    gl.useProgram(floor.program);
    shared(floor);
    gl.uniform3fv(floor.u("u_ink"), tint.ink);
    gl.uniform3fv(floor.u("u_ground"), tint.ground);
    gl.bindVertexArray(scene.floorVao);
    gl.drawElements(gl.TRIANGLES, scene.indexCount, gl.UNSIGNED_SHORT, 0);

    // The motes: tested against the floor, never written to it.
    gl.depthMask(false);
    gl.useProgram(motes.program);
    shared(motes);
    gl.uniform1f(motes.u("u_px"), ratio);
    gl.bindVertexArray(scene.moteVao);
    gl.drawArrays(gl.POINTS, 0, scene.moteCount);
    gl.depthMask(true);
    gl.bindVertexArray(null);
  }

  function step(dt: number): void {
    const s = dt / 1000;
    const k = 1 - Math.exp(-dt / 450);
    lean.x += (aim.x - lean.x) * k;
    lean.y += (aim.y - lean.y) * k;
    cam = approachCamera(cam, target(), dt, CAMERA_TAU);
    mood = approachMood(mood, MOODS[voice], dt, MOOD_TAU);
    flow.z += s * DRIFT * mood.drift;
    flow.x += s * 0.05 * mood.drift;
    clock += s;
  }

  /** Under reduced motion or Calm: the page's framing and the mood, at once, in one frame. */
  function drawStill(): void {
    if (!scene) return;
    lean.x = lean.y = aim.x = aim.y = 0;
    cam = { ...CAMERAS[page] };
    mood = { ...MOODS[voice] };
    swellStart = -Infinity;
    resize();
    draw(performance.now());
  }

  function tick(now: number): void {
    raf = 0;
    if (!scene) return;
    const interval = frameInterval({ hidden: document.hidden, still: still(), sinceActivity: now - lastActivity, voice });
    if (interval === null) {
      lastFrame = 0;
      return;
    }
    if (!lastFrame || now - lastFrame >= interval - 4) {
      const verdict = lastFrame ? governor.frame(now - lastFrame, interval) : null;
      if (verdict === "lower") resize();
      if (verdict === "rest") {
        resting = true;
        wake();
        return;
      }
      step(lastFrame ? Math.min(now - lastFrame, 250) : 16);
      lastFrame = now;
      draw(now);
    }
    raf = requestAnimationFrame(tick);
  }

  /** Something changed: draw it, by the loop if motion is allowed, as one still frame if not. */
  function wake(): void {
    if (!scene || document.hidden) return;
    if (still()) {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      lastFrame = 0;
      state("still");
      drawStill();
    } else {
      state("on");
      if (!raf) raf = requestAnimationFrame(tick);
    }
  }

  // The pointer, a key or a scroll keeps the loop at full pace; it never redraws a still frame.
  const activity = (): void => {
    lastActivity = performance.now();
    if (!still()) wake();
  };

  // A change the scene shows (the page, the voice): the loop picks it up, or one still frame does.
  const changed = (): void => {
    lastActivity = performance.now();
    wake();
  };

  function onPointer(e: PointerEvent): void {
    if (fine.matches && !still()) {
      aim.x = Math.max(-1, Math.min(1, (e.clientX / Math.max(1, innerWidth)) * 2 - 1));
      aim.y = Math.max(-1, Math.min(1, (e.clientY / Math.max(1, innerHeight)) * 2 - 1));
    }
    activity();
  }

  function onLost(e: Event): void {
    e.preventDefault(); // ask for it back
    scene = null;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    state("off");
  }

  function onRestored(): void {
    const gl = canvas?.getContext("webgl2");
    scene = gl ? buildScene(gl, coarse) : null;
    if (!scene) return;
    governor.reset();
    resize();
    wake();
  }

  function stop(): void {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    if (canvas) {
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      // Hand the GPU memory back now rather than at garbage collection.
      scene?.gl.getExtension("WEBGL_lose_context")?.loseContext();
      canvas.remove();
    }
    canvas = null;
    scene = null;
    state("off");
  }

  function start(): void {
    canvas = document.createElement("canvas");
    canvas.className = "backdrop";
    canvas.setAttribute("aria-hidden", "true");
    host.append(canvas);
    const gl = canvas.getContext("webgl2", {
      alpha: true,
      premultipliedAlpha: true,
      antialias: false, // the contours anti-alias themselves
      depth: true,
      stencil: false,
      powerPreference: "low-power",
      preserveDrawingBuffer: false,
    });
    scene = gl ? buildScene(gl, coarse) : null;
    readColors();
    if (!gl || !scene || !colors) {
      stop();
      return;
    }
    resting = isSoftwareRenderer(rendererName(gl));
    canvas.addEventListener("webglcontextlost", onLost);
    canvas.addEventListener("webglcontextrestored", onRestored);
    governor.reset();
    resize();
    lastFrame = 0;
    // The first frame is drawn before the canvas fades in, so it never shows empty.
    if (!still()) {
      step(16);
      draw(performance.now());
    }
    wake();
  }

  // The voice state, the theme and the motion setting are read from the page itself, so nothing
  // else has to remember to tell the backdrop.
  new MutationObserver(() => {
    const next = (body.dataset.voice as Voice | undefined) ?? "idle";
    if (next === voice) return;
    if (next === "listening") swellStart = performance.now();
    voice = next;
    changed();
  }).observe(body, { attributes: true, attributeFilter: ["data-voice"] });

  const restyle = (): void => {
    if (!scene) return;
    readColors();
    wake();
  };
  new MutationObserver(restyle).observe(root, { attributes: true, attributeFilter: ["data-theme", "data-motion"] });
  dark.addEventListener("change", restyle);
  reduced.addEventListener("change", restyle);

  onRoute((route) => {
    page = route;
    changed();
  });

  window.addEventListener("pointermove", onPointer, { passive: true });
  window.addEventListener("pointerdown", activity, { passive: true });
  window.addEventListener("keydown", activity, { passive: true });
  window.addEventListener("scroll", activity, { passive: true });
  window.addEventListener("resize", () => {
    if (!scene) return;
    governor.reset();
    resize();
    if (still()) drawStill();
  });
  document.addEventListener("visibilitychange", () => {
    lastFrame = 0;
    if (!document.hidden) wake();
  });

  return {
    setEnabled(on: boolean): void {
      if (on === enabled) return;
      enabled = on;
      if (on) start();
      else stop();
    },
  };
}
