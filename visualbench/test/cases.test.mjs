import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const cases = JSON.parse(await readFile(new URL("../cases.json", import.meta.url), "utf8"));

test("capture ids and paths are unique and deterministic", () => {
  assert.equal(new Set(cases.map(item => item.id)).size, cases.length);
  assert.equal(new Set(cases.map(item => item.path)).size, cases.length);
  for (const item of cases) {
    assert.match(item.id, /^[a-z0-9-]+$/);
    assert.match(item.path, /motion=0/);
    assert.ok(item.viewport.width >= 320);
    assert.ok(item.prompt.length > 30);
  }
});

test("native generated fixture is standalone and capture-aware", async () => {
  const generated = await readFile(
    new URL("../gallery/signal-forge/index.html", import.meta.url),
    "utf8"
  );
  assert.match(generated, /window\.__VISUALBENCH_READY__/);
  assert.match(generated, /motion/);
  assert.doesNotMatch(generated, /https?:\/\//);
  assert.ok(generated.length > 10_000);
  const transformed = await readFile(
    new URL("../gallery/signal-rift/index.html", import.meta.url),
    "utf8"
  );
  assert.match(transformed, /data:image\/webp;base64,/);
  assert.match(transformed, /"kind":"image-rift"/);
  assert.doesNotMatch(transformed, /https?:\/\//);
});
