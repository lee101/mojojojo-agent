# Image Rift

A build-free WebGL image-to-image shader lab. It opens with the included mascot and accepts local PNG, JPEG, WebP, or GIF images by picker or drag-and-drop. Four shader treatments are included:

- **Prism fault** — polar displacement, pointer refraction, and chromatic separation
- **Signal relief** — Sobel edge topography with quantized color contours
- **Liquid mercury** — domain-warped luminance rendered as flowing chrome
- **Pulse print** — animated graphic halftone with duotone registration lines

Uploads stay in the browser and are never transmitted.

## Run

```bash
python -m http.server 8000
# open http://localhost:8000/visualbench/gallery/image-rift/
```

No runtime libraries are required; optional UI fonts load from Google Fonts. The bundled default image ensures the lab still works offline with system fonts.

## Deterministic captures

Use, for example, `?mode=mercury&motion=0&time=3.25&intensity=72&detail=56&hue=18&seed=7`. Modes are `prism`, `relief`, `mercury`, and `halftone`; numeric values are clamped. The page sets `window.__VISUALBENCH_READY__ = true` and `body[data-ready="true"]` after the default texture renders.

## License

The demo code is MIT licensed; see [LICENSE.txt](LICENSE.txt). `assets/mascot.webp` is copied from this repository's project artwork and is included for use with this demo; retain its attribution and the repository license notice when redistributing it.
