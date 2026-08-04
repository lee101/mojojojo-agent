---
name: visualizer
description: Build deterministic procedural WebGL or image-transform visualizers with minimal model-written source and measurable visualbench output.
---

# Visualizer workflow

Use the native scaffold first; it expands a short shell command into a complete,
self-contained WebGL experience without spending model tokens writing shader,
layout, resize, fallback, and deterministic-capture boilerplate.

1. Run `mjj visualize --help` only if the requested arguments are unclear.
2. Scaffold inside the requested repository:
   `mjj visualize OUTPUT --kind KIND --palette PALETTE --seed N --title "TITLE"`.
3. For image-to-image work, add `--image PATH`. The generator orientation-fixes,
   bounds, and embeds the source as quality-85 WebP; do not base64-encode it in a
   model response.
4. Available procedural kinds are `aurora`, `contours`, `cells`, and `tunnel`.
   Image-aware kinds are `image-rift` and `image-relief` (they also have a
   procedural fallback).
5. Open the generated `index.html` only when task-specific art direction needs
   changes. Preserve `motion=0`, `time=N`, `window.__VISUALBENCH_READY__`, and
   the WebGL failure state so captures remain deterministic and testable.
6. Validate with the project's browser tests. In mojojojo-agent itself, run
   `cd visualbench && npm run bench` to generate fixtures, capture screenshots,
   score health/artistic proxies, and measure the token/speed budget.

Prefer changing palette, seed, title, and kind before editing the shader. This
keeps tool traffic small and makes visual variants comparable.
