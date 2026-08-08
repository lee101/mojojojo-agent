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
Native-library probing is also lazy, and file completion does not walk the
workspace until the user types an `@file` or image completion.

## Startup measurement

Run `python bench/startup_bench.py` to launch fresh child processes under a
temporary MJJ home. On the 2026-08-05 audit host, seven measured runs produced:

| case | median | p95 |
| --- | ---: | ---: |
| `--version` | 65.020 ms | 115.214 ms |
| `--help` | 164.927 ms | 165.346 ms |
| dependency-free UI start and `/exit` | 315.486 ms | 315.984 ms |

Interactive terminals print a bootstrap status before project context and the
rich composer load. These are complete-process latency measurements, not model
response-time claims.

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

Search scoring, embedding scans, identifier tokenize/embed, BM25 posting
accumulation, and supported execution kernels already use Mojo behind stable
guarded boundaries. The session store, HTTP provider client, portable process
layer, and terminal editor remain Python until their Mojo replacements can meet
the same Windows and Linux behavior and fallback tests. The current Mojo
distribution is not a reliable native Windows packaging target, so replacing
those pieces today would remove Windows support rather than improve it.

Migration follows three rules:

1. Keep model-visible schemas and persisted session formats stable.
2. Land a tested portable fallback before enabling a native implementation.
3. Measure hot paths through `bench/`; do not count test-runner dependencies as
   runtime work or claim speedups from dependency renaming.

## uv and pixi peer wiring

Keep the split sharp:

| tool | owns |
| --- | --- |
| `uv` | Python package, extras, tests, wheels |
| `pixi` | pinned Mojo nightly and `mojo build` of shared libraries |

Extras stay optional so the base wheel stays dependency-free on Python 3.11+:

| extra | peer / package | role |
| --- | --- | --- |
| `accel` | `mojosub` | tiered JIT for numeric kernels (`quantize_i8`, BM25 fallback) |
| `syntax` | `tree-sitter-language-pack` | typed repo-map symbols and non-Python syntax checks |

Shared libraries are discovered, never imported as Python modules:

1. `MJJ_MOJO_EMBED_LIB`
2. this repo's `build/libmjj_search.so` (`pixi run mojo-check`)
3. adjacent `../mojo-embed/build`
4. packaged / system paths

When several candidates load, MJJ prefers the library that exports the fullest
ABI (`search`, `tokenize`, `embed`, `bm25`). Missing symbols degrade that path
only; the harness keeps running.

Local peer overrides while developing both sides:

```bash
# Python JIT peer
uv sync --extra accel
uv pip install -e ../mojosub

# Native search / BM25 ABI
pixi run mojo-check

# Typed symbols (portable tree-sitter today)
uv sync --extra syntax
```

`../mojo-tree-sitter*` checkouts are the future Mojo host for batch parse /
symbol work. Until that ABI is measured and wired, `syntax` keeps the same
model-visible maps via the Python tree-sitter pack. Do not vendor peer trees
into this repository; discover them the same way as `mojo-embed`.
