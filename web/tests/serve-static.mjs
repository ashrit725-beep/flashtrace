// Tiny static file server for the Playwright tests: serves web/out (the
// `next build` static export) on http://127.0.0.1:4317 with no dependencies.
// /run/?id=x -> out/run/index.html, like the FastAPI server will do. API
// paths (/health, /jobs/...) are NOT served here: each test mocks them in the
// browser with page.route (tests/mock-api.ts).

import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(fileURLToPath(new URL("../out", import.meta.url)));
const PORT = Number(process.env.PORT ?? 4317);
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".txt": "text/plain; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
};

/** Map a URL path to a file inside ROOT, or null. Never escapes ROOT. */
function fileFor(urlPath) {
  let path;
  try {
    path = decodeURIComponent(urlPath);
  } catch {
    return null;
  }
  const candidates = path.endsWith("/")
    ? [join(path, "index.html")]
    : [path, join(path, "index.html"), `${path}.html`];
  for (const candidate of candidates) {
    const full = resolve(ROOT, "." + normalize(candidate));
    if (full !== ROOT && !full.startsWith(ROOT + sep)) return null;
    if (existsSync(full) && statSync(full).isFile()) return full;
  }
  return null;
}

if (!existsSync(join(ROOT, "index.html"))) {
  console.error(`No static export at ${ROOT}. Run "npm run build" first.`);
  process.exit(1);
}

createServer((req, res) => {
  const { pathname } = new URL(req.url ?? "/", "http://localhost");
  const file = fileFor(pathname);
  if (!file) {
    const notFound = join(ROOT, "404.html");
    res.writeHead(404, { "Content-Type": TYPES[".html"] });
    if (existsSync(notFound)) createReadStream(notFound).pipe(res);
    else res.end("Not found");
    return;
  }
  res.writeHead(200, {
    "Content-Type": TYPES[extname(file)] ?? "application/octet-stream",
    "Cache-Control": "no-store",
  });
  createReadStream(file).pipe(res);
}).listen(PORT, "127.0.0.1", () => {
  console.log(`serving ${ROOT} on http://127.0.0.1:${PORT}`);
});
