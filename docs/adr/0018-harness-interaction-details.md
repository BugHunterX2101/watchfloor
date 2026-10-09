# ADR-018: Interaction details for the harness and the views

- **Status:** Accepted (2026-10-10)
- **Refines:** ADR-017 (harness visual design), DESIGN.md §15 (views) and §17 (harness page)

## Context

ADR-017 set the look. What separates a convincing voice product from a styled chat box is the
behaviour at each moment of a turn: when the microphone opens, while the assistant works, while it
speaks, and when an action completes. Each detail below answers one of those moments, and none is
decorative. Inputs: the Alexa+ MCP Add-on guide (Visual Foundations, Layout and Rendering,
Components and Patterns), turn-taking earcon guidance (Google conversation design), agent
transparency patterns (step ledger with status glyphs), and the MCP Apps host-context spec
(ext-apps 2.0.3).

## Decision

**Harness page**

- **Live captions.** While a reply is read aloud, its words ink in as the voice reaches them, from
  the real `SpeechSynthesis` word boundaries. Voices that report no boundaries leave the line as is.
- **What the microphone hears** is shown large on the stage while listening, as Echo devices show
  recognised words.
- **Earcons** synthesized with Web Audio (no files): rising pair when the mic opens, falling pair
  when it closes, a soft triad when an approved action ran. Peak gain 0.05, under 250 ms, off with
  the same switch as spoken replies.
- **Live trace.** A running card appears the moment a turn starts, with an elapsed-time counter, and
  is replaced by the real spans. Status is a shape (check, cross, spinning arc), not only a colour;
  spans stagger in 40 ms apart.
- **"Working on it…"** carries a light sweep; the tab title mirrors the voice state.
- **Theme switch** (System, Light, Dark), remembered per browser and sent to the view as host
  context, so both themes the Alexa+ guide requires can be shown side by side.
- **Device fidelity.** The view receives the host context an Echo Show would give it: touch without
  hover at Compact/Standard/Large, the container width, locale and time zone, re-sent on change.
- **Keyboard:** `/` focuses the message box (hint hidden on touch screens); Escape stops speech.
- **Offline** is stated plainly and holds the Send button.
- **Type:** Fraunces' optical-size axis (display forms large, sturdier text forms in the
  transcript), balanced headings, pretty-wrapped paragraphs, tabular figures for timings, and a
  settling spring (`linear()`, ≈2 % overshoot) for entrances where supported.

**Views (within DD §15.2's 150 ms motion limit)**

- Factor names are spelled out (Responsiveness, Intent, Governance, Schedule): a touch screen has
  no hover, so a letter with a tooltip hid the meaning.
- The 14-day trend marks today with a dot and states its start and end values.
- Confirm shows the time left as a bar that empties in one-second steps (not animated), amber in the
  last minute, with a one-time polite announcement.
- After Approve, a **Done** screen states what ran, with a check mark that reads without colour.

## Consequences

- Harness bundle: JS 65 KB gzip (unchanged), CSS 6.6 KB; the optical-size Fraunces cut adds about
  31 KB to the Latin font file. Each view stays at 71 KB gzip, under the 150 KB gate.
- Captions depend on the browser's voice reporting word boundaries; without them the reply is still
  spoken and shown in full.
