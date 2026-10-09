// Confirm sheet (DD §15.5): the preview, Approve (confirm_action) or Not now (nothing is called).

import { useEffect, useState } from "preact/hooks";
import { channelOf, countdown } from "./format";
import type { Confirmed, Proposal } from "./types";

function headline(p: Record<string, unknown>): { title: string; quote?: string; lines: string[] } {
  const s = (k: string) => (typeof p[k] === "string" ? (p[k] as string) : "");
  const account = s("account") || "this deal";
  const channel = channelOf(p.side_effects);
  const posts = channel ? `Posts to ${channel}` : "";
  switch (p.action_type) {
    case "create_task":
      return {
        title: `Create a task for ${s("assignee")}`,
        quote: s("title"),
        lines: [[s("due_display") && `Due ${s("due_display")}`, posts].filter(Boolean).join(" · ")],
      };
    case "reassign_owner":
      return { title: `Reassign ${account}`, lines: [`From ${s("from_owner")} to ${s("to_owner")}`, posts] };
    case "notify_channel":
      return { title: `Post to ${s("channel")}`, quote: s("message"), lines: [account] };
    case "draft_followup":
      return { title: `Draft a follow-up for ${account}`, quote: s("intent"), lines: ["Sent to you as a draft, never to the customer"] };
    default: {
      const changes = Array.isArray(p.changes) ? (p.changes as Record<string, string>[]) : [];
      return {
        title: `Update ${account}`,
        lines: changes.map((c) => `${c.field.replace("_", " ")}: ${c.from ?? "none"} → ${c.to_display ?? c.to}`),
      };
    }
  }
}

export function Confirm(props: { data: Proposal; busy: boolean; approve: () => void; dismiss: () => void }) {
  const { data, busy } = props;
  const [opened] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const left = countdown(data.expires_at, now);
  const h = headline(data.preview);
  // The time left as a bar that empties (stepped once a second, no animation), warning in the last
  // minute; the one-minute mark is announced once, politely.
  const expires = new Date(data.expires_at).getTime();
  const share = Math.min(1, Math.max(0, (expires - now) / Math.max(1, expires - opened)));
  const low = !left.expired && expires - now <= 60_000;
  return (
    <section class="screen" aria-label="Confirm action">
      <div class="card sheet">
        <p class="title">{h.title}</p>
        {h.quote && <p class="quote">“{h.quote}”</p>}
        {h.lines.filter(Boolean).map((l) => (
          <p key={l} class="meta">
            {l}
          </p>
        ))}
        <div class="actions split">
          <button type="button" class="secondary" disabled={busy} onClick={props.dismiss}>
            Not now
          </button>
          <button type="button" class="primary" disabled={busy || left.expired} onClick={props.approve}>
            Approve
          </button>
        </div>
        <div class={`expiry${low ? " low" : ""}`} aria-hidden="true">
          <span style={{ transform: `scaleX(${share})` }} />
        </div>
        <p class={`meta timer${low ? " low" : ""}`} role="timer" aria-live="off">
          {left.expired ? "This preview has expired. Ask again to make a new one." : `Expires in ${left.text}`}
        </p>
        <p class="sr-only" role="status">
          {low ? "About a minute left to approve." : ""}
        </p>
      </div>
    </section>
  );
}

/** After Approve: what ran, stated plainly, with a mark that reads as done without colour. */
export function Done({ data }: { data: Confirmed }) {
  return (
    <section class="screen" aria-label="Action complete">
      <div class="card sheet done" role="status">
        <svg class="done-mark" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="11" />
          <path d="M7 12.5l3.2 3.2L17 9" />
        </svg>
        <p class="title">{data.status === "already_executed" ? "Already done" : "Done"}</p>
        <p>{data.summary}</p>
      </div>
    </section>
  );
}
