// Learning from corrections: what may be learned, how a lesson puts the user's words right, and how a
// change waiting on a yes is dropped however the user says no (`npm test`).
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseAction } from "../src/voice/actions.ts";
import { profileOf } from "../src/voice/context.ts";
import { isAnswer } from "../src/voice/grammar.ts";
import { addLesson, applyLessons, dropLesson, MAX_LESSONS, parseLesson } from "../src/voice/lessons.ts";

const castle = { heard: "castle bio", meant: "Kestrel Biotech" };

test("only names and short phrases are learned", () => {
  assert.deepEqual(parseLesson(castle), castle);
  assert.deepEqual(parseLesson({ heard: "  “harbour”  ", meant: "Harbor & Pine" }), { heard: "harbour", meant: "Harbor & Pine" });
  // never a number, in digits or words: a misheard amount is read back instead
  assert.equal(parseLesson({ heard: "fifteen", meant: "fifty" }), null);
  assert.equal(parseLesson({ heard: "kestrel", meant: "Kestrel 2" }), null);
  assert.equal(parseLesson({ heard: "forty k", meant: "Kestrel" }), null);
  // never an answer, a cancel or the wake word
  assert.equal(parseLesson({ heard: "yeah", meant: "Kestrel" }), null);
  assert.equal(parseLesson({ heard: "castle", meant: "no" }), null);
  assert.equal(parseLesson({ heard: "never mind", meant: "Kestrel" }), null);
  assert.equal(parseLesson({ heard: "watchfloor", meant: "Kestrel" }), null);
  // nothing to learn, too short, too long, or not a lesson at all
  assert.equal(parseLesson({ heard: "Kestrel", meant: "kestrel" }), null);
  assert.equal(parseLesson({ heard: "ke", meant: "Kestrel" }), null);
  assert.equal(parseLesson({ heard: "a b c d e f", meant: "Kestrel" }), null);
  assert.equal(parseLesson({ heard: "castle" }), null);
  assert.equal(parseLesson("castle"), null);
  assert.equal(parseLesson(null), null);
});

test("a lesson rewrites whole words only, and never doubles the right name", () => {
  const list = [castle];
  assert.deepEqual(applyLessons("how is castle bio doing", list), { text: "how is Kestrel Biotech doing", used: [castle] });
  assert.equal(applyLessons("open Castle, Bio please", list).text, "open Kestrel Biotech please");
  assert.equal(applyLessons("castle bio biotech", list).text, "Kestrel Biotech");
  // inside another word: untouched
  assert.deepEqual(applyLessons("newcastle biology", list), { text: "newcastle biology", used: [] });
  // already right: untouched, and not reported as a correction
  const kestral = { heard: "kestral", meant: "Kestrel" };
  assert.deepEqual(applyLessons("open Kestrel", [kestral]), { text: "open Kestrel", used: [] });
  // the longer phrase wins over a shorter one inside it
  const harbour = { heard: "harbour", meant: "Harbor" };
  const harbourPine = { heard: "harbour and pine", meant: "Harbor & Pine" };
  assert.equal(applyLessons("brief me on harbour and pine", [harbour, harbourPine]).text, "brief me on Harbor & Pine");
  // numbers in the sentence are left exactly as heard
  assert.equal(applyLessons("set castle bio to fifty thousand", list).text, "set Kestrel Biotech to fifty thousand");
});

test("the newest lesson for a phrase wins, a contradicting one retires, and the list is capped", () => {
  const first = { heard: "castle bio", meant: "Castle Systems" };
  assert.deepEqual(addLesson([first], castle), [castle]);
  // learning kestral → Kestrel retires an older Kestrel → something that would undo it
  const undo = { heard: "kestrel", meant: "Kestrel Labs" };
  assert.deepEqual(addLesson([undo], { heard: "kestral", meant: "kestrel" }), [{ heard: "kestral", meant: "kestrel" }]);
  let list = [];
  for (let i = 0; i < MAX_LESSONS + 3; i++) list = addLesson(list, { heard: `name ${"abcdefghijk"[i]}x`, meant: `Account ${"abcdefghijk"[i]}` });
  assert.equal(list.length, MAX_LESSONS);
  assert.equal(list[0].meant, "Account k");
  assert.deepEqual(dropLesson([castle, undo], "Castle Bio"), [undo]);
});

test("remember is a page action the model can ask for, checked like any other", () => {
  assert.deepEqual(parseAction({ type: "remember", heard: "castle bio", meant: "Kestrel Biotech" }), { type: "remember", ...castle });
  assert.equal(parseAction({ type: "remember", heard: "fifteen", meant: "fifty" }), null);
  assert.equal(parseAction({ type: "remember", heard: "castle bio" }), null);
});

test("what was learned is told to the assistant, at most eight", () => {
  const now = new Date(2026, 9, 19, 9, 0);
  assert.equal(profileOf({ name: "", style: "normal" }, [], now).lessons, undefined);
  const many = Array.from({ length: 10 }, (_, i) => ({ heard: `name ${i}`, meant: `Account ${i}`, extra: true }));
  const told = profileOf({ name: "", style: "normal" }, [], now, many).lessons;
  assert.equal(told.length, 8);
  assert.deepEqual(told[0], { heard: "name 0", meant: "Account 0" });
});

test("a change waiting on a yes is dropped however no is said", () => {
  for (const said of ["no", "never mind", "no never mind", "never mind, leave it", "cancel that", "forget about it", "skip it", "dont worry about it", "actually no thanks", "cancel the draft", "Watchfloor, never mind"]) {
    assert.ok(isAnswer(said), said);
  }
  for (const said of ["never mind the pipeline, show orders", "no I said Kestrel", "leave it at forty"]) {
    assert.ok(!isAnswer(said), said);
  }
});
