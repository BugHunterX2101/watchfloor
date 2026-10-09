// Post-build gate (DD §6.6, TD §10): every view is one self-contained file, ≤ 150 KB gzipped,
// with nothing that would load from the network. Namespace strings such as
// "http://www.w3.org/2000/svg" inside the bundled libraries are identifiers, not requests,
// so the check looks for loading constructs rather than any "http".
import { readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";

const LIMIT = 150 * 1024;
const LOADERS = [
  /<script[^>]+\bsrc\s*=/i,
  /<link[^>]+\bhref\s*=\s*["']?(https?:)?\/\//i,
  /<img[^>]+\bsrc\s*=\s*["']?(https?:)?\/\//i,
  /url\(\s*["']?(https?:)?\/\//i,
  /@import\s+["']?(url\()?\s*["']?(https?:)?\/\//i,
];

let failed = false;
for (const view of ["board", "detail", "confirm"]) {
  const path = new URL(`../../mcp_server/ui_dist/${view}.html`, import.meta.url);
  const html = readFileSync(path, "utf8");
  const gz = gzipSync(html).length;
  const hits = LOADERS.filter((re) => re.test(html)).map(String);
  const ok = gz <= LIMIT && hits.length === 0;
  failed ||= !ok;
  console.log(`${ok ? "ok  " : "FAIL"} ${view}.html  ${(gz / 1024).toFixed(1)} KB gzip${hits.length ? `  loads: ${hits}` : ""}`);
}
process.exit(failed ? 1 : 0);
