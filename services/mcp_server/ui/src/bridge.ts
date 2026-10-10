// The ext-apps App wrapper (TD §6.6): tool results in, server tool calls out, host theme applied.
//
// ⚠ Verify (S3): written against @modelcontextprotocol/ext-apps 2.0.3 (App, ontoolresult,
// callServerTool, onhostcontextchanged, applyDocumentTheme). Alexa+'s host support is unconfirmed.

import { App, applyDocumentTheme } from "@modelcontextprotocol/ext-apps";
import type { Data } from "./types";

export interface Bridge {
  /** Call a Watchfloor tool through the host; resolves with its structuredContent. */
  call(name: string, args: Record<string, unknown>): Promise<Data>;
}

interface ToolResult {
  structuredContent?: unknown;
  content?: { type: string; text?: string }[];
  isError?: boolean;
}

/** An error's summary is its message plus what to do next, the same words Alexa+ would speak. */
function message(r: ToolResult): string {
  const sc = r.structuredContent as { error?: { message?: string }; summary?: string } | undefined;
  return (
    sc?.summary ??
    sc?.error?.message ??
    r.content?.find((c) => c.type === "text")?.text ??
    "That didn't work. Please try again."
  );
}

export function connect(onData: (d: Data) => void, onError: (text: string) => void): Bridge {
  const app = new App({ name: "watchfloor-ui", version: "1.0.0" }, {}, { autoResize: true });

  app.ontoolresult = (result: ToolResult) => {
    const sc = result.structuredContent;
    if (result.isError) onError(message(result));
    else if (sc && typeof sc === "object") onData(sc as Data);
  };
  app.onhostcontextchanged = (ctx) => {
    if (ctx.theme) applyDocumentTheme(ctx.theme);
  };

  app
    .connect()
    .then(() => {
      const theme = app.getHostContext()?.theme;
      if (theme) applyDocumentTheme(theme);
    })
    .catch(() => onError("This view could not connect to its host."));

  return {
    async call(name, args) {
      const result = (await app.callServerTool({ name, arguments: args })) as ToolResult;
      if (result.isError) throw new Error(message(result));
      const sc = result.structuredContent;
      if (!sc || typeof sc !== "object") throw new Error("The tool returned no data.");
      return sc as Data;
    },
  };
}
