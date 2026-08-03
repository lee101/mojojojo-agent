# Pelagic / resultwatershader

A standalone Three.js procedural ocean study. Six directional wave harmonics drive a displaced mesh with crest foam, Fresnel reflections, atmospheric depth, and an art-directed sky. Drag to orbit, scroll to zoom, or use the weather controls.

## Run

```bash
python -m http.server 8000
# open http://localhost:8000/visualbench/gallery/resultwatershader/
```

Three.js and the optional display fonts load from CDNs; no build step is required. If WebGL is unavailable, an accessible fallback message is shown.

## Deterministic captures

Use `?motion=0&time=6.5&preset=open&swell=1&chop=.65&light=18`. `preset` accepts `calm`, `open`, or `storm`; numeric controls are clamped to their UI ranges. The page sets `window.__VISUALBENCH_READY__ = true` and `body[data-ready="true"]` after its first render.

## License

MIT — see [LICENSE.txt](LICENSE.txt). Three.js is MIT licensed; Google Fonts are served under their respective OFL licenses.
