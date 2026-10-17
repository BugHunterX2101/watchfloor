// Hands-free listening, driven with a fake clock and a fake recogniser (`npm test`).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createHandsFree,
  ECHO_TAIL_MS,
  FOLLOW_UP_MS,
  IDLE_OFF_MS,
  isStopWord,
  SETTLE_MS,
  SPEAKING_LIMIT_MS,
  WAKE_WINDOW_MS,
} from "../src/voice/handsfree.ts";

function rig({ wake = true } = {}) {
  let t = 0;
  let next = 1;
  const timers = new Map();
  const log = { utterances: [], states: [], heard: [], wakes: 0, stops: 0, autoOff: 0, fatal: [], earsStarted: 0, earsStopped: 0 };
  const settings = { wake, answering: false };
  let handlers = null;
  const hf = createHandsFree({
    ears: {
      start(h) {
        handlers = h;
        log.earsStarted += 1;
      },
      stop() {
        log.earsStopped += 1;
      },
    },
    clock: {
      now: () => t,
      setTimer(fn, ms) {
        const id = next++;
        timers.set(id, { at: t + ms, fn });
        return id;
      },
      clearTimer: (id) => timers.delete(id),
    },
    wakeRequired: () => settings.wake,
    answering: () => settings.answering,
    onUtterance: (text) => log.utterances.push(text),
    onState: (state, hint) => log.states.push([state, hint]),
    onHeard: (text) => log.heard.push(text),
    onWake: () => (log.wakes += 1),
    onStopWord: () => (log.stops += 1),
    onAutoOff: () => (log.autoOff += 1),
    onFatal: (reason) => log.fatal.push(reason),
  });
  const advance = (ms) => {
    const end = t + ms;
    for (;;) {
      const due = [...timers.entries()].filter(([, v]) => v.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      t = due[1].at;
      timers.delete(due[0]);
      due[1].fn();
    }
    t = end;
  };
  return {
    hf,
    log,
    settings,
    advance,
    say: (text) => handlers.onFinal(text),
    hear: (text) => handlers.onInterim(text),
    fail: (reason) => handlers.onFatal(reason),
    state: () => hf.state(),
  };
}

test("nothing is acted on until the wake word is said", () => {
  const r = rig();
  r.hf.start();
  assert.equal(r.state(), "waiting");
  r.say("so I told him the contract is fine");
  r.advance(SETTLE_MS * 2);
  assert.deepEqual(r.log.utterances, []);
  assert.equal(r.state(), "waiting");

  r.say("Watchfloor open the pipeline");
  assert.equal(r.state(), "waiting"); // still settling
  r.advance(SETTLE_MS);
  assert.deepEqual(r.log.utterances, ["Watchfloor open the pipeline"]);
  assert.equal(r.state(), "busy");
});

test("what is not meant for the page is not even shown", () => {
  const r = rig();
  r.hf.start();
  r.hear("let's get lunch");
  assert.deepEqual(r.log.heard, []);
  r.hear("watchfloor what needs");
  assert.deepEqual(r.log.heard, ["watchfloor what needs"]);
});

test("the wake word alone opens a short window for the request", () => {
  const r = rig();
  r.hf.start();
  r.say("Hey Watchfloor");
  r.advance(SETTLE_MS);
  assert.equal(r.log.wakes, 1);
  assert.equal(r.state(), "awake");
  assert.deepEqual(r.log.utterances, []);

  r.advance(2000);
  r.say("go to orders"); // no wake word needed inside the window
  r.advance(SETTLE_MS);
  assert.deepEqual(r.log.utterances, ["go to orders"]);

  const quiet = rig();
  quiet.hf.start();
  quiet.say("watchfloor");
  quiet.advance(SETTLE_MS);
  quiet.advance(WAKE_WINDOW_MS);
  assert.equal(quiet.state(), "waiting"); // the window closed
  quiet.say("go to orders");
  quiet.advance(SETTLE_MS * 2);
  assert.deepEqual(quiet.log.utterances, []);
});

test("words said in one breath, in two results, are one request", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor show me");
  r.advance(400);
  r.say("the pipeline");
  r.advance(SETTLE_MS - 1);
  assert.deepEqual(r.log.utterances, []);
  r.advance(1);
  assert.deepEqual(r.log.utterances, ["watchfloor show me the pipeline"]);
});

test("after a reply a follow-up needs no wake word, for a while", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor what needs my attention");
  r.advance(SETTLE_MS);
  r.hf.speaking("Kestrel Biotech is at risk. Health thirty eight.");
  assert.equal(r.state(), "speaking");
  r.advance(6000);
  r.hf.spoke();
  assert.equal(r.state(), "awake");

  r.advance(FOLLOW_UP_MS - 1000);
  r.say("why is it at risk");
  r.advance(SETTLE_MS);
  assert.deepEqual(r.log.utterances.at(-1), "why is it at risk");
  r.hf.done();

  r.advance(FOLLOW_UP_MS);
  assert.equal(r.state(), "waiting");
  r.say("and the next one");
  r.advance(SETTLE_MS * 2);
  assert.equal(r.log.utterances.length, 2); // the window had closed
});

test("while the assistant speaks only 'stop' is heard, and never its own voice", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor read the board");
  r.advance(SETTLE_MS);
  r.hf.speaking("Please stop by Harbor and Pine this week. Kestrel is at risk.");
  r.say("open orders"); // not a stop word: ignored
  r.advance(SETTLE_MS * 2);
  assert.equal(r.log.stops, 0);
  assert.equal(r.log.utterances.length, 1);

  r.say("stop by harbor and pine"); // the assistant's own words
  r.say("stop"); // a lone "stop" inside what it is saying is the echo of its own voice
  assert.equal(r.log.stops, 0);

  r.hf.speaking("Kestrel is at risk.");
  r.say("Watchfloor, stop");
  r.say("stop");
  assert.equal(r.log.stops, 2);
});

test("the tail of the assistant's own voice is not taken for a request", () => {
  const r = rig({ wake: false });
  r.hf.start();
  r.say("show me the pipeline");
  r.advance(SETTLE_MS);
  r.hf.speaking("Kestrel Biotech is at risk. Health thirty eight.");
  r.advance(4000);
  r.hf.spoke();
  r.advance(800);
  r.say("health thirty eight"); // arrives late from the recogniser
  r.advance(SETTLE_MS * 2);
  assert.equal(r.log.utterances.length, 1);

  r.advance(ECHO_TAIL_MS);
  r.say("open orders");
  r.advance(SETTLE_MS);
  assert.deepEqual(r.log.utterances.at(-1), "open orders");
});

test("a quick yes to the page's question is an answer, not the echo of 'say yes or no'", () => {
  const r = rig({ wake: false });
  r.hf.start();
  r.say("remove the first rule");
  r.advance(SETTLE_MS);
  r.settings.answering = true; // the page's confirmation is on screen
  r.hf.speaking("Remove this standing order? Flag big deals. Say yes or no.");
  r.advance(2000);
  r.hf.spoke();
  r.advance(500); // well inside the echo tail
  r.say("yes");
  r.advance(SETTLE_MS);
  assert.deepEqual(r.log.utterances.at(-1), "yes");

  // Without a question on the screen the same word is still treated as the assistant's own voice.
  const q = rig({ wake: false });
  q.hf.start();
  q.hf.speaking("You can say yes or no to most things.");
  q.hf.spoke();
  q.advance(500);
  q.say("yes");
  q.advance(SETTLE_MS * 2);
  assert.equal(q.log.utterances.length, 0);
});

test("without the wake word every request is taken", () => {
  const r = rig({ wake: false });
  r.hf.start();
  assert.equal(r.state(), "awake");
  r.say("go to orders");
  r.advance(SETTLE_MS);
  assert.deepEqual(r.log.utterances, ["go to orders"]);
  r.hf.done();
  r.advance(FOLLOW_UP_MS * 3);
  assert.equal(r.state(), "awake"); // it never goes back to waiting
});

test("a busy page ignores speech except 'stop'", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor what needs my attention");
  r.advance(SETTLE_MS);
  assert.equal(r.state(), "busy");
  r.say("watchfloor and also show orders");
  r.advance(SETTLE_MS * 2);
  assert.equal(r.log.utterances.length, 1);
  r.say("stop");
  assert.equal(r.log.stops, 1);
});

test("hands-free switches itself off after five quiet minutes", () => {
  const r = rig();
  r.hf.start();
  r.advance(IDLE_OFF_MS - 1000);
  assert.equal(r.state(), "waiting");
  r.advance(31_000);
  assert.equal(r.state(), "off");
  assert.equal(r.log.autoOff, 1);
  assert.equal(r.log.earsStopped, 1);

  const busy = rig();
  busy.hf.start();
  busy.say("watchfloor take your time");
  busy.advance(SETTLE_MS);
  busy.advance(IDLE_OFF_MS * 2); // a long turn is not idleness
  assert.equal(busy.state(), "busy");
  assert.equal(busy.log.autoOff, 0);
});

test("a request keeps it awake", () => {
  const r = rig();
  r.hf.start();
  for (let i = 0; i < 4; i++) {
    r.advance(IDLE_OFF_MS - 60_000);
    r.say("watchfloor refresh");
    r.advance(SETTLE_MS);
    r.hf.done();
  }
  assert.notEqual(r.state(), "off");
  assert.equal(r.log.autoOff, 0);
});

test("a blocked microphone ends the session and says why", () => {
  const r = rig();
  r.hf.start();
  r.fail("not-allowed");
  assert.equal(r.state(), "off");
  assert.deepEqual(r.log.fatal, ["not-allowed"]);
});

test("a speech that never reports its end does not trap the page", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor read it");
  r.advance(SETTLE_MS);
  r.hf.speaking("A long reply.");
  r.advance(SPEAKING_LIMIT_MS);
  assert.equal(r.state(), "awake");
});

test("pause and resume stop and restart the microphone (a hidden tab)", () => {
  const r = rig();
  r.hf.start();
  r.hf.pause();
  r.hf.pause();
  assert.equal(r.log.earsStopped, 1);
  r.hf.resume();
  assert.equal(r.log.earsStarted, 2);
  r.hf.resume();
  assert.equal(r.log.earsStarted, 2);
  assert.equal(r.state(), "waiting");
});

test("turning the wake word off while waiting makes it listen at once", () => {
  const r = rig();
  r.hf.start();
  r.settings.wake = false;
  r.hf.refresh();
  assert.equal(r.state(), "awake");
  r.say("show orders");
  r.advance(SETTLE_MS);
  assert.deepEqual(r.log.utterances, ["show orders"]);
});

test("tapping the microphone is the same as saying the wake word", () => {
  const r = rig();
  r.hf.start();
  r.hf.wake();
  assert.equal(r.state(), "awake");
  assert.equal(r.log.wakes, 1);
  r.say("go to orders");
  r.advance(SETTLE_MS);
  assert.deepEqual(r.log.utterances, ["go to orders"]);
  r.hf.wake(); // busy: a tap does nothing
  assert.equal(r.state(), "busy");
  assert.equal(r.log.wakes, 1);
});

test("stop and start are idempotent", () => {
  const r = rig();
  r.hf.stop();
  r.hf.start();
  r.hf.start();
  assert.equal(r.log.earsStarted, 1);
  r.hf.stop();
  r.hf.stop();
  assert.equal(r.log.earsStopped, 1);
  assert.equal(r.state(), "off");
  r.hf.speaking("ignored");
  r.hf.spoke();
  r.hf.done();
  assert.equal(r.state(), "off");
});

test("isStopWord", () => {
  assert.equal(isStopWord("stop"), true);
  assert.equal(isStopWord("Watchfloor, stop!"), true);
  assert.equal(isStopWord("Never mind"), true);
  assert.equal(isStopWord("stop by harbor"), false);
  assert.equal(isStopWord("don't stop"), false);
});
