// The settings dialog: what to call you, how replies sound, and how much the page moves. Every change
// applies at once and is remembered in this browser (store.ts); nothing is sent anywhere.

import { h } from "./dom";
import { speak, stopSpeaking, voices } from "./speech";
import { forgetEverything, lessons, settings, unlearn, update, type Motion, type ReplyStyle } from "./store";

const MOTIONS: [Motion, string][] = [
  ["auto", "Follow my system"],
  ["calm", "Calm"],
];

const STYLES: [ReplyStyle, string][] = [
  ["brief", "Brief: one or two short sentences"],
  ["normal", "Normal"],
  ["detailed", "Detailed: with the reasons"],
];

export interface SettingsHost {
  /** Hands-free listening: whether this browser can do it, whether it is on, and a way to switch it. */
  handsFree: { available: boolean; active(): boolean; set(on: boolean): void };
}

export function mountSettings(host: SettingsHost): { open(): void } {
  let dialog: HTMLDialogElement | null = null;

  function build(): HTMLDialogElement {
    const s = settings();

    const name = h("input", {
      id: "set-name",
      class: "text",
      type: "text",
      maxlength: 40,
      autocomplete: "off",
      spellcheck: "false",
      value: s.name,
    });
    name.addEventListener("input", () => update({ name: name.value }));

    const aloud = h("input", { id: "set-aloud", type: "checkbox", checked: s.readAloud });
    aloud.addEventListener("change", () => update({ readAloud: aloud.checked }));

    const handsFree = h("input", {
      id: "set-hf",
      type: "checkbox",
      checked: host.handsFree.active(),
      disabled: !host.handsFree.available,
    });
    handsFree.addEventListener("change", () => host.handsFree.set(handsFree.checked));

    const wake = h("input", { id: "set-wake", type: "checkbox", checked: s.wakeWord });
    wake.addEventListener("change", () => update({ wakeWord: wake.checked }));

    const alerts = h("input", { id: "set-alerts", type: "checkbox", checked: s.alerts });
    alerts.addEventListener("change", () => update({ alerts: alerts.checked }));

    const lengthSelect = h("select", { id: "set-style", class: "select" });
    for (const [value, text] of STYLES) {
      lengthSelect.append(h("option", { value, selected: value === s.style }, text));
    }
    lengthSelect.addEventListener("change", () => update({ style: lengthSelect.value as ReplyStyle }));

    const voiceSelect = h("select", { id: "set-voice", class: "select" });
    const fillVoices = (): void => {
      const all = voices();
      const here = (navigator.language || "en").slice(0, 2).toLowerCase();
      const mine = all.filter((v) => v.lang.toLowerCase().startsWith(here));
      const rest = all.filter((v) => !mine.includes(v));
      voiceSelect.replaceChildren(h("option", { value: "" }, "Default (Amazon Polly when available)"));
      for (const v of [...mine, ...rest]) {
        voiceSelect.append(h("option", { value: v.voiceURI, selected: v.voiceURI === settings().voiceURI }, `${v.name} (${v.lang})`));
      }
      voiceSelect.disabled = all.length === 0;
    };
    fillVoices();
    voiceSelect.addEventListener("change", () => update({ voiceURI: voiceSelect.value }));

    const rateOut = h("output", { for: "set-rate", class: "range-out" }, `${s.rate.toFixed(2)}×`);
    const rate = h("input", { id: "set-rate", class: "range", type: "range", min: 0.7, max: 1.4, step: 0.05, value: s.rate });
    rate.addEventListener("input", () => {
      update({ rate: Number(rate.value) });
      rateOut.textContent = `${settings().rate.toFixed(2)}×`;
    });
    const preview = h("button", { type: "button", class: "btn btn-quiet" }, "Preview");
    preview.addEventListener("click", () => {
      stopSpeaking();
      speak("Kestrel Biotech is at risk. Health thirty-eight.");
    });

    const motionBox = h("div", { class: "seg", role: "radiogroup", "aria-labelledby": "set-motion-label" });
    const paintMotion = (): void => {
      for (const b of motionBox.querySelectorAll<HTMLButtonElement>("button")) {
        const on = b.dataset.motion === settings().motion;
        b.setAttribute("aria-checked", String(on));
        b.tabIndex = on ? 0 : -1;
      }
    };
    const chooseMotion = (id: Motion): void => {
      update({ motion: id });
      paintMotion();
      motionBox.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
    };
    for (const [id, text] of MOTIONS) {
      const b = h("button", { type: "button", role: "radio", "data-motion": id }, text);
      b.addEventListener("click", () => chooseMotion(id));
      b.addEventListener("keydown", (e) => {
        if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
        e.preventDefault();
        chooseMotion(id === "auto" ? "calm" : "auto");
      });
      motionBox.append(b);
    }
    paintMotion();

    const orb = h("input", { id: "set-orb", type: "checkbox", checked: s.orb3d });
    orb.addEventListener("change", () => update({ orb3d: orb.checked }));

    const floor = h("input", { id: "set-scene", type: "checkbox", checked: s.scene });
    floor.addEventListener("change", () => update({ scene: floor.checked }));

    // Corrections the user taught by voice (voice/lessons.ts): shown so they can see and undo what was learned.
    const learned = h("ul", { class: "learned", "aria-labelledby": "set-learned-label" });
    const paintLearned = (): void => {
      const list = lessons();
      learned.replaceChildren(
        ...(list.length
          ? list.map((l) => {
              const remove = h("button", { type: "button", class: "btn btn-quiet", "aria-label": `Forget “${l.heard}”` }, "Forget");
              remove.addEventListener("click", () => {
                unlearn(l.heard);
                paintLearned();
              });
              return h("li", { class: "learned-item" }, h("span", {}, `“${l.heard}” means ${l.meant}`), remove);
            })
          : [h("li", { class: "help" }, "Nothing yet. Correct a name I misheard (“no, I said Kestrel”) and I'll remember it.")]),
      );
    };
    paintLearned();

    const forget = h("button", { type: "button", class: "btn btn-quiet danger-text" }, "Forget everything in this browser");
    const close = h("button", { type: "button", class: "icon-btn close", "aria-label": "Close" }, "✕");

    const toggle = (id: string, label: string, help: string, input: HTMLInputElement): HTMLElement =>
      h(
        "div",
        { class: "set-row" },
        h("label", { class: "switch", for: id }, input, h("span", { "aria-hidden": "true" }), label),
        h("p", { class: "help" }, help),
      );

    const d = h(
      "dialog",
      { class: "sheet", "aria-labelledby": "settings-title" },
      h("div", { class: "sheet-bar" }, h("h2", { class: "sheet-title", id: "settings-title" }, "Settings"), close),
      h(
        "div",
        { class: "sheet-main settings" },
        h(
          "div",
          { class: "set-row" },
          h("label", { for: "set-name", class: "set-label" }, "What should I call you?"),
          name,
          h("p", { class: "help" }, "Used for the greeting. Stays in this browser."),
        ),
        toggle("set-aloud", "Read replies aloud, with sound cues", "Turns off speech and the listening chimes together.", aloud),
        toggle(
          "set-hf",
          "Hands-free listening",
          host.handsFree.available
            ? "Keeps the microphone open so you can run everything by voice. Your browser's speech service hears the microphone while it is on."
            : "Needs Chrome or Edge, which provide speech recognition.",
          handsFree,
        ),
        toggle(
          "set-wake",
          "Only act when I say “Watchfloor”",
          "Hands-free then acts on requests that start with the wake word, or that follow a reply. Off: everything it hears is treated as a request.",
          wake,
        ),
        toggle(
          "set-alerts",
          "Speak up when the board changes",
          "While hands-free is on, Watchfloor looks at your pipeline about once a minute and tells you when a deal slips to at risk, drops sharply, recovers, or a standing order triggers.",
          alerts,
        ),
        h(
          "div",
          { class: "set-row" },
          h("label", { for: "set-style", class: "set-label" }, "How long should answers be?"),
          lengthSelect,
          h("p", { class: "help" }, "You can also just say “keep it short” or “more detail”."),
        ),
        h("div", { class: "set-row" }, h("label", { for: "set-voice", class: "set-label" }, "Voice"), voiceSelect),
        h(
          "div",
          { class: "set-row" },
          h("label", { for: "set-rate", class: "set-label" }, "Speaking speed"),
          h("div", { class: "range-row" }, rate, rateOut, preview),
        ),
        h(
          "div",
          { class: "set-row" },
          h("span", { class: "set-label", id: "set-motion-label" }, "Motion"),
          motionBox,
          h("p", { class: "help" }, "Calm holds the sweep and the lamp still. “Follow my system” respects your reduced-motion setting."),
        ),
        toggle(
          "set-orb",
          "Animate the voice lamp",
          "The lamp at the centre of the scope breathes while ready and pulses on each spoken word. Off: it changes colour only.",
          orb,
        ),
        toggle(
          "set-scene",
          "Turn the sweep on the scope",
          "A line turns once every six seconds and each deal answers as it passes. Off: the scope holds still; every deal stays where it is.",
          floor,
        ),
        h(
          "div",
          { class: "set-row" },
          h("span", { class: "set-label", id: "set-learned-label" }, "What I've learned from your corrections"),
          learned,
          h("p", { class: "help" }, "Applied before I act on what I hear. Names and phrases only: never numbers, yes or no."),
        ),
        h("div", { class: "sheet-actions left" }, forget),
      ),
    );
    forget.addEventListener("click", () => {
      forgetEverything();
      d.close();
    });
    close.addEventListener("click", () => d.close());
    d.addEventListener("click", (e) => {
      if (e.target === d) d.close();
    });
    // The voice list arrives late in some browsers; refill it while the dialog is open.
    const refill = (): void => fillVoices();
    if ("speechSynthesis" in window) window.speechSynthesis.addEventListener("voiceschanged", refill);
    d.addEventListener("close", () => {
      stopSpeaking();
      if ("speechSynthesis" in window) window.speechSynthesis.removeEventListener("voiceschanged", refill);
    });
    document.body.append(d);
    return d;
  }

  return {
    open() {
      if (dialog?.open) return; // already showing: rebuilding it would orphan its listeners
      // Rebuilt each time so the fields always show what is stored now (after "forget", for one).
      dialog?.remove();
      dialog = build();
      dialog.showModal();
      dialog.querySelector<HTMLElement>("#set-name")?.focus();
    },
  };
}
