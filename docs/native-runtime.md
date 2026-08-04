# Native runtime boundary

MJJ keeps its control plane portable while moving bounded computation into Mojo.
On Python 3.11 and newer, the base wheel has no third-party runtime dependency.
This makes headless execution, search, and the stdlib interactive composer a
small deployment target without weakening the guarded-fallback rule.

```bash
uv tool install mojojojo-agent          # lean base
uv tool install 'mojojojo-agent[full]'  # rich TUI and normalized vision
```

The standalone release executable includes `full` so its user-facing keyboard
and image behavior stays consistent on Windows, Linux, and macOS. Imports remain
lazy: headless commands do not initialize the composer or pixel decoder.

## Dependency audit

| resolved package | product boundary |
| --- | --- |
| `pillow` | optional `vision`/`full`; WebP normalization and ANSI previews |
| `prompt-toolkit` | optional `tui`/`full`; completion, history, multiline editing, and portable hotkeys |
| `wcwidth` | transitive only through optional prompt-toolkit |
| `pytest`, `pytest-asyncio`, `iniconfig`, `pluggy`, `packaging`, `pygments`, `colorama` | development/test environment only; absent from wheel requirements |
| `typing-extensions` | resolver compatibility dependency where required; not imported by the MJJ runtime |
| `tomli` | Python 3.10 compatibility only; Python 3.11+ uses `tomllib` from the standard library |

The stdlib image path reads bounded PNG, JPEG, GIF, and WebP headers and sends
an input unchanged only when its longest edge is at most 2048 pixels. Larger
inputs fail with an actionable request for the `vision` extra rather than being
silently sent at high token cost. Kitty preview invokes `icat` directly; ANSI
pixel rendering requires `vision`.

## Why the control plane is not pure Mojo yet

Search scoring, embedding scans, and supported execution kernels already use
Mojo behind stable guarded boundaries. The session store, HTTP provider client,
portable process layer, and terminal editor remain Python until their Mojo
replacements can meet the same Windows and Linux behavior and fallback tests.
The current Mojo distribution is not a reliable native Windows packaging target,
so replacing those pieces today would remove Windows support rather than improve
it.

Migration follows three rules:

1. Keep model-visible schemas and persisted session formats stable.
2. Land a tested portable fallback before enabling a native implementation.
3. Measure hot paths through `bench/`; do not count test-runner dependencies as
   runtime work or claim speedups from dependency renaming.
