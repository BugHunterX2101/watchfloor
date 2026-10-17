// The honest label the rules require on this path (DD §17): always visible, never dismissible.

export function renderBanner(el: HTMLElement): void {
  el.className = "banner";
  el.innerHTML = "";
  const title = document.createElement("strong");
  title.textContent = "Simulated Alexa+ experience";
  const note = document.createElement("span");
  note.textContent =
    "A stand-in for the Alexa+ app. The Watchfloor MCP server, sign-in and tools behind it are the real ones; the companies, people and deals are made-up sample data.";
  el.append(title, note);
}
