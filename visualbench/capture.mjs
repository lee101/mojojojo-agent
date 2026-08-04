import { createReadStream, existsSync, readdirSync, statSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { homedir } from "node:os";
import { extname, join, normalize, resolve, sep } from "node:path";
import { chromium } from "playwright";

const root = resolve(import.meta.dirname);
const cases = JSON.parse(await readFile(join(root, "cases.json"), "utf8"));
const output = join(root, "output");
await mkdir(output, { recursive: true });

const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json",
  ".webp": "image/webp"
};

const server = createServer((request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
  if (pathname === "/favicon.ico") {
    response.writeHead(204).end();
    return;
  }
  let target = normalize(join(root, pathname));
  if (target !== root && !target.startsWith(`${root}${sep}`)) {
    response.writeHead(403).end("forbidden");
    return;
  }
  try {
    if (statSync(target).isDirectory()) target = join(target, "index.html");
    response.writeHead(200, { "content-type": contentTypes[extname(target)] || "application/octet-stream" });
    createReadStream(target).pipe(response);
  } catch {
    response.writeHead(404).end("not found");
  }
});
await new Promise(resolveReady => server.listen(0, "127.0.0.1", resolveReady));
const { port } = server.address();

function installedChromium() {
  if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE) return process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
  const preferred = chromium.executablePath();
  if (existsSync(preferred)) return preferred;
  const cache = join(homedir(), ".cache", "ms-playwright");
  if (!existsSync(cache)) return undefined;
  const versions = readdirSync(cache).filter(name => name.startsWith("chromium-")).sort().reverse();
  for (const version of versions) {
    for (const relative of ["chrome-linux64/chrome", "chrome-linux/chrome"]) {
      const candidate = join(cache, version, relative);
      if (existsSync(candidate)) return candidate;
    }
  }
  return undefined;
}

const executablePath = installedChromium();
const browser = await chromium.launch({
  ...(executablePath ? { executablePath } : {}),
  headless: true,
  args: ["--use-angle=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"]
});
const results = [];
try {
  for (const visualCase of cases) {
    const page = await browser.newPage({ viewport: visualCase.viewport, deviceScaleFactor: 1 });
    const consoleErrors = [];
    page.on("console", message => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    const started = performance.now();
    await page.goto(`http://127.0.0.1:${port}${visualCase.path}`, { waitUntil: "networkidle" });
    await page.waitForFunction(() => window.__VISUALBENCH_READY__ === true, null, { timeout: 20_000 });
    const loaded = performance.now();
    const state = await page.evaluate(() => ({
      ready: document.body.dataset.ready,
      fallback: document.body.dataset.ready === "fallback",
      canvas: [...document.querySelectorAll("canvas")].map(canvas => ({ width: canvas.width, height: canvas.height })),
      metrics: window.__VISUALBENCH_METRICS__ || null
    }));
    const screenshot = join(output, `${visualCase.id}.png`);
    const screenshotStarted = performance.now();
    await page.screenshot({ path: screenshot, fullPage: true });
    const finished = performance.now();
    results.push({
      id: visualCase.id,
      screenshot,
      milliseconds: Math.round(finished - started),
      loadMilliseconds: Math.round(loaded - started),
      screenshotMilliseconds: Math.round(finished - screenshotStarted),
      bytes: statSync(screenshot).size,
      consoleErrors,
      ...state
    });
    await page.close();
  }
} finally {
  await browser.close();
  await new Promise(resolveClosed => server.close(resolveClosed));
}

await writeFile(join(output, "results.json"), JSON.stringify(results, null, 2) + "\n");
for (const result of results) {
  console.log(`${result.fallback || result.consoleErrors.length ? "FAIL" : "PASS"} ${result.id.padEnd(22)} ${String(result.bytes).padStart(8)} bytes  ${result.milliseconds} ms`);
  for (const error of result.consoleErrors) console.log(`     console: ${error}`);
}
if (results.some(result => result.fallback || result.consoleErrors.length || result.bytes < 20_000)) process.exitCode = 1;
