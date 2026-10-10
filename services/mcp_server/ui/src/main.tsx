// One app for all three views: the host's tool result picks the first screen, and taps move
// between them through server tools (explain → plan → propose → confirm). Nothing is fetched
// any other way, so the view never shows data the model didn't receive (DD §15.6).

import { render } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import { Board } from "./Board";
import { connect, type Bridge } from "./bridge";
import { Confirm, Done, UndoneReceipt } from "./Confirm";
import { Detail, Plan } from "./Detail";
import "./tokens.css";
import "./styles.css";
import { viewFor, type Data, type RecommendedAction, type View } from "./types";

function Skeleton({ kind }: { kind: string }) {
  return (
    <section class="screen" aria-busy="true" aria-label="Loading">
      {Array.from({ length: kind === "board" ? 3 : 1 }, (_, i) => (
        <div key={i} class="card skeleton" />
      ))}
    </section>
  );
}

function Root({ initial }: { initial: string }) {
  const [stack, setStack] = useState<View[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | undefined>();
  const view: View | undefined = stack[stack.length - 1];

  const bridge: Bridge = useMemo(
    () =>
      connect(
        (d) => {
          const v = viewFor(d);
          if (v) setStack([v]);
        },
        (text) => setError(text),
      ),
    [],
  );

  /** Call a tool and show its screen. ``replace`` swaps the top screen instead of stacking one, for
   *  steps that can't be gone back to: an approved sheet, an undone receipt. */
  async function go(
    name: string,
    args: Record<string, unknown>,
    onData?: (d: Data) => View | null,
    replace = false,
  ) {
    setBusy(true);
    setError(null);
    try {
      const data = await bridge.call(name, args);
      const next = onData ? onData(data) : viewFor(data);
      if (next) setStack((s) => [...(replace ? s.slice(0, -1) : s), next]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => setStatus(undefined), [view]);

  const back = stack.length > 1 && (
    <button type="button" class="secondary back" onClick={() => setStack((s) => s.slice(0, -1))} disabled={busy}>
      Back
    </button>
  );

  let body;
  if (!view) body = <Skeleton kind={initial} />;
  else if (view.kind === "board") {
    body = <Board data={view.data} busy={busy} open={(id) => go("explain_deal_risk", { deal_id: id })} />;
  } else if (view.kind === "detail") {
    const dealId = view.data.deal.deal_id;
    body = (
      <Detail
        data={view.data}
        busy={busy}
        status={status}
        dig={() =>
          go("start_deep_triage", { deal_id: dealId }, (d) => {
            setStatus(typeof d.summary === "string" ? d.summary : "Deep review started.");
            return null;
          })
        }
        plan={() => go("get_triage_result", { deal_id: dealId })}
      />
    );
  } else if (view.kind === "plan") {
    const dealId = view.data.deal_id;
    body = (
      <Plan
        data={view.data}
        busy={busy}
        propose={(a: RecommendedAction) =>
          go("propose_action", { deal_id: dealId, action_type: a.action_type, ...a.params })
        }
      />
    );
  } else if (view.kind === "confirm") {
    const token = view.data.confirmation_token;
    body = (
      <Confirm
        data={view.data}
        busy={busy}
        // A tap on the action button is an explicit user action, like a spoken "yes" (DD §15.5).
        // The receipt replaces the sheet: Back must not lead to an approval that already ran.
        approve={() =>
          go(
            "confirm_action",
            { confirmation_token: token },
            (d) => {
              const v = viewFor(d);
              return v?.kind === "done" ? { ...v, token } : v;
            },
            true,
          )
        }
        dismiss={() => setStack((s) => [...s, { kind: "message", text: "Okay, nothing was changed." }])}
        more={() =>
          go("extend_preview", { confirmation_token: token }, (d) => {
            const expires = typeof d.expires_at === "string" ? d.expires_at : null;
            if (expires) {
              setStack((s) =>
                s.map((v) => (v.kind === "confirm" && v.data.confirmation_token === token
                  ? { kind: "confirm", data: { ...v.data, expires_at: expires } }
                  : v)),
              );
            }
            return null;
          })
        }
      />
    );
  } else if (view.kind === "done") {
    const token = view.token;
    body = (
      <Done
        data={view.data}
        busy={busy}
        undo={token ? () => go("undo_action", { confirmation_token: token }, undefined, true) : undefined}
      />
    );
  } else if (view.kind === "undone") {
    body = <UndoneReceipt data={view.data} />;
  } else {
    body = (
      <section class="screen">
        <div class="card notice" role="status">
          <p>{view.text}</p>
        </div>
      </section>
    );
  }

  return (
    <main>
      {back}
      {error && (
        <p class="error" role="alert">
          {error}
        </p>
      )}
      {body}
    </main>
  );
}

const root = document.getElementById("app");
if (root) render(<Root initial={root.dataset.view ?? "board"} />, root);
