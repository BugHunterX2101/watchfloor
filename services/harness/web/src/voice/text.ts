// Text helpers for talking and listening: normalising what speech recognition heard, comparing it
// with names it may have misheard, and turning a written reply into something a voice reads well.
// Pure (no DOM, no storage), so `npm test` runs it directly.

/** Lower case, "&" as "and", apostrophes gone, everything else that isn't a letter or digit a space. */
export function norm(s: string): string {
  return s
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[’'`]/g, "")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim()
    .replace(/\s+/g, " ");
}

/** Edit distance between two strings (insert, delete, substitute), two rows of memory. */
export function levenshtein(a: string, b: string): number {
  if (a === b) return 0;
  if (!a.length) return b.length;
  if (!b.length) return a.length;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    for (let j = 1; j <= b.length; j++) {
      row[j] = Math.min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = row;
  }
  return prev[b.length];
}

/** 1 for identical, 0 for nothing in common: edit distance over the longer length. */
export function similarity(a: string, b: string): number {
  const longest = Math.max(a.length, b.length);
  return longest === 0 ? 1 : 1 - levenshtein(a, b) / longest;
}

/** Sounds that speech recognition swaps for each other, folded together so "kestral" meets "kestrel". */
export function phonetic(word: string): string {
  return word
    .replace(/ph/g, "f")
    .replace(/ck|kk/g, "k")
    .replace(/c(?=[aou])/g, "k")
    .replace(/c/g, "s")
    .replace(/q/g, "k")
    .replace(/[aeiouy]+/g, "a")
    .replace(/(.)\1+/g, "$1");
}

const WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];

/** "three" or "3" as a number, 0 to 10 (digits to 99); null for anything else. */
export function toCount(word: string): number | null {
  const w = word.trim().toLowerCase();
  if (/^\d{1,2}$/.test(w)) return Number(w);
  const i = WORDS.indexOf(w);
  return i >= 0 ? i : null;
}

const ORDINALS: Record<string, number> = {
  first: 1,
  "1st": 1,
  one: 1,
  second: 2,
  "2nd": 2,
  two: 2,
  third: 3,
  "3rd": 3,
  three: 3,
  fourth: 4,
  "4th": 4,
  four: 4,
  fifth: 5,
  "5th": 5,
  five: 5,
  last: -1,
  latest: -1,
  final: -1,
};

/** "first" to "fifth", "last", "1st" to "5th" as a one-based position (-1 for the last). */
export function ordinal(word: string): number | null {
  const w = word.trim().toLowerCase();
  return w in ORDINALS ? ORDINALS[w] : null;
}

const SCALE: Record<string, string> = { k: "thousand", m: "million", b: "billion" };

/** A reply written for the screen, made easy to hear: markup, ids and links gone, money and
 *  percentages said the way a person says them. */
export function speakable(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/https?:\/\/\S+/g, "a link")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*(?:[-*•]|\d+[.)])\s+/gm, "")
    .replace(/(\*\*|__|\*|`)/g, "")
    .replace(/\b(?:ws|us|ac|ct|dl|act|hist|pr|tk|job|rule|al|aud)_[A-Za-z0-9]{4,}\b/g, "") // ids.py's prefixes
    .replace(/\$(\d+(?:\.\d+)?)\s?([kKmMbB])\b/g, (_m, n: string, s: string) => `${n} ${SCALE[s.toLowerCase()]} dollars`)
    .replace(/\$(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)/g, "$1 dollars")
    .replace(/(\d)\s*%/g, "$1 percent")
    .replace(/(\d+)\s*\/\s*100\b/g, "$1 out of 100")
    .replace(/\s*[→⟶]\s*/g, " to ")
    .replace(/\s+[·•|]\s+/g, ". ")
    .replace(/\s*[—–]\s*/g, ", ")
    .replace(/&/g, " and ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Whether a reply ends by asking the user something, so a hands-free conversation waits longer for the answer. */
export function asksSomething(text: string): boolean {
  const tail = text.trim().replace(/["'”’)\]\s]+$/, "");
  return tail.endsWith("?") || /\bsay yes or no\.?$/i.test(tail);
}

/** Whether `heard` is just the assistant's own voice coming back in through the microphone. */
export function isEcho(heard: string, spoken: string): boolean {
  const h = norm(heard);
  const s = norm(spoken);
  if (!h) return true;
  if (s.includes(h)) return true;
  const hw = h.split(" ");
  const sw = s.split(" ");
  if (hw.length < 3) return false;
  // A near match of the same few words, somewhere in what was being said (recognition drops and swaps words).
  for (const len of [hw.length - 1, hw.length, hw.length + 1]) {
    for (let i = 0; i + len <= sw.length; i++) {
      if (similarity(h, sw.slice(i, i + len).join(" ")) >= 0.8) return true;
    }
  }
  return false;
}
