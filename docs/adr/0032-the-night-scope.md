# ADR-032: The night scope replaces the 3D orb and floor

- **Status:** Accepted (2026-10-22)
- **Supersedes in part:** ADR-026 (the 3D orb and the watch floor; its pages, personalisation and
  Orders remain)
- **Refines:** ADR-017 (visual design)

## Context

ADR-026 gave the Watch page a point-sphere orb and a perspective floor on a 2D canvas, with the user's
deals standing on it as columns. It looked like a product, but it said little: the floor's height
and lane were decoration over a number the board already stated, the orb competed with the words
for attention, and both cost frame time on the machines a voice product is most often left running on.
Motion was the content, which is the wrong way round for a page that must also read without it.

## Decision

1. **The pipeline is a scope.** `src/plot.ts` draws each open deal as a contact on an SVG scope.
   Range is health (the closer to the centre, the more the deal needs the user), bearing is the
   stage, mark size is the amount. Rings at 35 and 60 are where the At risk and Watch bands begin
   (`BAND_EDGES` in `logic.ts`: health is 100 minus risk, so these are the server's entry thresholds
   of risk 65 and 40; the server's hysteresis exits, 60 and 36, keep a deal from flickering and are
   not drawn).
2. **The centre is the lamp.** The assistant's voice state (idle, listening, thinking, speaking,
   error) drives the centre mark and a sweep that turns only while motion is allowed. The state is
   also written out in `#voice-state`, so it never depends on motion or colour.
3. **Every mark is a word, a shape and a colour together, and a button.** Each contact has a
   spoken-style label and opens the deal sheet; the same facts are in the board and in the summary
   line beside the plot. Colour carries risk and nothing else.
4. **No canvas, no WebGL, no library.** `orb3d.ts` and `scene3d.ts` are removed. The "Calm" setting
   and `prefers-reduced-motion` still stop the sweep and draw the plot still. The stored key
   `orb3d` is kept so a saved choice carries over (it now means "animate the lamp").
5. **Live data drives the words.** Suggestions, standing-order examples and Guide scenarios name
   deals that are on the user's board; "Take a bearing" re-reads the briefing, and a plot older than
   five minutes is taken again when Watch comes back into view. A ship's clock names the watch
   (middle, morning, forenoon, afternoon, first and last dog, first) and strikes its bells. The
   suggestion, example and scenario lines are built from the board, not written out in advance.
6. **Type and palette.** Schibsted Grotesk for the interface, Newsreader for the voice's words,
   Martian Mono for figures; bone on ink, with contrast checked in both themes. All fonts are
   served from the app's own origin (the CSP allows nothing else).

## Consequences

- The page is lighter and every fact survives with colour, motion and sight removed.
- The look is more particular, which is what the logo, lockups, social card and thumbnail were
  redrawn to match (`docs/brand/README.md`).
- ADR-026's floor, skyline and 3D orb no longer exist; its description of them is history.
