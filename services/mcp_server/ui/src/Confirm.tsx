// Confirm sheet (DD §15.5): the preview, Approve (confirm_action) or Not now (nothing is called).

import { useEffect, useState } from "preact/hooks";
import { channelOf, countdown } from "./format";
import type { Proposal } from "./types";

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
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const left = countdown(data.expires_at, now);
  const h = headline(data.preview);
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
        <p class="meta" role="timer" aria-live="off">
          {left.expired ? "This preview has expired. Ask again to make a new one." : `Expires in ${left.text}`}
        </p>
      </div>
    </section>
  );
}
