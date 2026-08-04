# Native visualizers

`mjj visualize` creates a complete, standalone WebGL visualizer from a compact
command. The generated `index.html` has no runtime dependencies or network
requests and includes resize handling, pointer interaction, reduced-motion
support, a visible WebGL failure state, responsive editorial UI, and stable
capture controls.

```bash
mjj visualize signal-field \
  --kind aurora --palette ultraviolet --seed 29 \
  --title "Living signal field"
```

Procedural kinds are `aurora`, `contours`, `cells`, and `tunnel`. The
`image-rift` and `image-relief` kinds accept an optional image:

```bash
mjj visualize transformed \
  --kind image-rift --palette ember --image reference.png
```

The image follows the same path as model vision inputs: EXIF orientation is
applied, the longest edge is bounded to 2048 pixels, and it is embedded as WebP
quality 85. Use `--force` to replace an existing generated `index.html` and
`--json` to return generation timing, source-token size, and image byte counts.
Output paths must remain inside the selected `-C/--cwd` working tree.

For deterministic screenshots, add `?motion=0&time=11`. `kind` and `palette`
can also be varied in the query string without regenerating the file. The page
sets `window.__VISUALBENCH_READY__` after its first WebGL frame and reports
first-frame metadata through `window.__VISUALBENCH_METRICS__`.

## Why this is token efficient

The generator is a CLI primitive rather than another always-visible function
schema. The model already has the shell tool, and the bundled `visualizer`
skill is loaded only for relevant work. A normal result reports just the
relative path, selected style, and byte count; detailed measurements require
`--json`.

The reproducible budget benchmark is:

```bash
cd visualbench
uv run --project .. python budget_bench.py --iterations 100
```

On the August 2026 reference run (Linux x86-64, Xeon E5-2697 v4), the generated
source was approximately 3,364 tokens. Loading the workflow and generating the
first visualizer cost 453 harness-context tokens, while another variant cost 51
tokens: 7.4× and 66.0× source expansion respectively. The normal shell result
remained lossless at a 24-token output budget. Native file generation measured
0.370 ms median and 0.430 ms p95 over 100 variants. These are machine-specific
measurements, not universal performance claims; `budget_bench.py` writes the
complete local report to `visualbench/output/budget.json`.

Do not set the whole agent's shell budget to 24 tokens: builds and test failures
need more diagnostic context. The result shows that visualizer creation itself
does not consume the default 1,600-token allowance.

## Visual validation

Run the full local loop with `cd visualbench && npm run bench`. It regenerates
the committed fixture, runs structural tests, captures every deterministic
desktop/mobile case in Chromium/SwiftShader, builds a WebP contact sheet, and
scores objective health proxies.

The numeric interest score balances entropy, edge energy, colorfulness,
luminance range, spatially active tiles, composition separation, and clipping.
It catches blank, flat, noisy, or collapsed variants; it is not a claim to
measure taste. Inspect `output/contact-sheet.webp` for artistic judgment.
