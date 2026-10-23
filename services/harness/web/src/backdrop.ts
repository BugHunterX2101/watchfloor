// The chart below the page (ADR-035): an imaginary drowned coast, behind every page, drawn with
// WebGL2 and no library. The sea floor drifts toward the viewer in depth contours, shaded by the
// light and by depth; nearer banks hide the ones behind them; a glowing line marks the waterline;
// a graticule of chart crosses floats in the datum plane above the floor and slips over it at its
// own pace; a pool of the lamp's colour wanders across the water and lights its caustics and the
// motes; shafts of light slant down through it all. It is decoration only: aria-hidden, no pointer
// events, no data, and every area of it is the lamp's hue no brighter (no darker, in the light
// theme) than CAPS says in backdrop-logic.ts, so text keeps its contrast anywhere over it.
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
  tintOf,
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
//
// The scene is a chart of a drowned coast under a datum: everything below the datum is sea, tinted
// by depth (pale at the shore, deepening out), lit from the upper left so the banks have form, with
// the lamp's caustics wandering over the shallows. A glowing line marks the datum itself, and a
// graticule of chart crosses floats in the datum plane, between you and the sea floor, so it slips
// over the banks at its own pace as the camera moves. Every area is the lamp's hue at a luminance
// no brighter (or darker, in the light theme) than the cap's tint, so text keeps its contrast.
const FLOOR_FS = `#version 300 es
precision highp float;
precision highp int;
in vec3 v_ground;
in vec3 v_normal;
uniform vec3 u_ink;
uniform vec3 u_lamp;
uniform vec3 u_deep;
uniform vec3 u_glint;
uniform vec3 u_ground;
uniform vec2 u_cap;
uniform float u_far;
uniform float u_dark;
uniform float u_camH;
uniform float u_vivid;
uniform vec3 u_pool;
uniform float u_poolGain;
${FLOOR}
out vec4 color;

const float DATUM = 0.5; // the waterline, in contour units

// A contour at every whole value of v, about "width" pixels wide and anti-aliased; it fades away
// where the lines crowd together toward the horizon.
float contour(float v, float width) {
  float w = fwidth(v);
  float d = abs(fract(v - 0.5) - 0.5) / max(w, 1e-5);
  return (1.0 - smoothstep(width * 0.5 - 0.5, width * 0.5 + 0.5, d)) * (1.0 - smoothstep(0.07, 0.2, w));
}

// Light on the sea floor: the thin bright network that sunlight makes through waves.
float caustics(vec2 p, float t) {
  vec2 q = mod(p * 6.28318, 6.28318) - 250.0;
  vec2 i = q;
  float c = 1.0;
  for (int n = 0; n < 4; n++) {
    float tt = t * (1.0 - 3.5 / float(n + 1));
    i = q + vec2(cos(tt - i.x) + sin(tt + i.y), sin(tt - i.y) + cos(tt + i.x));
    c += 1.0 / length(vec2(q.x / (sin(i.x + tt) / 0.005), q.y / (cos(i.y + tt) / 0.005)));
  }
  c = 1.17 - pow(c / 4.0, 1.4);
  c = pow(abs(c), 8.0);
  return isnan(c) ? 0.0 : clamp(c, 0.0, 1.0);
}

void main() {
  float h = floorAt(v_ground.xz);
  float v = h * 12.0;
  float minor = contour(v, 0.9);
  float index = contour(v / 5.0, 1.25);
  float wd = fwidth(v);
  float crowd = 1.0 - smoothstep(0.07, 0.2, wd);
  // Light from the upper left, behind the viewer: slopes facing it return more light.
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

  // The sea: below the datum, deepest tint where the water is deep, a pale rim along the shore.
  float under = DATUM - v;
  float water = smoothstep(-0.5, 0.7, under);
  float shallow = exp(-max(under, 0.0) * 0.16);
  float rel = mix(1.0 - lit, lit, u_dark); // the light each slope returns, in either theme
  float land = 0.06 + 0.42 * smoothstep(0.3, 0.95, rel);
  float sea = 0.3 + 0.5 * shallow + 0.14 * rel;
  float caust = water > 0.01 ? caustics((v_ground.xz + u_flow) * 0.17, u_time * 0.32) : 0.0;
  float cl = caust * (0.12 + 0.88 * pool) * water * (0.3 + 0.7 * shallow);
  float light = clamp(mix(land, sea, water) + pool * 0.3 + ring * 0.5 + cl * 0.6, 0.0, 1.0) * fog * u_vivid;
  float glint = clamp(pool * 0.55 + cl * 1.1 + ring * 0.6, 0.0, 1.0);

  // The shore: the contour at the datum itself, drawn heavier than the rest, in the lamp's colour.
  float shore = (1.0 - smoothstep(1.0, 2.2, abs(under) / max(wd, 1e-5))) * crowd;

  // The datum plane's graticule: where the line from the eye to this bit of sea floor crosses the
  // plane, a cross at every cell and a fine rule every four. It exists only over the water.
  float grat = 0.0;
  if (under > 0.0 && u_camH > DATUM / 12.0 + 0.25) {
    float t = (u_camH - DATUM / 12.0) / (u_camH - h);
    vec2 u = (vec2(t * v_ground.x, t * v_ground.z) + u_flow * 0.55) * 0.5;
    vec2 f = abs(fract(u - 0.5) - 0.5);
    vec2 w = max(fwidth(u), vec2(1e-5));
    vec2 ln = 1.0 - smoothstep(0.5, 1.5, f / w);
    float tick = 0.16;
    float cross = max(ln.x * (1.0 - smoothstep(tick * 0.6, tick, f.y)), ln.y * (1.0 - smoothstep(tick * 0.6, tick, f.x)));
    vec2 fm = abs(fract(u * 0.25 - 0.5) - 0.5);
    vec2 lm = 1.0 - smoothstep(0.5, 1.5, fm / (w * 0.25));
    float legible = 1.0 - smoothstep(0.08, 0.3, max(w.x, w.y));
    grat = (cross + 0.3 * max(lm.x, lm.y)) * legible * smoothstep(0.0, 0.8, under);
  }

  float lines = (minor * 0.55 + index) * (0.4 + 0.6 * lit);
  float aLine = clamp(lines * (0.62 + 0.7 * pool + ring) * fog, 0.0, 1.0) * u_cap.y;
  float aGrat = clamp(grat, 0.0, 1.0) * fog * u_cap.y * 0.9;
  float aShore = shore * fog * u_cap.y;
  vec3 lineColor = mix(u_ink, u_lamp, clamp(pool * 0.8 + ring, 0.0, 1.0));

  // Wash over the ground's fill, then the hairlines over both: the contours, the grid, the shore.
  float aWash = light * u_cap.x;
  vec3 washColor = mix(u_deep, u_glint, glint);
  float fill = 0.94 * (1.0 - smoothstep(u_far * 0.5, u_far, dist));
  vec4 c = vec4(washColor * aWash + u_ground * fill * (1.0 - aWash), aWash + fill * (1.0 - aWash));
  c = vec4(lineColor * aLine, aLine) + c * (1.0 - aLine);
  c = vec4(u_lamp * aGrat, aGrat) + c * (1.0 - aGrat);
  c = vec4(u_lamp * aShore, aShore) + c * (1.0 - aShore);
  color = c;
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
uniform float u_vivid;
uniform vec3 u_glint;
uniform vec2 u_cap;
out vec4 color;
void main() {
  float d = v_clip.y - u_horizon;
  float haze = d > 0.0 ? exp(-d * 2.6) : exp(d * 12.0);
  float dx = v_clip.x - u_poolX;
  float glow = exp(-dx * dx * 1.1);
  float a = clamp(haze * (0.5 + 0.5 * glow * u_poolGain), 0.0, 1.0) * u_vivid * u_cap.x;
  color = vec4(u_glint * a, a);
}`;

// Shafts of light slanting down through the water, over everything, brightest at the top: slow
// bands whose edges drift, so the surface above seems to move.
const SHAFT_FS = `#version 300 es
precision highp float;
in vec2 v_clip;
uniform float u_time;
uniform float u_poolX;
uniform float u_poolGain;
uniform float u_vivid;
uniform vec3 u_glint;
uniform vec2 u_cap;
out vec4 color;
void main() {
  float a = v_clip.x * 0.9 - v_clip.y * 0.5 + u_poolX * 0.04;
  float s = 0.5 + 0.5 * sin(a * 11.0 + sin(a * 4.0 + u_time * 0.07) * 1.6 + u_time * 0.025);
  s = pow(s, 4.0) * (0.55 + 0.45 * sin(a * 3.1 - u_time * 0.045));
  float fade = smoothstep(-0.7, 1.0, v_clip.y);
  float k = s * fade * (0.5 + 0.35 * u_poolGain) * u_vivid * u_cap.x * 0.42;
  color = vec4(u_glint * k, k);
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
  shaft: Program;
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
  const shaft = compile(gl, HAZE_VS, SHAFT_FS);
  const motes = compile(gl, MOTE_VS, MOTE_FS);
  const floorVao = gl.createVertexArray();
  const hazeVao = gl.createVertexArray();
  const moteVao = gl.createVertexArray();
  if (!floor || !haze || !shaft || !motes || !floorVao || !hazeVao || !moteVao) return null;

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

  return { gl, floor, haze, shaft, motes, floorVao, hazeVao, moteVao, indexCount: grid.indices.length, moteCount: size.motes };
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
  deep: Rgb; // the open sea: the lamp's hue, pushed to a clear blue, at the cap's luminance
  glint: Rgb; // the lamp's own saturation at the same luminance: its pool, the caustics, the shafts
  dark: number; // 1 in the dark theme
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
    const cap = CAPS[scheme];
    const toward: Rgb = scheme === "dark" ? [0, 0, 0] : [1, 1, 1];
    colors =
      ink && lamp && ground
        ? {
            ink,
            lamp,
            ground,
            deep: tintOf(lamp, cap.tint, toward, cap.sea),
            glint: tintOf(lamp, cap.tint, toward),
            dark: scheme === "dark" ? 1 : 0,
            cap: [cap.wash, cap.line],
          }
        : null;
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
    const { gl, floor, haze, shaft, motes } = scene;
    const tint = colors;
    const aspect = canvas.width / Math.max(1, canvas.height);
    const vp = viewProjection(cam, aspect);
    const pool = poolAt(clock);
    const swell = swellAt(now - swellStart);
    const sp = spread(cam, aspect);
    const ratio = canvas.width / Math.max(1, canvas.clientWidth);
    const vivid = Math.max(0.6, Math.min(1, 0.55 + 0.45 * mood.pool)); // an error dims the sea

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
    gl.uniform1f(haze.u("u_vivid"), vivid);
    gl.uniform3fv(haze.u("u_glint"), tint.glint);
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
      gl.uniform3fv(p.u("u_deep"), tint.deep);
      gl.uniform3fv(p.u("u_glint"), tint.glint);
      gl.uniform1f(p.u("u_dark"), tint.dark);
      gl.uniform1f(p.u("u_camH"), cam.height);
      gl.uniform1f(p.u("u_vivid"), vivid);
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

    // The shafts, over everything.
    gl.disable(gl.DEPTH_TEST);
    gl.useProgram(shaft.program);
    gl.uniform1f(shaft.u("u_time"), clock);
    gl.uniform1f(shaft.u("u_poolX"), project(vp, pool.x, 0, -pool.z)[0]);
    gl.uniform1f(shaft.u("u_poolGain"), mood.pool);
    gl.uniform1f(shaft.u("u_vivid"), vivid);
    gl.uniform3fv(shaft.u("u_glint"), tint.glint);
    gl.uniform2fv(shaft.u("u_cap"), tint.cap);
    gl.bindVertexArray(scene.hazeVao);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
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
