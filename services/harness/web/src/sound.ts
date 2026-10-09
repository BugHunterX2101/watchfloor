// Earcons, synthesized with Web Audio (no files, no network). Pitch carries the meaning, the way
// voice assistants signal turn-taking: a rising pair when the microphone opens, a falling pair when
// it closes, and a soft major triad when an approved action has run. Quiet (peak gain 0.05) and
// under 250 ms, so they never compete with speech or leak back into recognition.

let ctx: AudioContext | null = null;
let enabled = true;

export function setSounds(on: boolean): void {
  enabled = on;
}

function tone(c: AudioContext, freq: number, at: number, dur = 0.11, peak = 0.05): void {
  const osc = c.createOscillator();
  const gain = c.createGain();
  osc.type = "sine";
  osc.frequency.setValueAtTime(freq, at);
  // Exponential ramps from near zero: no click at the start or the end.
  gain.gain.setValueAtTime(0.0001, at);
  gain.gain.exponentialRampToValueAtTime(peak, at + 0.012);
  gain.gain.exponentialRampToValueAtTime(0.0001, at + dur);
  osc.connect(gain).connect(c.destination);
  osc.start(at);
  osc.stop(at + dur + 0.02);
}

function play(notes: [freq: number, offset: number][]): void {
  if (!enabled) return;
  try {
    // Created on the first user gesture (a tap on the mic or Approve), as autoplay rules require.
    ctx ??= new AudioContext();
    if (ctx.state === "suspended") void ctx.resume();
    const t = ctx.currentTime + 0.01;
    for (const [freq, offset] of notes) tone(ctx, freq, t + offset);
  } catch {
    // No audio device or Web Audio: the visual state still says the same thing.
  }
}

export const earcon = {
  listen: (): void => play([[659.25, 0], [880, 0.075]]), // E5 → A5
  stop: (): void => play([[880, 0], [659.25, 0.075]]), // A5 → E5
  done: (): void => play([[523.25, 0], [659.25, 0.06], [783.99, 0.12]]), // C5 E5 G5
};
