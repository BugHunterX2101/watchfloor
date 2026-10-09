// Live captions: while a reply is read aloud, its words "ink in" as the voice reaches them, driven
// by the real speech word boundaries (not a timer), so the screen and the voice never drift apart.
// The line's text is unchanged (only wrapped in spans), so screen readers read it as before. If the
// voice reports no boundaries (many network voices), nothing dims and the line simply stays as is.

export interface Caption {
  word(index: number): void;
  end(): void;
}

export function caption(line: HTMLElement, text: string): Caption {
  const words: { start: number; el: HTMLSpanElement }[] = [];
  const nodes: Node[] = [];
  let offset = 0;
  for (const part of text.split(/(\s+)/)) {
    if (part && !/^\s+$/.test(part)) {
      const el = document.createElement("span");
      el.className = "w";
      el.textContent = part;
      words.push({ start: offset, el });
      nodes.push(el);
    } else if (part) {
      nodes.push(document.createTextNode(part));
    }
    offset += part.length;
  }
  line.replaceChildren(...nodes);

  let reached = -1;
  return {
    word(index) {
      line.classList.add("captioning");
      // The last word starting at or before the boundary; boundaries only move forward.
      let i = reached;
      while (i + 1 < words.length && words[i + 1].start <= index) i++;
      for (let k = Math.max(0, reached); k <= i; k++) words[k].el.classList.add("said");
      words[reached]?.el.classList.remove("now");
      words[i]?.el.classList.add("now");
      reached = i;
    },
    end() {
      line.classList.remove("captioning");
      words[reached]?.el.classList.remove("now");
    },
  };
}
