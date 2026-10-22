# Watchfloor brand assets

The mark is **a night scope**: a bone rim, the Watch and At-risk range rings (the inner one dashed),
the ice-blue sweep, one contact, and the assistant's lamp at the centre. It is the same drawing as the
Watch page's plot, where range is health and bearing is stage (ADR-0032), so the logo and the product
are one object. The name is set in Newsreader over the descriptor **PIPELINE RISK, ON WATCH** in
Martian Mono, semi-condensed.

| File | Use |
| --- | --- |
| `watchfloor-mark-256.png`, `-512.png`, `-1024.png` | The mark alone, transparent outside the disc. App icons, avatars, favicons |
| `watchfloor-lockup-light.png` | Mark + wordmark + descriptor, transparent, for light backgrounds (2400 × 512) |
| `watchfloor-lockup-dark.png` | The same for dark backgrounds |
| `watchfloor-social-card-1200x630.png` (and `2400x1260`) | Link preview / social card: the live scope beside the line |
| `watchfloor-devpost-thumbnail-1500x1000.png` | 3:2 project image for Devpost |

Editable vector sources are in `services/harness/web/public/`: `logo-mark.svg`, `logo-lockup.svg`,
`logo-lockup-dark.svg`. The SVG lockups name Newsreader and Martian Mono with serif and monospace
fallbacks; the PNGs here were rendered with the real fonts the app ships, so use the PNGs where exact
lettering matters. The social card and thumbnail are captured from the running app after a real
bearing on the demo data (made-up companies), with the sweep held still.

## Colour

| Token | Dark | Light | Used for |
| --- | --- | --- | --- |
| Ink | `#0B0D10` | `#0B0D10` | The mark's disc (the mark is always the night scope) |
| Surface | `#14181E` | `#14181E` | Inside the rim |
| Bone | `#ECE6D8` | `#ECE6D8` | The rim, rings and contact |
| Signal (lamp) | `#9CC9FF` | `#9CC9FF` | The sweep and the lamp (in the app's light theme the signal is `#1F4BB8`) |
| Wordmark | `#ECE6D8` | `#15181C` | "Watchfloor" |
| Muted | `#A39C8C` | `#575247` | The descriptor |

Risk colours (red, amber, green) are never used in the logo: they mean something in the product, so
they appear only where risk is shown.

## Use

- Clear space: at least the lamp's diameter on every side of the mark.
- Smallest size: the mark alone at 16 px; the lockup at 120 px wide (the descriptor is dropped below
  that, as the app's top bar does under 1040 px).
- Don't recolour the rim or the sweep, don't add contacts in risk colours, and don't put the lockup on
  a busy background without a scrim.
- In the app the lamp breathes while listening and the sweep turns; keep both still in print.
