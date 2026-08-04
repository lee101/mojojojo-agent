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
