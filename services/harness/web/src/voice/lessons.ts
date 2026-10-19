// Corrections the user has taught the assistant: "no, I said Castle Bio, not Kestrel" leaves a lesson
// (heard "castle bio" → meant "Kestrel Biotech"), so the same mishearing is put right before anything
// acts on it next time, by the spoken-command grammar and by the model alike. Lessons live in this
// browser only (store.ts), are listed in Settings where each can be removed, and are told to the model
// as quoted facts, never as instructions.
//
// What can be learned is narrow on purpose. A lesson rewrites the user's words before they are read,
// so it may never touch a number or a yes/no: those are read back instead, every time. Only names and
// short phrases qualify. harness_api/control.py checks the same rules on the server.
//
// Pure (no DOM, no storage), so `npm test` checks it directly.

export interface Lesson {
  heard: string;
  meant: string;
}

export const MAX_LESSONS = 8;
export const MAX_HEARD = 40;
export const MAX_MEANT = 80;

/** Words a lesson may never stand for or produce: answers, cancels, the wake word. */
const RESERVED = new Set([
  "yes",
  "yeah",
  "yep",
  "no",
  "nope",
  "ok",
  "okay",
  "sure",
  "stop",
  "cancel",
  "undo",
  "confirm",
  "never mind",
  "nevermind",
  "go ahead",
  "do it",
  "don't",
  "watchfloor",
  "help",
  "back",
]);

/** Any number, in digits or words: a misheard amount is read back, never silently rewritten. */
const NUMBER =
  /\d|\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|grand|k)\b/i;

const tidy = (v: unknown, max: number): string =>
  typeof v === "string"
    ? v
        .replace(/\p{Cc}/gu, " ")
        .replace(/[“”"]/g, "")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, max)
    : "";

/** Lower case, letters and digits only, single spaces: how two phrasings are compared. */
export const norm = (s: string): string =>
  s
    .toLowerCase()
    .replace(/[^\p{L}\p{N}&']+/gu, " ")
    .trim();

/** A lesson, checked and tidied; null when it is not one this page will learn. */
export function parseLesson(raw: unknown): Lesson | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const heard = tidy(o.heard, MAX_HEARD);
  const meant = tidy(o.meant, MAX_MEANT);
  const h = norm(heard);
  const m = norm(meant);
  if (h.length < 3 || !m || h === m) return null;
  if (!/\p{L}/u.test(h) || h.split(" ").length > 5) return null;
  if (RESERVED.has(h) || RESERVED.has(m)) return null;
  if (NUMBER.test(heard) || NUMBER.test(meant)) return null;
  return { heard, meant };
}

/** The list with this lesson first: one per heard phrase (the newest wins), at most MAX_LESSONS. */
export function addLesson(list: readonly Lesson[], lesson: Lesson): Lesson[] {
  const key = norm(lesson.heard);
  // Learning "kestral → Kestrel" retires an older "Kestrel → something else" that would undo it.
  const rest = list.filter((l) => norm(l.heard) !== key && norm(l.heard) !== norm(lesson.meant));
  return [lesson, ...rest].slice(0, MAX_LESSONS);
}

export function dropLesson(list: readonly Lesson[], heard: string): Lesson[] {
  const key = norm(heard);
  return list.filter((l) => norm(l.heard) !== key);
}

const escape = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** A phrase as a pattern: its words in order, any spacing or punctuation between them. */
function phrase(words: string[]): string {
  return words.map(escape).join("[\\s,.'-]+");
}

/**
 * The user's words with every learned mishearing put right, and the lessons that applied. Whole words
 * only, longest phrase first; when the user also said the rest of the right name ("kestral biotech" with
 * a lesson kestral → Kestrel Biotech), that tail is not doubled.
 */
export function applyLessons(text: string, list: readonly Lesson[]): { text: string; used: Lesson[] } {
  const used: Lesson[] = [];
  let out = text;
  const ordered = [...list].sort((a, b) => norm(b.heard).length - norm(a.heard).length);
  for (const lesson of ordered) {
    const heardWords = norm(lesson.heard).split(" ");
    const meantWords = norm(lesson.meant).split(" ");
    // Optional tails of the right name, longest first: "biotech inc", "inc".
    const tails = meantWords.slice(1).map((_, i) => phrase(meantWords.slice(i + 1)));
    const tail = tails.length ? `(?:[\\s,]+(?:${tails.join("|")}))?` : "";
    const re = new RegExp(`(?<![\\p{L}\\p{N}])${phrase(heardWords)}${tail}(?![\\p{L}\\p{N}])`, "giu");
    let hit = false;
    out = out.replace(re, (match) => {
      if (norm(match) === norm(lesson.meant)) return match; // already said right
      hit = true;
      return lesson.meant;
    });
    if (hit) used.push(lesson);
  }
  return { text: out, used };
}
