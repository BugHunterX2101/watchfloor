// A band is a word, a colour and a shape together (triangle, square, circle), so it reads under
// Color Correction, Color Inversion and in greyscale, never by colour alone (DD §15.2, Alexa+ a11y).

import { BAND_LABEL } from "./format";
import type { Band } from "./types";

const SHAPE: Record<Band, preact.JSX.Element> = {
  at_risk: <path d="M6 1.2 11 10.5H1z" />,
  watch: <rect x="1.8" y="1.8" width="8.4" height="8.4" rx="1" />,
  healthy: <circle cx="6" cy="6" r="4.6" />,
};

export function BandChip({ band }: { band: Band }) {
  return (
    <span class={`band ${band}`}>
      <svg class="band-mark" viewBox="0 0 12 12" aria-hidden="true">
        {SHAPE[band]}
      </svg>
      {BAND_LABEL[band]}
    </span>
  );
}
