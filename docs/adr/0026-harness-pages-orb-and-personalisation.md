# ADR-026: Harness pages, a 3D orb and local personalisation

- **Status:** Accepted (2026-10-17). The 3D orb and the watch floor were replaced by the night scope ([ADR-032](0032-the-night-scope.md)); the pages and personalisation stand.
- **Refines:** ADR-017 (visual design), ADR-018 (interaction details); DESIGN.md §17 (harness page)

## Context

The harness was one screen: a conversation, a view and a trace. That is the right centre for a voice
product, but it left four gaps for anyone using it for more than a minute:

1. Nowhere to go. The risk board only existed as a spoken answer; the standing orders only as a
   sentence; the tool calls only as the last few trace cards.
2. No sense of who it was talking to. Every visit started from the same blank.
3. The idle screen was a flat gradient orb, which undersold a voice product.
4. A screen-reader or keyboard user had one landmark and no way to jump.

## Decision

**Five pages in one document, addressed by the URL hash** (`#/`, `#/pipeline`, `#/orders`,
`#/activity`, `#/guide`): Watch (unchanged at its core), Pipeline, Orders, Activity, Guide. Pages are
shown and hidden, never rebuilt, so the conversation, the open view and the trace survive a trip to
another page. Back and Forward work, every page can be linked to, an unknown address lands on Watch,
and the page's heading takes focus on arrival. Signing in from a page returns to that page. On a
phone the tabs become a bottom bar (five items, icon and label).

**Every page reads the real tools through the existing proxy.** Pipeline calls `get_risk_briefing`
(with the server's own `limit` and `segment` arguments) and `explain_deal_risk`; Orders calls
`list_standing_orders` and `remove_standing_order`; Activity shows what the harness already
reports about every call (tool, server latency, ok). Two names join the `/api/tool` allowlist;
removing an order sits behind a confirmation dialog that has Cancel focused first. Creating or
changing an order, a preference or a deal stays in the conversation, behind a preview and a yes
(ADR-003). Calls made from a page land in the Watch trace and in Activity, labelled with their
source, so nothing the page does is invisible.

**Numbers are computed once, in a pure module** (`logic.ts`) with unit tests (`npm test`): health is
100 minus the RIGS score (ADR-020; the explain trend arrives as scores and is converted), percentiles
are nearest-rank over observed calls, the health ring's arc is health percent of the circumference.
API results pass through a parser that checks the shape and fills in health, so a malformed answer
becomes an error state, not a broken page.

**A 3D orb with no library.** A Fibonacci sphere of points, projected with a real perspective camera
on a 2D canvas, leaning toward the pointer. It follows the voice state (slow turn when idle, a ripple
while listening, orbiting rings while thinking, a pulse on every spoken word from the speech word
boundaries, a tremor in the error colours). It stops when hidden or off screen, thins itself if
frames run slow (about 8 ms per frame measured in headless Edge), and draws a single still frame
under the system's reduced-motion setting or the "Calm" preference. The state is still written out
in text; the orb is never the only signal. Deal cards lean toward a fine pointer with a light that
follows it, off for touch, reduced motion and Calm.

**A watch floor behind the page.** A perspective floor of grid lines runs out to a glowing horizon
while the camera drifts forward and data motes float past (a 2D canvas, a real projection, no
library; `src/scene3d.ts`). After a briefing the user's own deals stand on the floor as glowing
columns: coloured by band, taller the lower the deal's health, each in a lane chosen by a hash of its
id so a deal is always in the same place. The scene follows the assistant (faster while it thinks,
ripples across the floor while it listens, one on every spoken word), the pointer moves the camera a
little, scrolling lowers the horizon, the horizon warms as more of the briefing is at risk, and takes
a faint tint from the hour. It is decoration only: every fact is also in words, and the Guide says
so. It stops when hidden, thins itself if frames run slow (about 8 ms per frame measured with the
orb running), holds one still frame under reduced motion or "Calm", and can be switched off. Only
the id, band and health of the last briefing's deals are kept in the browser for it (no names or
amounts), and they are cleared on sign-out and by "Forget everything", so a shared browser never
shows the last person's pipeline.

**A logo made from the product.** The mark is a health ring, open at one end, with a sentinel dot in
the gap, around the assistant's orb: the same drawing as the Pipeline page's ring. The ring turns
while the assistant listens or thinks. Vector sources live in `services/harness/web/public/` and
pixel-exact PNGs, a share card and a Devpost thumbnail in `docs/brand/`, rendered locally with the
real fonts. No logo-generator service was used: they return template art, and the mark had to be
the product's own ring.

**Personalisation that stays on the device.** A name for the greeting (time-of-day aware), voice and
speed, motion, the 3D orb switch, up to five pinned deals, and the last questions, all in
`localStorage` behind guarded reads; "Forget everything" clears it. Pinned deals and recent
questions lead the suggestion chips, and pinned deals appear on the Watch screen. Nothing is sent to
a server and nothing is required.

**Accessibility as a gate.** axe-core (WCAG 2.0/2.1 A and AA plus best practice) reports zero
violations on all five pages in both themes and in the deal and settings dialogs. Bands stay a word,
a colour and a shape. `g` then `w`, `p`, `o`, `a` or `g` jump between pages, `?` opens the guide and
`,` opens settings.

## Consequences

- The built page is about 85 KB gzipped JavaScript and 11 KB CSS, with no CDN, font service or
  network dependency, so it still works offline-first.
- Loading the Pipeline page is a briefing as far as the server is concerned (it marks deals as seen,
  exactly as asking by voice does). The page says so.
- The `/api/tool` allowlist is the only server change. It has a test that the two new names pass and
  that the writes which must be conversational still return 403.
- Local personalisation means a different browser starts blank; that is intended.
