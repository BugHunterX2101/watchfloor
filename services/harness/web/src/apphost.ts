// MCP Apps host (DD §6.6, §17), on the ext-apps AppBridge: renders a ui:// view in a sandboxed
// iframe, hands it the tool result after the ui/initialize handshake, and forwards the view's
// tool calls (row tap, Propose, Approve) to the harness API with the user's token.
//
// ⚠ Verify (S3): built against @modelcontextprotocol/ext-apps 2.0.3. Alexa+'s own host is what
// the real add-on renders in; this host only mirrors it for Path B.
//
// The iframe gets sandbox="allow-scripts" only: no same-origin access, no forms, no popups and no
// top navigation. Its origin is "null", so the transport matches messages by source window.

import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";
import { callTool, type Call } from "./api";

let current: AppBridge | null = null;

function theme(): "light" | "dark" {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export async function renderView(
  container: HTMLElement,
  view: { uri: string; html: string; data: string },
  onCall: (call: Call) => void,
): Promise<void> {
  await clear();
  let data: Record<string, unknown> = {};
  try {
    data = JSON.parse(view.data) as Record<string, unknown>;
  } catch {
    data = {};
  }

  const frame = document.createElement("iframe");
  frame.setAttribute("sandbox", "allow-scripts");
  frame.setAttribute("referrerpolicy", "no-referrer");
  frame.title = view.uri;
  frame.srcdoc = view.html;
  container.replaceChildren(frame);
  const target = frame.contentWindow;
  if (!target) return;

  const bridge = new AppBridge(
    null,
    { name: "watchfloor-harness", version: "1.0.0" },
    { serverTools: {} },
    { hostContext: { theme: theme(), displayMode: "inline" } },
  );
  bridge.oninitialized = () => {
    void bridge.sendToolResult({ content: [], structuredContent: data });
  };
  bridge.oncalltool = async (params) => {
    const result = await callTool(params.name, (params.arguments ?? {}) as Record<string, unknown>);
    onCall(result.call);
    return {
      content: result.content,
      structuredContent: result.structuredContent ?? undefined,
      isError: result.isError,
    };
  };
  bridge.onsizechange = ({ height }) => {
    // The view reports its content height; the frame's own border sits outside that (border-box).
    const border = frame.offsetHeight - frame.clientHeight;
    if (height) frame.style.height = `${Math.min(Math.ceil(height), 1600) + border}px`;
  };
  await bridge.connect(new PostMessageTransport(target, target));
  current = bridge;
}

async function clear(): Promise<void> {
  const old = current;
  current = null;
  if (old) await old.close().catch(() => undefined);
}

export async function clearView(container: HTMLElement, note: string): Promise<void> {
  await clear();
  const p = document.createElement("p");
  p.className = "muted";
  p.textContent = note;
  container.replaceChildren(p);
}
