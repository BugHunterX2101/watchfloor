// Hands-free listening, driven with a fake clock and a fake recogniser (`npm test`).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  CONVERSATION_MS,
  createHandsFree,
  ECHO_TAIL_MS,
  IDLE_OFF_MS,
  isStopWord,
  overlap,
  QUESTION_MS,
  SETTLE_MS,
  SPEAKING_LIMIT_MS,
  talkOver,
  WAKE_WINDOW_MS,
} from "../src/voice/handsfree.ts";
import { isAnswer } from "../src/voice/grammar.ts";

function rig({ wake = true } = {}) {
  let t = 0;
  let next = 1;
  const timers = new Map();
  const log = {
    utterances: [],
    opts: [],
    states: [],
    heard: [],
    wakes: 0,
    stops: 0,
    talkOvers: 0,
    autoOff: 0,
    fatal: [],
    earsStarted: 0,
    earsStopped: 0,
  };
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
    canQueue: (text) => !isAnswer(text),
    onUtterance: (text, opts) => {
      log.utterances.push(text);
      log.opts.push(opts ?? null);
    },
    onState: (state, hint) => log.states.push([state, hint]),
    onHeard: (text) => log.heard.push(text),
    onWake: () => (log.wakes += 1),
    onStopWord: () => (log.stops += 1),
    onTalkOver: () => (log.talkOvers += 1),
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

  r.advance(CONVERSATION_MS - 1000);
  r.say("why is it at risk");
  r.advance(SETTLE_MS);
  assert.deepEqual(r.log.utterances.at(-1), "why is it at risk");
  r.hf.done();

  r.advance(CONVERSATION_MS);
  assert.equal(r.state(), "waiting");
  r.say("and the next one");
  r.advance(SETTLE_MS * 2);
  assert.equal(r.log.utterances.length, 2); // the window had closed
});

test("its own 'Hands-free is on' opens no conversation: chatter after it is still ignored", () => {
  const r = rig();
  r.hf.start();
  r.hf.speaking("Hands-free is on. Say Watchfloor, then your request.");
  r.advance(3000);
  r.hf.spoke();
  assert.equal(r.state(), "waiting");
  r.say("so anyway I told him the contract was fine and we should get lunch");
  r.advance(SETTLE_MS * 2);
  assert.deepEqual(r.log.utterances, []);

  // but once the user has asked something, a reply does open the conversation
  r.say("watchfloor what needs my attention");
  r.advance(SETTLE_MS);
  r.hf.speaking("Kestrel is at risk.");
  r.hf.spoke();
  assert.equal(r.state(), "awake");

  // and a tap on the microphone counts as the user having asked
  const tapped = rig();
  tapped.hf.start();
  tapped.hf.wake();
  tapped.hf.speaking("Listening.");
  tapped.hf.spoke();
  assert.equal(tapped.state(), "awake");
});

test("a conversation goes on turn after turn without the wake word or a tap", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor what needs my attention");
  r.advance(SETTLE_MS);
  const turns = ["why is the first one at risk", "set up a follow up for it", "yes go ahead", "and what about the biggest deal"];
  for (const [i, line] of turns.entries()) {
    r.hf.speaking(`reply number ${i}`);
    r.advance(5000);
    r.hf.spoke();
    r.advance(30_000); // a pause to think, well past the old eight seconds
    assert.equal(r.state(), "awake");
    r.say(line);
    r.advance(SETTLE_MS);
    assert.equal(r.log.utterances.at(-1), line);
  }
  assert.equal(r.log.utterances.length, 5);
  assert.equal(r.log.wakes, 0);
});

test("a question from the assistant keeps the conversation open longer", () => {
  const plain = rig();
  plain.hf.start();
  plain.say("watchfloor how is kestrel");
  plain.advance(SETTLE_MS);
  plain.hf.speaking("Kestrel is at risk.");
  plain.hf.spoke(false);
  plain.advance(CONVERSATION_MS);
  assert.equal(plain.state(), "waiting");

  const asked = rig();
  asked.hf.start();
  asked.say("watchfloor how is kestrel");
  asked.advance(SETTLE_MS);
  asked.hf.speaking("Kestrel is at risk. Should I set up a follow up?");
  asked.hf.spoke(true);
  asked.advance(CONVERSATION_MS + 1000);
  assert.equal(asked.state(), "awake"); // still waiting for the answer
  asked.say("yes please do");
  asked.advance(SETTLE_MS);
  assert.equal(asked.log.utterances.at(-1), "yes please do");

  const silent = rig(); // a reply that is not read aloud ends the turn through done()
  silent.hf.start();
  silent.say("watchfloor how is kestrel");
  silent.advance(SETTLE_MS);
  silent.hf.done(true);
  silent.advance(QUESTION_MS - 1000);
  assert.equal(silent.state(), "awake");
  silent.advance(2000);
  assert.equal(silent.state(), "waiting");
});

test("'that's all' ends the conversation once it has been acknowledged", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor what needs my attention");
  r.advance(SETTLE_MS);
  r.hf.speaking("Kestrel is at risk.");
  r.hf.spoke();
  r.say("thanks that's all"); // the page recognises it and rests
  r.advance(SETTLE_MS);
  assert.equal(r.hf.rest(), true);
  r.hf.speaking("Okay. Say Watchfloor when you need me.");
  r.advance(3000);
  r.hf.spoke();
  assert.equal(r.state(), "waiting"); // not a new follow-up window

  r.say("so what is the weather like");
  r.advance(SETTLE_MS * 2);
  assert.equal(r.log.utterances.length, 2); // no wake word: not for the page
  r.say("watchfloor open orders");
  r.advance(SETTLE_MS);
  assert.equal(r.log.utterances.at(-1), "watchfloor open orders");

  const open = rig({ wake: false }); // always listening: there is no wake word to go back to
  open.hf.start();
  assert.equal(open.hf.rest(), false);
  assert.equal(rig().hf.rest(), false); // off
});

test("a rest that was never acknowledged does not swallow the next request", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor go to orders");
  r.advance(SETTLE_MS);
  assert.equal(r.hf.rest(), true);
  r.hf.done(); // the acknowledgement was silent
  assert.equal(r.state(), "waiting");
  r.say("watchfloor go to the pipeline");
  r.advance(SETTLE_MS);
  r.hf.done(); // an ordinary turn: resting was spent
  assert.equal(r.state(), "awake");
});

test("talking over the assistant cuts it off and is taken as the next request", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor read the board");
  r.advance(SETTLE_MS);
  r.hf.speaking("Kestrel Biotech is at risk. Health thirty eight. Harbor and Pine is on watch.");
  r.advance(4000);

  r.say("Watchfloor, no show me the second one instead");
  assert.equal(r.log.talkOvers, 1);
  assert.notEqual(r.state(), "speaking");
  r.advance(SETTLE_MS);
  assert.equal(r.log.utterances.at(-1), "Watchfloor, no show me the second one instead");
  assert.equal(r.state(), "busy");

  // the cut-off speech reports its end a moment later: that is not a reply finishing
  r.hf.spoke();
  assert.equal(r.state(), "busy");
  // the next reply is a normal one
  r.hf.speaking("The second deal is Harbor and Pine.");
  r.hf.spoke();
  assert.equal(r.state(), "awake");
});

test("a clear request without the wake word also interrupts, but fragments and echoes do not", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor read the board");
  r.advance(SETTLE_MS);
  r.hf.speaking("Kestrel Biotech is at risk. Health thirty eight. Harbor and Pine is on watch.");

  r.say("open orders"); // two words: could be a piece of anything
  r.say("is at risk health thirty eight"); // the assistant's own voice
  r.say("harbor and pine is on watch"); // ditto
  r.say("kestrel is at risk and health"); // mostly its own words, mis-heard
  assert.equal(r.log.talkOvers, 0);
  assert.equal(r.state(), "speaking");

  r.say("actually draft an email to the owner of lumenridge");
  assert.equal(r.log.talkOvers, 1);
  r.advance(SETTLE_MS);
  assert.equal(r.log.utterances.at(-1), "actually draft an email to the owner of lumenridge");
});

test("the wake word alone, said over the assistant, stops it and listens", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor read the board");
  r.advance(SETTLE_MS);
  r.hf.speaking("Kestrel Biotech is at risk and so is the rest of the list.");
  r.say("Watchfloor");
  assert.equal(r.log.talkOvers, 1);
  assert.equal(r.log.wakes, 1);
  assert.equal(r.state(), "awake");
  r.say("go to orders");
  r.advance(SETTLE_MS);
  assert.equal(r.log.utterances.at(-1), "go to orders");

  // and if the assistant itself said "Watchfloor", hearing it back is not a call
  const echo = rig();
  echo.hf.start();
  echo.say("watchfloor who are you");
  echo.advance(SETTLE_MS);
  echo.hf.speaking("I'm Watchfloor. I watch your pipeline for risk.");
  echo.say("Watchfloor");
  assert.equal(echo.log.talkOvers, 0);
  assert.equal(echo.state(), "speaking");
});

test("talkOver and overlap", () => {
  const said = "Kestrel Biotech is at risk. Health thirty eight.";
  assert.deepEqual(talkOver("Watchfloor stop reading and open orders", said), { text: "stop reading and open orders" });
  assert.deepEqual(talkOver("Watchfloor", said), { text: "" });
  assert.deepEqual(talkOver("compare it with last month please", said), { text: "compare it with last month please" });
  assert.equal(talkOver("open orders", said), null);
  assert.equal(talkOver("biotech is at risk health thirty", said), null);
  assert.equal(talkOver("", said), null);
  assert.equal(overlap("health thirty eight", said), 1);
  assert.equal(overlap("draft an email", said), 0);
  assert.equal(overlap("ok", said), 1); // nothing to judge by: treated as the assistant's own
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
  r.advance(CONVERSATION_MS * 3);
  assert.equal(r.state(), "awake"); // it never goes back to waiting
});

test("a busy page does not act at once, but 'stop' still works", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor what needs my attention");
  r.advance(SETTLE_MS);
  assert.equal(r.state(), "busy");
  r.say("watchfloor and also show orders");
  r.advance(SETTLE_MS * 2);
  assert.equal(r.log.utterances.length, 1, "it is held, not run alongside the first");
  r.say("stop");
  assert.equal(r.log.stops, 1);
});

test("what is said while it works is taken up afterwards, as said before the reply was heard", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor set a rule for health thirty five");
  r.advance(SETTLE_MS);
  assert.equal(r.state(), "busy");
  r.say("make it forty");
  r.advance(SETTLE_MS * 2);
  assert.equal(r.log.utterances.length, 1);
  r.hf.speaking("Should I save a rule for health at or below thirty five?");
  assert.equal(r.log.utterances.length, 1, "not while the reply is being read");
  r.hf.spoke(true);
  assert.deepEqual(r.log.utterances, ["watchfloor set a rule for health thirty five", "make it forty"]);
  assert.deepEqual(r.log.opts, [null, { unheard: true }]);
  assert.equal(r.state(), "busy");
  r.hf.done();
  assert.equal(r.state(), "awake");
});

test("only the latest thing said while it works is kept", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor how is kestrel");
  r.advance(SETTLE_MS);
  r.say("no wait make it forty");
  r.say("actually make it fifty");
  r.hf.done();
  assert.deepEqual(r.log.utterances.slice(1), ["actually make it fifty"]);
});

test("a yes or a no said while it works is never held: it would answer a question not yet heard", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor set a rule for health thirty five");
  r.advance(SETTLE_MS);
  for (const said of ["yes", "yes go ahead", "no", "cancel that", "sure", "a"]) r.say(said);
  r.hf.done();
  assert.equal(r.log.utterances.length, 1);
  assert.equal(r.state(), "awake");
});

test("its own progress remark coming back in through the microphone is not held", () => {
  const r = rig();
  r.hf.start();
  r.say("watchfloor how is kestrel");
  r.advance(SETTLE_MS);
  r.hf.noteSpoken("Still working on that.");
  r.say("still working on that");
  r.hf.done();
  assert.equal(r.log.utterances.length, 1);
});

test("'that's all', hands-free off and talking over drop what was held", () => {
  const rested = rig();
  rested.hf.start();
  rested.say("watchfloor how is kestrel");
  rested.advance(SETTLE_MS);
  rested.say("and also harbor");
  assert.equal(rested.hf.rest(), true);
  rested.hf.done();
  assert.equal(rested.log.utterances.length, 1);
  assert.equal(rested.state(), "waiting");

  const off = rig();
  off.hf.start();
  off.say("watchfloor how is kestrel");
  off.advance(SETTLE_MS);
  off.say("and also harbor");
  off.hf.stop();
  off.hf.start();
  off.hf.done();
  assert.equal(off.log.utterances.length, 1);
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
