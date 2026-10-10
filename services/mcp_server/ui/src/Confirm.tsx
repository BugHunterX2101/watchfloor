// Confirm sheet (DD §15.5): the preview, the named action (confirm_action) or Not now (nothing is called).
// The sheet reads top to bottom as a contract: a verb-first title, what changes (before → after),
// what else happens (side effects), then a button that names the action rather than "Approve".
// The server already binds the token to this exact payload and lets an expired token run nothing.

import { useEffect, useRef, useState } from "preact/hooks";
import { channelOf, clock, countdown, effectText, revertedText } from "./format";
import type { Confirmed, Proposal, Undone } from "./types";

interface Sheet {
  title: string;
  verb: string;
  /** Changes someone else's work or posts where others read it: the sheet says so. */
  high: boolean;
  quote?: string;
  diff: { label: string; from: string; to: string }[];
  lines: string[];
}

function sheet(p: Record<string, unknown>): Sheet {
  const s = (k: string) => (typeof p[k] === "string" ? (p[k] as string) : "");
  const account = s("account") || "this deal";
  switch (p.action_type) {
    case "create_task":
      return {
        title: `Create a task for ${s("assignee")}`,
        verb: "Create task",
        high: false,
        quote: s("title"),
        diff: [],
        lines: [s("due_display") && `Due ${s("due_display")}`, account],
      };
    case "reassign_owner":
      return {
        title: `Reassign ${account}`,
        verb: "Reassign owner",
        high: true,
        diff: [{ label: "Owner", from: s("from_owner"), to: s("to_owner") }],
        lines: [],
      };
    case "notify_channel": {
      const channel = s("channel") || channelOf(p.side_effects) || "the team channel";
      return { title: `Post to ${channel}`, verb: `Post to ${channel}`, high: true, quote: s("message"), diff: [], lines: [account] };
    }
    case "draft_followup":
      return { title: `Draft a follow-up for ${account}`, verb: "Draft follow-up", high: false, quote: s("intent"), diff: [], lines: [] };
    default: {
      const changes = Array.isArray(p.changes) ? (p.changes as Record<string, string>[]) : [];
      return {
        title: `Update ${account}`,
        verb: "Update deal",
        high: true,
        diff: changes.map((c) => ({
          label: c.field.replace("_", " "),
          from: c.from_display ?? c.from ?? "none",
          to: c.to_display ?? c.to,
        })),
        lines: [],
      };
    }
  }
}

export function Confirm(props: {
  data: Proposal;
  busy: boolean;
  approve: () => void;
  dismiss: () => void;
  /** extend_preview (ADR-021): offered in the last minute, so a decision is never rushed. */
  more: () => void;
}) {
  const { data, busy } = props;
  const [opened, setOpened] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  const [added, setAdded] = useState<number | null>(null); // minutes granted by the last More time
  const shownExpiry = useRef(data.expires_at);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  // More time arrived: the bar refills from here, and the sheet says so once.
  useEffect(() => {
    if (data.expires_at === shownExpiry.current) return;
    shownExpiry.current = data.expires_at;
    setOpened(Date.now());
    setNow(Date.now());
    setAdded(Math.max(1, Math.round((new Date(data.expires_at).getTime() - Date.now()) / 60_000)));
  }, [data.expires_at]);
  const left = countdown(data.expires_at, now);
  const h = sheet(data.preview);
  const effects = Array.isArray(data.preview.side_effects) ? (data.preview.side_effects as string[]) : [];
  // The time left as a bar that empties (stepped once a second, no animation), warning in the last
  // minute; the one-minute mark is announced once, politely.
  const expires = new Date(data.expires_at).getTime();
  const share = Math.min(1, Math.max(0, (expires - now) / Math.max(1, expires - opened)));
  const low = !left.expired && expires - now <= 60_000;
  return (
    <section class="screen" aria-label="Confirm action">
      <div class={`card sheet${h.high ? " high" : ""}${left.expired ? " expired" : ""}`}>
        <p class="eyebrow">{h.high ? "NEEDS YOUR OK · AFFECTS YOUR TEAM" : "NEEDS YOUR OK"}</p>
        <p class="title">{h.title}</p>
        {h.quote && <p class="quote">“{h.quote}”</p>}
        {h.diff.length > 0 && (
          <dl class="diff">
            {h.diff.map((d) => (
              <div key={d.label} class="diff-row">
                <dt>{d.label}</dt>
                <dd>
                  <s class="from">{d.from}</s>
                  <span class="arrow" aria-label="changes to">
                    {" → "}
                  </span>
                  <strong class="to">{d.to}</strong>
                </dd>
              </div>
            ))}
          </dl>
        )}
        {h.lines.filter(Boolean).map((l) => (
          <p key={l} class="meta">
            {l}
          </p>
        ))}
        {effects.length > 0 && (
          <>
            <p class="eyebrow effects-head">WHAT HAPPENS</p>
            <ul class="effects">
              {effects.map((e) => (
                <li key={e}>{effectText(e, false)}</li>
              ))}
            </ul>
          </>
        )}
        <div class="actions split">
          <button type="button" class="secondary" disabled={busy} onClick={props.dismiss}>
            Not now
          </button>
          <button type="button" class="primary" disabled={busy || left.expired} onClick={props.approve}>
            {h.verb}
          </button>
        </div>
        <div class={`expiry${low ? " low" : ""}`} aria-hidden="true">
          <span style={{ transform: `scaleX(${share})` }} />
        </div>
        <div class="timer-row">
          <p class={`meta timer${low ? " low" : ""}`} role="timer" aria-live="off">
            {left.expired
              ? "Expired. Nothing was changed. Ask again for a fresh preview."
              : `Expires in ${left.text}${added !== null ? " · more time added" : ""}`}
          </p>
          {low && (
            <button type="button" class="secondary more" disabled={busy} onClick={props.more}>
              More time
            </button>
          )}
        </div>
        <p class="sr-only" role="status">
          {low
            ? "About a minute left to approve. More time is available."
            : left.expired
              ? "This preview expired. Nothing was changed."
              : added !== null
                ? `More time added: about ${added} minutes to decide.`
                : ""}
        </p>
      </div>
    </section>
  );
}

/** After the action ran: a receipt, not just a tick. What happened, each side effect, and the
 *  audit reference, with a mark that reads as done without colour. Stays until the user moves on.
 *  For its first 30 seconds it offers Undo (ADR-021): the Slack post waits until then, so an undo
 *  withdraws it before anyone reads it. */
export function Done(props: { data: Confirmed; busy: boolean; undo?: () => void }) {
  const { data, busy } = props;
  const [at] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  const until = data.undo_until ? new Date(data.undo_until).getTime() : 0;
  const open = Boolean(props.undo) && data.status === "executed" && until > now;
  useEffect(() => {
    if (!open) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [open]);
  const again = data.status === "already_executed";
  const effects = data.side_effects ?? [];
  const seconds = Math.max(0, Math.ceil((until - now) / 1000));
  return (
    <section class="screen" aria-label="Action complete">
      <div class="card sheet done">
        <svg class="done-mark" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="11" />
          <path d="M7 12.5l3.2 3.2L17 9" />
        </svg>
        {/* Only the outcome is a live region: the ticking Undo countdown must not be re-announced. */}
        <div role="status">
          <p class="title">{again ? "Already done" : "Done"}</p>
          <p>{afterTitle(data.summary, "Done")}</p>
        </div>
        {effects.length > 0 && (
          <ul class="receipt">
            {effects.map((e) => {
              // A post still waiting out the undo window has not happened yet: no check for it.
              const held = open && e.startsWith("slack_post:");
              return (
                <li key={e} class={held ? "held" : undefined}>
                  {effectText(e, true, held)}
                </li>
              );
            })}
          </ul>
        )}
        {open && (
          <div class="undo-row">
            <button type="button" class="secondary" disabled={busy} onClick={props.undo}>
              Undo{" "}
              <span class="undo-left" aria-hidden="true">
                0:{String(seconds).padStart(2, "0")}
              </span>
            </button>
          </div>
        )}
        <p class="meta">
          {again ? "It ran earlier, so nothing ran twice." : `Recorded ${clock(new Date(at).toISOString())}`}
          {data.audit_id && ` · audit ${data.audit_id}`}
        </p>
      </div>
    </section>
  );
}

/** The title already says "Done" or "Undone": the summary's own lead word would repeat it. */
function afterTitle(summary: string, title: string): string {
  return summary.startsWith(`${title}. `) ? summary.slice(title.length + 2) : summary;
}

/** After an undo: the same receipt shape, with a turned-back arrow instead of the check and the
 *  list of what was reversed. Nothing remains in effect, and it says so. */
export function UndoneReceipt({ data }: { data: Undone }) {
  const [at] = useState(Date.now());
  return (
    <section class="screen" aria-label="Action undone">
      <div class="card sheet done undone" role="status">
        <svg class="done-mark" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="11" />
          <path d="M9.5 8.5 6.5 11.5l3 3" />
          <path d="M6.5 11.5h7a3.5 3.5 0 0 1 0 7h-2" />
        </svg>
        <p class="title">Undone</p>
        <p>{afterTitle(data.summary, "Undone")}</p>
        {data.reverted.length > 0 && (
          <ul class="receipt">
            {data.reverted.map((r) => (
              <li key={r}>{revertedText(r)}</li>
            ))}
          </ul>
        )}
        <p class="meta">
          Recorded {clock(new Date(at).toISOString())} · audit {data.audit_id}
        </p>
      </div>
    </section>
  );
}
