# ADR-017: Harness visual design ("night watch") and Alexa+ alignment of the views

- **Status:** Accepted (2026-10-10)
- **Refines:** DESIGN.md §15.2 (view surfaces, radius, touch size, button emphasis) and §17 (harness page)

## Context

The harness page is what a judge or a teammate sees first: the voice loop, the MCP App views and the
tool trace. It was a plain chat form with an emoji microphone. The views already followed DD §15,
but Amazon has since published the Alexa+ MCP Add-on Design Guide, and the views render inside that
host.

Research inputs:

- [Alexa+ MCP Add-on Design Guide](https://developer.amazon.com/docs/alexaplus/add-ons/mcp-addon-design-guide.html):
  [Visual Foundations](https://developer.amazon.com/docs/alexaplus/add-ons/mcp-addon-visual-foundations.html)
  (768×480 base canvas scaled by the host; card surfaces dark `#14181E`, nested `#1B2028`,
  light `#FFFFFF` / `#FAF9FB`; radii 4/8/12/16; bright colour only on final actions; no bold
  colour fields or gradients as backgrounds),
  [Layout and Rendering](https://developer.amazon.com/docs/alexaplus/add-ons/mcp-addon-layout-and-rendering.html),
  [Accessibility](https://developer.amazon.com/docs/alexaplus/add-ons/mcp-addon-accessibility.html)
  (48×48 px touch targets, 4.5:1 text contrast, no flashing),
  [Brand Expression](https://developer.amazon.com/docs/alexaplus/add-ons/mcp-addon-brand-expression.html).
- Voice-assistant state design: idle, listening, thinking and speaking must each be distinct and
  written out, motion must not imply listening when nothing is heard, and reduced motion swaps
  movement for intensity (practitioner guides surveyed 2026; no formal standard exists).
- Agent transparency: show each tool call as a labelled item with its inputs and outcome, rather
  than a spinner ([Smashing Magazine, 2026](https://smashingmagazine.com/2026/05/practical-interface-patterns-ai-transparency/)).
- Component patterns from 21st.dev (voice orbs, tool-call cards, the "Agent Trace" waterfall,
  animated-border composers), used as references only: the harness is dependency-free TypeScript,
  so nothing was installed from it.
- Skills: ui-ux-pro-max (style match "AI-native UI"; its purple/pink palette was rejected as
  generic) and frontend-design (commit to one distinctive direction; no default font stacks).

## Decision

**Harness page: "night watch".** A watch floor at night: deep ink, one signal colour, instruments.

- Colour: ink `#080B11` with Alexa's card surfaces; a single teal-to-blue signal (`#4FE0C8`,
  `#3AA8E8`, `#7C8CFF`) for the voice presence and focus; severity colours (`--crit`, `--warn`,
  `--ok`, the same values as the views) only where risk is shown. Light theme mirrors every token.
  All text pairs measured at ≥ 4.5:1 in both themes.
- Type, self-hosted from `@fontsource-variable` (OFL, bundled, no network): Fraunces for the
  assistant's voice and headings (a spoken reply reads as a voice, not a log line), Geist for the
  interface, Geist Mono for the trace and engine labels.
- Layout: the **stage** is an Echo-Show-like screen where views render, with a device switcher
  (Fit, Compact 960, Standard 1280, Large 1920 per DD §15.1) that lays the view out at that width
  and scales it to fit. The **rail** holds the voice presence, the transcript, suggestion chips
  (phrasings the offline router also understands) and the composer. The **trace** lists every tool
  call with its latency bar and expandable arguments (confirmation tokens masked).
- Motion with meaning only: an orb and the screen's top "watch light" carry the voice state
  (slow drift idle, ripples while recognition is open, fast sweep while working, a pulse on each
  real spoken word boundary). Every state is also written out in text. Entrances are 150–420 ms
  ease-out; `prefers-reduced-motion` removes movement and keeps intensity changes.
- Background: static atmosphere (two soft light pools, a fading instrument grid, film grain from
  an inline SVG). Nothing animates behind the content.
- The DD §17 label "Simulated Alexa+ experience" stays as a sticky, undismissable banner.

**Views (Board, Detail, Confirm), aligned to Alexa+ Visual Foundations.**

- Surfaces: dark canvas `#14181E` with rows `#1B2028`; light canvas `#FAF9FB` with white rows.
- Radius 12 px and touch target 48 px at the 768 px base canvas, scaling with width as before.
- Emphasis: only **Approve** (the final action) uses the bright accent (`button.primary`). Other
  actions use a muted fill (`#2D415E` dark, Alexa's secondary blue); dismissals and Back are
  outlined. Motion stays within DD §15.2 (150 ms highlight only).

## Consequences

- The harness bundle grows by the fonts' Latin subsets (~90 KB woff2, cached; other subsets load
  only when a page uses those characters). JS is 64 KB gzipped, CSS 6 KB.
- The views' DD §15.2 table is superseded for `--bg`, `--surface`, radius, touch size and button
  emphasis; band colours, type scale and the 150 ms motion limit are unchanged. Each view is still
  one self-contained file under the 150 KB gzip gate.
- The device preview approximates Alexa's scaling with a CSS transform; the real host's grid
  snapping (Layout and Rendering) is not reproduced, so the simulator remains the reference.
