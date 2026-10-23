// A little weight for the deal cards (ADR-035): with a fine pointer over one, the card leans a few
// degrees toward it, as a plate on a chart table would under a hand, and the lamp's light rides
// the spot. It only sets two custom properties (--tilt-x/--tilt-y degrees, --lamp-x/--lamp-y
// percent) and a class; the styles decide what they do. Nothing here runs for touch, under
// reduced motion or Calm, and it never touches the rail, the device screen or the scope.

const MAX_DEG = 4.5;

export function mountTilt(): void {
  const fine = matchMedia("(hover: hover) and (pointer: fine)");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const root = document.documentElement;
  const allowed = (): boolean => fine.matches && !reduced.matches && root.dataset.motion !== "calm";

  let card: HTMLElement | null = null;
  let at: { x: number; y: number } | null = null;
  let raf = 0;

  function release(): void {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    at = null;
    if (!card) return;
    card.classList.remove("leaning");
    for (const name of ["--tilt-x", "--tilt-y", "--lamp-x", "--lamp-y"]) card.style.removeProperty(name);
    card = null;
  }

  function lean(): void {
    raf = 0;
    if (!card || !at) return;
    const r = card.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (at.x - r.left) / Math.max(1, r.width)));
    const y = Math.min(1, Math.max(0, (at.y - r.top) / Math.max(1, r.height)));
    // The part under the pointer presses in: pointer at the top edge, the top edge recedes.
    card.style.setProperty("--tilt-x", `${((0.5 - y) * 2 * MAX_DEG).toFixed(2)}deg`);
    card.style.setProperty("--tilt-y", `${((x - 0.5) * 2 * MAX_DEG).toFixed(2)}deg`);
    card.style.setProperty("--lamp-x", `${(x * 100).toFixed(1)}%`);
    card.style.setProperty("--lamp-y", `${(y * 100).toFixed(1)}%`);
  }

  document.addEventListener(
    "pointermove",
    (e) => {
      if (e.pointerType === "touch" || !allowed()) {
        if (card) release();
        return;
      }
      const next = e.target instanceof Element ? e.target.closest<HTMLElement>(".deal-card") : null;
      if (next !== card) {
        release();
        card = next;
        card?.classList.add("leaning");
      }
      if (!card) return;
      at = { x: e.clientX, y: e.clientY };
      if (!raf) raf = requestAnimationFrame(lean);
    },
    { passive: true },
  );
  // The pointer leaves the window, a key moves focus on, or the board is drawn again under it.
  document.addEventListener("pointerleave", release, { passive: true });
  window.addEventListener("blur", release);
  window.addEventListener("hashchange", release);
  for (const query of [fine, reduced]) query.addEventListener("change", release);
}
