# Watchfloor brand assets

The mark is a **health ring, open at one end, with a sentinel dot in the gap**, around the assistant's
orb. It is the same ring as the health ring on the Pipeline page, so the logo and the product are one
object. The name reads as two words: **Watch** (full weight) and **floor** (muted), over the descriptor
**PIPELINE RISK WATCH**.

| File | Use |
| --- | --- |
| `watchfloor-mark-256.png`, `-512.png`, `-1024.png` | The mark alone, transparent. App icons, avatars, favicons |
| `watchfloor-lockup-light.png` | Mark + wordmark + descriptor, transparent, for light backgrounds (2400 × 512) |
| `watchfloor-lockup-dark.png` | The same for dark backgrounds |
| `watchfloor-social-card-1200x630.png` (and `2400x1260`) | Link preview / social card over the 3D floor |
| `watchfloor-devpost-thumbnail-1500x1000.png` | 3:2 project image for Devpost |

Editable vector sources are in `services/harness/web/public/`: `logo-mark.svg`, `logo-lockup.svg`,
`logo-lockup-dark.svg`. The SVG lockups name Geist with a system fallback; the PNGs here were rendered
with the real Geist, Geist Mono and Fraunces fonts, so use the PNGs where exact lettering matters.

## Colour

| Token | Dark | Light | Used for |
| --- | --- | --- | --- |
| Signal | `#4FE0C8` | `#0B7F73` | The ring's start, the sentinel dot, links |
| Signal 2 | `#3AA8E8` | `#1D6FB8` | The ring's middle |
| Signal 3 | `#7C8CFF` | `#4B55D6` | The ring's end |
| Ink | `#E8ECF3` | `#111723` | "Watch" |
| Muted | `#98A2B4` | `#556074` | "floor" |

Risk colours (red, amber, green) are never used in the logo: they mean something in the product, so
they appear only where risk is shown.

## Use

- Clear space: at least the height of the sentinel dot on every side of the mark.
- Smallest size: the mark alone at 16 px; the lockup at 120 px wide (the descriptor is dropped below
  that).
- Don't recolour the ring, don't close the gap, don't put the lockup on a busy background without a
  scrim (the social card shows one).
- The mark may turn (listening and thinking in the app); keep it still in print.
