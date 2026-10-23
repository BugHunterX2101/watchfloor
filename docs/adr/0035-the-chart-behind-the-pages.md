# ADR-035: The chart behind the pages

- **Status:** Accepted (2026-10-23)
- **Supersedes in part:** ADR-032 §4 ("no canvas, no WebGL"; the scope itself stays SVG and
  library-free)
- **Refines:** ADR-017 (visual design)

## Context

ADR-032 replaced the 3D orb and floor with the night scope and left the pages on a flat CSS "chart
room": a graticule and a soft lamp glow. The scope works, but the room around it was a sheet of
paper, the same on every page and still whatever the assistant was doing. The request was for a
background with depth and life that belongs to the product, not a stock gradient or particle field.

ADR-032's reasons still hold: motion must not be the content, every fact must survive without colour,
motion or sight, and the page is often left running on modest machines. So the background may carry
atmosphere and nothing else.

## Decision

1. **A sounding chart seen from a low bridge.** `src/backdrop.ts` draws an imaginary sea floor in
   depth contours behind every page, with WebGL2 and no library. The floor is gradient noise (an
   integer hash, so it is the same on every GPU) with a slow domain warp and folded ridges, so
   contours bunch on the crests as soundings do on a bank. Heights are taken per pixel, so contours
   stay smooth curves; they are anti-aliased and fade where they crowd toward the horizon. Depth
   testing lets nearer banks hide the lines behind them. A haze lies along the horizon, and motes in
   the water are dark until the lamp's pool reaches them.
2. **Only the sweep turns.** The floor drifts toward the viewer and a pool of the lamp's colour
   wanders across it over minutes (`poolAt`). Nothing in the backdrop rotates, so the scope's sweep
   stays the one turning thing on screen.
3. **The voice state is a mood, not a beat.** `body[data-voice]` eases the scene (`MOODS`): listening
   hushes the drift and turns the lamp up, with one swell as it starts (`SWELL_MS`, 3.6 s); thinking
   hurries the drift; speaking brightens the pool; an error dims it. The state is still written in
   `#voice-state`; the backdrop only echoes it.
4. **Each page has its own framing** (`CAMERAS`), eased over about two seconds: Watch looks out to a
   horizon in the top third, Pipeline leans over the chart table with no horizon, and Orders,
   Activity and Guide sit between. A fine pointer leans the camera a little; touch does not.
5. **Text keeps its contrast, by test.** The shaders clamp to `CAPS`. A wash (pool and haze) may move
   the page at most 3% in dark and 7% in light, and `test/backdrop.test.mjs` proves `--faint`,
   `--muted` and `--text` keep 4.5:1 over the brightest page colour with it, reading the tokens from
   `styles.css` so a palette change re-runs the proof. Contours and motes are hairlines capped at 22%
   (dark) and 16% (light), like the CSS graticule they replace, and never a field behind a word.
6. **It never runs faster than it must.** 30 frames a second while something is happening, 10 after
   20 s without input; full pace again whenever the assistant is listening, thinking or speaking.
   None in a hidden tab. One still frame under `prefers-reduced-motion` or Calm, redrawn only when
   the page, theme or voice changes. The device pixel ratio is capped at 1.5, and phones get a
   coarser mesh and fewer motes (`gridFor`).
7. **A slow machine gets less, then a still frame.** A time-based governor (`createGovernor`) steps
   the resolution through 1, 0.75, 0.55 and 0.4 once frames have been late by 1.5 s in total, then
   rests on a still frame. A throttled tab or an occasional late frame does not count. Software
   renderers (SwiftShader, llvmpipe, Microsoft Basic Render) are known by name and start at rest.
8. **It is a setting and a voice command.** Settings has "Show the chart behind the pages"
   (`backdrop`, on by default; stored and sanitized in `store.ts`). "Hide the background" (also
   *backdrop*, *scene*, *floor*) maps to `set_backdrop`; *sweep*, *radar* and *plot* now mean the
   scope's animation (`set_floor`), and *orb* and *lamp* the lamp (`set_orb`). `control.py` lists
   `set_backdrop`, so the model can propose it too.
9. **The CSS chart room is the fallback.** `body[data-backdrop]` is `on`, `still` or `off`. The CSS
   graticule fades out only while the chart shows. With WebGL2 missing, a context lost and not
   restored, a shader that fails, or the setting off, the page is the ADR-032 room again. The chunk
   is loaded by a dynamic import when the browser is idle, so it never delays the first paint.

## Consequences

- The pages have a place in them: one product-specific scene that changes with the page and the
  assistant, and stays behind the words.
- The scene is its own 16.5 KB chunk (6.7 KB gzipped), loaded after first paint; the main bundle
  gains only the lazy loader and the setting.
- `backdrop-logic.ts` holds every number (camera, moods, caps, pacing, governor, geometry) free of
  the DOM, so `npm test` checks them.
- The page actions are now 31; `actions.ts` and `control.py` stay mirrored.
- The auth server's sign-in, consent, error and legal pages take the same room without WebGL: their
  CSP is `default-src 'none'` with inline styles only, so `templates/_room.css` (included into each
  page's `<style>`) draws a still chart with CSS gradients, the harness's tokens and a small scope
  mark whose sweep turns unless reduced motion is asked for. Fonts fall back to the device's own.

## Evidence

Headless Edge through puppeteer-core, on the GPU (Intel Iris Xe, D3D11) and on SwiftShader:

- 30 fps while active and 10 fps idle, at about 1 ms of main-thread work a second.
- Calm and reduced motion: no frames after the still one, including on pointer moves.
- Contrast over screenshots, dark and light: no 7×7 block of text under 4.5:1. The faintest hairline
  is about 3.0:1 in dark and 3.5:1 in light, by design (decoration, not text).
- SwiftShader starts at rest. Phone viewports, no CSP violations, and no console errors with WebGL
  removed.
- Web: 132 tests pass, `tsc` clean. API: 126 tests pass, ruff clean.

## Risks

- Hairlines cross text at about 3:1. They are a pixel wide and move; text over them still measures
  4.5:1 at the block level. If a reader finds them busy, the setting and "hide the background" turn
  them off.
- Tested in Chromium (Edge) only. Safari and Firefox are untested; any failure there should fall back
  to the CSS room, but that is unverified.
- Already there before this change, with the chart off as well: axe reports one
  `label-content-name-mismatch`, and navigation logs an `AbortError`.
