# mjj visualbench

This is the reproducible visual feedback loop for multimodal coding runs and
the native `mjj visualize` scaffolder. It measures four separate properties:

1. **runtime health** — WebGL readiness, canvas size, console errors, fallback,
   blank/flat output, and mobile composition;
2. **visual structure** — entropy, edge energy, colorfulness, luminance range,
   active spatial tiles, clipping, and center/border separation;
3. **speed** — native source generation, first WebGL frame, page readiness,
   screenshot encoding, and total capture time;
4. **token efficiency** — generated-source size versus first-use and repeated
   harness context, plus a lossless output-budget sweep.

Run everything:

```bash
cd visualbench
npm install
npm run bench
```

Or run individual stages:

```bash
npm run generate   # regenerate gallery/signal-forge through mjj visualize
npm test           # manifest and standalone-fixture contracts
npm run capture    # deterministic Chromium/SwiftShader screenshots
npm run score      # health proxies, diversity matrix, contact sheet
npm run budget     # source expansion, generation latency, token sweep
```

Screenshots and JSON reports land in ignored `output/`. `capture.mjs` uses an
installed Playwright browser or its normal cache. Run `npx playwright install
chromium` once if necessary; `PLAYWRIGHT_CHROMIUM_EXECUTABLE` can select a
system browser.

## Cases and artistic review

The twelve current cases cover a Three.js ocean, four image-to-image shaders,
desktop/mobile viewports, and four native Signal Forge modes: aurora, contour
topography, Voronoi cells, and a polar signal tunnel. Two more cases exercise
native quality-85 WebP image embedding through chromatic rift and relief modes.
Every URL fixes motion and time, and every page exposes
`window.__VISUALBENCH_READY__`.

`score.py` writes `scores.json`, a richer `report.json`, and a quality-85 WebP
`contact-sheet.webp`. The `interest_score` is a deterministic regression proxy,
not an aesthetic oracle. It rewards balanced complexity, color/dynamic range,
spatial activity, and composition while penalizing clipping. Always inspect the
contact sheet before making an artistic claim.

In the August 2026 reference capture, all twelve cases passed without WebGL
fallbacks or console errors. The four procedural Signal Forge modes scored
85.2–92.9 on the proxy. The image modes scored 55.5 and 64.8 because their
art direction deliberately preserves large black negative-space regions; that
loss is retained rather than tuning the metric around it. Across all six native
modes, pairwise normalized image distance averaged 0.347 with a 0.204 minimum,
so the variants did not collapse into near-duplicates. These figures are
produced by `npm run score` from the captured PNGs and should be interpreted
beside the contact sheet.

## Speed and token budget

`budget_bench.py` repeatedly generates real files in a temporary workspace. It
uses the same four-characters-per-token estimator as the harness ledger and
includes the actual bundled skill output, shell-call JSON, and default CLI
result. It does not count hypothetical model reasoning or claim API-token
pricing.

Reference command and result on Linux x86-64, Xeon E5-2697 v4:

```text
$ uv run --project .. python budget_bench.py --iterations 100
native generation median 0.370 ms · p95 0.430 ms
source ~3364 tokens · first use 453 · repeat 51 · amplification 7.4x/66.0x
minimum lossless shell-result budget 24 tokens · always-on schema tax 0
```

Browser rendering is reported separately in `output/results.json`. On the same
run, procedural pages produced their first WebGL frame in 32–62 ms under
SwiftShader; embedded-image decode moved the two image modes to 153–158 ms.
Desktop PNG encoding dominated total capture time (up to about 3.0 s for the
high-entropy generated fields), which is why capture time is not presented as
shader frame rate.

The 24-token result budget applies only to the concise visualizer command
result. The general shell budget should remain large enough to preserve build
errors and test diagnostics.

Publish losses beside wins. A smaller context or faster shader is only better
when browser health, visual structure, diversity, and the contact sheet remain
acceptable.
