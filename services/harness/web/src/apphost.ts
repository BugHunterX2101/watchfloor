// MCP Apps host (DD §6.6, §17), on the ext-apps AppBridge: renders a ui:// view in a sandboxed
// iframe, hands it the tool result after the ui/initialize handshake, and forwards the view's
// tool calls (row tap, Propose, the sheet's action, Undo) to the harness API with the user's token.
//
// ⚠ Verify (S3): built against @modelcontextprotocol/ext-apps 2.0.3. Alexa+'s own host is what
// the real add-on renders in; this host only mirrors it for Path B.
//
// The iframe gets sandbox="allow-scripts" only: no same-origin access, no forms, no popups and no
// top navigation. Its origin is "null", so the transport matches messages by source window.
//
// Device preview: "fit" lays the view out at the stage's width. Compact, Standard and Large lay it
// out at the Echo Show widths of DD §15.1 and scale the frame down to fit, so the view's own
// width-based type scale shows what that device would render.

import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";
import { callTool, type Call } from "./api";

export type Device = "fit" | "compact" | "standard" | "large";

const WIDTHS: Record<Exclude<Device, "fit">, number> = { compact: 960, standard: 1280, large: 1920 };

let current: AppBridge | null = null;
let frame: HTMLIFrameElement | null = null;
let box: HTMLElement | null = null;
let device: Device = "fit";
let contentHeight = 0;
let themeChoice: "auto" | "light" | "dark" = "auto";
const observer = new ResizeObserver(() => layout());
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

function theme(): "light" | "dark" {
  if (themeChoice !== "auto") return themeChoice;
  return darkQuery.matches ? "dark" : "light";
}

/** What the view is told about its surface. An Echo Show size is a touch screen without hover, so
 *  the view sees the same capabilities it would on the device; Fit is this browser window. */
function hostContext() {
  const echo = device !== "fit";
  const width = device === "fit" ? (box?.clientWidth ?? 768) : WIDTHS[device];
  return {
    theme: theme(),
    displayMode: "inline" as const,
    platform: "web" as const,
    deviceCapabilities: { touch: echo || matchMedia("(pointer: coarse)").matches, hover: !echo },
    containerDimensions: { width, maxHeight: 2400 },
    locale: navigator.language,
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
}

function pushContext(): void {
  current?.setHostContext(hostContext());
}

/** Light, dark or follow the system: applied to this page and sent to the view as host context. */
export function setTheme(choice: "auto" | "light" | "dark"): void {
  themeChoice = choice;
  if (choice === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = choice;
  pushContext();
}

darkQuery.addEventListener("change", () => {
  if (themeChoice === "auto") pushContext();
});

function layout(): void {
  if (!frame || !box) return;
  if (device === "fit") {
    frame.style.width = "100%";
    frame.style.transform = "";
    box.style.height = "";
    if (contentHeight) frame.style.height = `${contentHeight}px`;
    return;
  }
  const width = WIDTHS[device];
  const scale = Math.min(1, box.clientWidth / width);
  frame.style.width = `${width}px`;
  frame.style.transform = `scale(${scale})`;
  if (contentHeight) {
    frame.style.height = `${contentHeight}px`;
    box.style.height = `${Math.ceil(contentHeight * scale)}px`;
  }
}

export function setDevice(next: Device): void {
  device = next;
  layout();
  pushContext();
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

  const el = document.createElement("iframe");
  el.setAttribute("sandbox", "allow-scripts");
  el.setAttribute("referrerpolicy", "no-referrer");
  el.title = `Watchfloor view: ${view.uri}`;
  el.srcdoc = view.html;
  container.replaceChildren(el);
  frame = el;
  box = container;
  contentHeight = 0;
  observer.disconnect();
  observer.observe(container);
  layout();
  const target = el.contentWindow;
  if (!target) return;

  const bridge = new AppBridge(
    null,
    { name: "watchfloor-harness", version: "1.0.0" },
    { serverTools: {} },
    { hostContext: hostContext() },
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
    if (!height) return;
    contentHeight = Math.min(Math.ceil(height), 2400);
    layout();
  };
  await bridge.connect(new PostMessageTransport(target, target));
  current = bridge;
}

async function clear(): Promise<void> {
  const old = current;
  current = null;
  frame = null;
  observer.disconnect();
  if (old) await old.close().catch(() => undefined);
}

export async function clearView(container: HTMLElement): Promise<void> {
  await clear();
  container.style.height = "";
  container.replaceChildren();
}
