// Runs what voice asked for. An action is a plain object (voice/actions.ts) that came from the
// spoken-command grammar or from the assistant's `control_app` tool; this turns each into a change
// to the page and a short sentence about it. It touches the page only through the Host handed in by
// main.ts, so every page, setting and dialog is reached the same way a tap would reach it.
//
// Nothing here writes the user's data except through the pages' own flows: removing a standing order
// still opens the page's confirmation, signing out and forgetting what the browser holds ask first.

import type { ActivityHandle, Filter } from "../activity";
import type { OrdersHandle } from "../orders";
import type { PipelineHandle } from "../pipeline";
import type { ConfirmOptions } from "../dom";
import type { Settings } from "../store";
import { stopSpeaking } from "../speech";
import { PAGE_LABEL, type AppAction, type DeviceName, type PageName, type StyleName } from "./actions";
import { resolveRule } from "./grammar";

export interface Host {
  page(): PageName;
  /** Go to a page (any dialog open is closed first). */
  go(page: PageName): void;
  dialog(): "none" | "settings" | "deal" | "confirm";
  closeDialog(): void;
  openSettings(): void;
  settings(): Readonly<Settings>;
  update(patch: Partial<Settings>): void;
  /** The voices the browser offers, the viewer's language first. */
  voices(): SpeechSynthesisVoice[];
  pipeline: PipelineHandle;
  orders: OrdersHandle;
  activity: ActivityHandle;
  guide: { describe(): string };
  /** What the Watch page has to say for itself: the last reply, or how to begin. */
  watchSummary(): string;
  lastReply(): string;
  clearConversation(): void;
  signedIn(): boolean;
  signOut(): Promise<void>;
  forgetData(): void;
  clearActivity(): void;
  /** Pins changed: the idle screen's chips and the suggestions follow. */
  pinsChanged(): void;
  /** Hands-free on or off; the sentence says what happened. */
  setHandsFree(on: boolean): string;
  scroll(direction: "up" | "down" | "top" | "bottom"): void;
  /** Show the page's own yes-or-no dialog; `yes` runs if the user agrees, and its sentence is announced. */
  confirm(options: ConfirmOptions, yes: () => Promise<string> | string): void;
  /** Say something that was not a reply to a request (the end of a confirmation, say). */
  announce(text: string): void;
  /** Keep a correction the user taught (voice/lessons.ts); false when it is not one the page learns. */
  learn(heard: string, meant: string): boolean;
}

export interface Outcome {
  /** A sentence about what happened ("Opening Pipeline."), or "" when there is nothing to say. */
  say: string;
  /** The words are what the user asked to hear (a page read out, the last reply again), not an acknowledgement. */
  content: boolean;
}

const said = (text: string): Outcome => ({ say: text, content: false });
const content = (text: string): Outcome => ({ say: text, content: true });

const DEVICE_SAY: Record<DeviceName, string> = {
  fit: "Fitting the view to the window.",
  compact: "Echo Show 5 size.",
  standard: "Echo Show 8 size.",
  large: "Echo Show 15 size.",
};
const FILTER_SAY: Record<Filter, string> = {
  all: "Showing every call.",
  writes: "Showing only the calls that change something.",
  errors: "Showing only the calls that failed.",
};
const STYLE_SAY: Record<StyleName, string> = {
  brief: "Short answers from now on.",
  normal: "Back to my usual length.",
  detailed: "I'll give you the reasons and the detail.",
};
const ORDINAL_WORDS =["first", "second", "third", "fourth", "fifth"];

/** Ask the question on the page's own dialog and out loud. */
function ask(h: Host, options: ConfirmOptions, yes: () => Promise<string> | string): Outcome {
  // One question at a time: a second "sign out" while the first is open would stack a second dialog.
  if (h.dialog() === "confirm") return said("There's a question on the screen already. Say yes or no.");
  h.confirm(options, yes);
  return said(`${options.title} ${options.body} Say yes or no.`);
}

export async function run(a: AppAction, h: Host): Promise<Outcome> {
  switch (a.type) {
    case "navigate": {
      if (h.page() === a.page && h.dialog() === "none") return said(`You're already on ${PAGE_LABEL[a.page]}.`);
      h.go(a.page);
      return said(a.page === "watch" ? "Back to Watch." : `Opening ${PAGE_LABEL[a.page]}.`);
    }
    case "open_settings":
      h.openSettings();
      return said("Opening settings.");
    case "close_dialog":
      if (h.dialog() === "none") return said("There's nothing open.");
      h.closeDialog();
      return said("Closed.");

    case "set_theme":
      h.update({ theme: a.theme });
      return said(a.theme === "auto" ? "Following your system theme." : `${a.theme === "dark" ? "Dark" : "Light"} mode.`);
    case "set_device":
      h.update({ device: a.device });
      if (h.page() !== "watch") h.go("watch"); // the preview lives on Watch
      return said(DEVICE_SAY[a.device]);
    case "set_read_aloud":
      h.update({ readAloud: a.on });
      return said(a.on ? "I'll read replies aloud." : "Okay, I'll stay quiet. Replies still appear on screen.");
    case "set_speech_rate": {
      const now = h.settings().rate;
      const next = a.rate ?? Math.round((now + (a.step === "faster" ? 0.15 : -0.15)) * 100) / 100;
      const bounded = Math.min(1.4, Math.max(0.7, next));
      if (bounded === now) return said(a.step === "faster" ? "That's already as fast as I go." : "That's already as slow as I go.");
      h.update({ rate: bounded });
      return said(a.step === "faster" ? "A bit faster." : a.step === "slower" ? "A bit slower." : `Speaking speed ${bounded.toFixed(2)}.`);
    }
    case "set_motion":
      h.update({ motion: a.motion });
      return said(a.motion === "calm" ? "Calm mode. The animation holds still." : "Motion follows your system setting.");
    case "set_orb":
      h.update({ orb3d: a.on });
      return said(a.on ? "The lamp is animated again." : "The lamp holds still now. It still changes colour.");
    case "set_floor":
      h.update({ scene: a.on });
      return said(a.on ? "The sweep is turning again." : "The sweep is off. The scope holds still.");
    case "set_name":
      h.update({ name: a.name });
      return said(`Nice to meet you, ${h.settings().name || a.name}.`);
    case "set_style":
      h.update({ style: a.style });
      return said(STYLE_SAY[a.style]);
    case "set_alerts":
      h.update({ alerts: a.on });
      return said(
        a.on
          ? "I'll speak up when something changes on your board."
          : "Okay, I won't interrupt. Ask me when you want an update.",
      );
    case "change_voice": {
      // The default voice (Amazon Polly where the server offers it) is one stop on the way round.
      const list = [{ voiceURI: "", name: "the default voice" }, ...h.voices()];
      if (list.length < 2) return said("This browser doesn't offer another voice.");
      const at = Math.max(0, list.findIndex((v) => v.voiceURI === h.settings().voiceURI));
      const next = list[(at + (a.step === "next" ? 1 : -1) + list.length) % list.length];
      h.update({ voiceURI: next.voiceURI });
      return said(`Using ${next.name}.`);
    }
    case "wake_word":
      h.update({ wakeWord: a.on });
      return said(a.on ? "I'll listen for Watchfloor." : "Okay, I'll treat everything I hear as a request.");

    case "pipeline_view": {
      if (h.page() !== "pipeline") h.go("pipeline");
      const { type: _type, ...view } = a;
      return said(await h.pipeline.show(view));
    }
    case "open_deal": {
      if (h.page() !== "pipeline") h.go("pipeline");
      return said(await h.pipeline.open(a));
    }
    case "pin_deal": {
      const text = await h.pipeline.pin(a, a.on);
      h.pinsChanged();
      return said(text);
    }
    case "activity_view":
      if (h.page() !== "activity") h.go("activity");
      h.activity.setFilter(a.filter);
      return said(FILTER_SAY[a.filter]);
    case "clear_activity":
      return ask(h, { title: "Clear the activity log?", body: "This session's list of tool calls is emptied.", confirm: "Clear", danger: true }, () => {
        h.clearActivity();
        return "Cleared.";
      });

    case "refresh":
      if (h.page() === "pipeline") {
        h.pipeline.reload();
        return said("Refreshing the board.");
      }
      if (h.page() === "orders") {
        h.orders.reload();
        return said("Refreshing your standing orders.");
      }
      if (h.page() === "activity") {
        h.activity.refresh();
        return said("Refreshed.");
      }
      return said("There's nothing to refresh here.");
    case "read_page": {
      const page = h.page();
      const body =
        page === "watch"
          ? h.watchSummary()
          : page === "pipeline"
            ? (await h.pipeline.ensure(), h.pipeline.describe())
            : page === "orders"
              ? (await h.orders.ensure(), h.orders.describe())
              : page === "activity"
                ? h.activity.describe()
                : h.guide.describe();
      return content(`You're on ${PAGE_LABEL[page]}. ${body}`);
    }
    case "repeat": {
      const last = h.lastReply();
      return last ? content(last) : said("I haven't said anything yet.");
    }
    case "stop_speaking":
      stopSpeaking();
      return said("");
    case "hands_free":
      return said(h.setHandsFree(a.on));
    case "scroll":
      h.scroll(a.direction);
      return said("");
    case "clear_conversation":
      h.clearConversation();
      return said("Cleared the conversation.");
    case "remember":
      return said(
        h.learn(a.heard, a.meant)
          ? `Noted: when I hear “${a.heard}”, I'll take it as ${a.meant}.`
          : "I can't keep that one: I only learn names and phrases, never numbers or yes and no.",
      );

    case "sign_out":
      if (!h.signedIn()) return said("You're not signed in.");
      return ask(h, { title: "Sign out of Watchfloor?", body: "You'll need to sign in again to use it.", confirm: "Sign out", danger: true }, async () => {
        await h.signOut();
        return "Signed out.";
      });
    case "forget_data":
      return ask(
        h,
        {
          title: "Forget everything this browser remembers about you?",
          body: "Your name, voice and motion choices, recent questions, pinned deals and the corrections I learned are removed. Your account and data are not touched.",
          confirm: "Forget",
          danger: true,
        },
        () => {
          h.forgetData();
          return "Forgotten. This browser has forgotten you.";
        },
      );
  }
}

/** "Remove the first rule": find the order, then put the page's own confirmation to the user. */
export async function removeByVoice(which: string, h: Host): Promise<string> {
  if (!h.signedIn()) return "Sign in first, and then I can remove an order.";
  if (h.page() !== "orders") h.go("orders");
  if (!(await h.orders.ensure())) return "I couldn't load your standing orders.";
  const rules = h.orders.rules();
  if (!rules.length) return "You don't have any standing orders.";
  const match = resolveRule(
    which,
    rules.map((r) => ({ id: r.rule_id, description: r.description })),
  );
  if (match.rule) {
    const rule = rules.find((r) => r.rule_id === match.rule?.id);
    if (rule) {
      void h.orders.removeRule(rule).then((result) => {
        h.announce(result === "removed" ? "Removed." : result === "cancelled" ? "Okay, I've kept it." : "I couldn't remove it.");
      });
      return `Remove this standing order? ${rule.description} Say yes or no.`;
    }
  }
  if (match.ambiguous.length) {
    const list = match.ambiguous
      .slice(0, 3)
      .map((p) => `The ${ORDINAL_WORDS[rules.findIndex((r) => r.rule_id === p.id)] ?? "next"}: ${p.description}`)
      .join(" ");
    return `Which one? ${list}`;
  }
  return `I couldn't match that to one of your ${rules.length} ${rules.length === 1 ? "order" : "orders"}. Say remove the first, second or last rule, or describe it.`;
}
