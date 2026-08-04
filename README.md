# mojojojo-agent

A fast, token-efficient coding agent for your terminal.

[![CI](https://github.com/lee101/mojojojo-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/lee101/mojojojo-agent/actions/workflows/ci.yml)
[![Native Mojo](https://github.com/lee101/mojojojo-agent/actions/workflows/mojo.yml/badge.svg)](https://github.com/lee101/mojojojo-agent/actions/workflows/mojo.yml)
[![MIT licensed](https://img.shields.io/badge/license-MIT-8b5cf6.svg)](LICENSE)

[Website](https://mojojojo.cc/agent) ·
[Getting started](docs/getting-started.md) ·
[Documentation](docs/README.md) ·
[Releases](https://github.com/lee101/mojojojo-agent/releases) ·
[Issues](https://github.com/lee101/mojojojo-agent/issues) ·
[Development](DEV.md) ·
[MIT licence](LICENSE)

MJJ reads a repository, edits files, runs tests, and reports the result like
Codex or Claude Code. Its search, output filtering, patching, and optional Mojo
hot paths are designed to send the model useful evidence instead of whole-file
dumps.

![A real Mojojojo Agent Signal Forge browser capture: a high-contrast ultraviolet field rendered by the deterministic Visualbench workflow.](docs/assets/visualbench-signal-forge.webp)

*Actual deterministic WebGL output from `mjj visualize`, captured and scored by
[Visualbench](visualbench/README.md). No mockup or generated marketing frame.*

## Install

Linux and macOS:

```bash
curl -fsSL https://mojojojo.cc/install.sh | sh
```

Windows PowerShell:

```powershell
irm https://mojojojo.cc/install.ps1 | iex
```

The installers select the correct release, verify its SHA-256 checksum, and do
not require a system Python. The feature-complete Python package is also
available:

```bash
uv tool install 'mojojojo-agent[full]'
```

Use `uv tool install mojojojo-agent` for the dependency-free base on Python
3.11+. It uses a stdlib line composer and accepts already-bounded common image
formats. See the [native runtime guide](docs/native-runtime.md).

## Start in a minute

```bash
cd your-project
mjj login chatgpt
mjj
```

Inside the app, type `/` for commands, `@` to attach a file, or run a task:

```text
Find the failing authentication test, fix it, and verify the focused suite.
```

For scripts and CI:

```bash
mjj exec "fix the failing tests and explain the cause"
mjj exec --json "review the current diff"
mjj exec --image screenshot.png "match this design"
```

MJJ can reuse an existing ChatGPT/Codex login or securely store OpenPaths,
OpenRouter, and OpenAI API keys. `auto` prefers an explicitly scoped MJJ OpenAI
key, then OpenPaths, then a ChatGPT login or OpenAI key. Select a provider when
the transport must be fixed.

## Pick cost, speed, or capability

Model aliases keep everyday choices stable while concrete model catalogs
change:

| model | use it for |
| --- | --- |
| `auto-code` | balanced coding default |
| `auto-fast` | quick edits, search, and latency-sensitive work |
| `auto-cheap` | routine high-volume tasks |
| `auto-best` | hard debugging, architecture, and review |
| `auto-openai` | balanced coding, constrained to OpenAI models |
| `auto-openai-fast` | lower-cost OpenAI coding |
| `auto-openai-best` | capability-first OpenAI coding |

```bash
mjj exec --provider openpaths --model auto-code "repair the build"
mjj exec --provider openai --model auto-fast "rename this field safely"
mjj exec --provider openrouter --model auto-openai "review this migration"
```

OpenPaths aliases use its live task router. Direct OpenAI aliases currently map
to the Sol, Terra, and Luna capability tiers. Provider-constrained aliases keep
the chosen model lab fixed even when OpenPaths or OpenRouter is the transport.
Use `/models` to see the current map and `/model NAME` to switch without
restarting. See [models and prompt caching](docs/models-and-cache.md) for the
exact mapping and tradeoffs.

## Prompt caching is automatic

`MJJ_CACHE_MODE=auto` is the default. MJJ fingerprints stable instructions and
tool schemas, avoids paid one-shot cache writes on current OpenAI models, and
adds an explicit reusable breakpoint when observed reuse fits the 30-minute
cache window. Claude model requests through OpenPaths or OpenRouter use an
adaptive 5-minute, 1-hour, or no-cache decision based on prefix reuse cadence.

Use `/cache` to inspect the policy and actual cache read/write token counts:

```text
/cache
/cache off
/cache explicit
/usage
```

The model response remains freshly generated; only repeated prompt prefill is
reused. Cache policy can reduce input cost and latency, but the best result
depends on real reuse, so MJJ reports provider telemetry instead of claiming a
hit in advance.

## Productive terminal workflow

- Searchable slash commands, history, completion, multiline editing, and
  portable model/reasoning hotkeys in the full build.
- `@path` and `@path:START-END` attach bounded context; image mentions use the
  same vision path as `--image`.
- `/permissions` switches between `auto`, `ask`, and `read-only`.
- `/diff`, `/review`, `/status`, `/checkpoints`, and conflict-safe `/undo` keep
  repository control in the app.
- `/goal`, `/plan`, resumable sessions, forks, HTML/JSONL export, and bounded
  autonomous continuation support longer work.
- `AGENTS.md`, `CLAUDE.md`, nested instructions, skills, MCP servers, language
  servers, and explicitly trusted plugins are discovered automatically.
- Kitty terminals display real images with `icat`; other full installations
  receive bounded ANSI previews. Image bytes never enter terminal transcripts.

The model sees a compact built-in toolset for ranked search, numbered reads,
atomic patches, shell execution, syntax/compiler checks, LSP navigation,
Python/Mojo execution, plans, skills, and terminal image events. Every tool
result passes through one token budget and oversized results remain recoverable
from disk.

## Learn more

- [Getting started](docs/getting-started.md)
- [Models and prompt caching](docs/models-and-cache.md)
- [Configuration](docs/config.md)
- [Tools](docs/tools.md)
- [Search](docs/search.md)
- [Windows and Linux support](docs/platforms.md)
- [Agent feature parity](docs/pi-parity.md)
- [Full documentation index](docs/README.md)

## Develop and benchmark

The project is open source and tested on Windows, Linux, Python 3.10/3.13, and
the pinned Mojo compiler. Start with [DEV.md](DEV.md) for architecture, native
builds, tests, benchmarks, and release work, or [CONTRIBUTING.md](CONTRIBUTING.md)
for the contribution workflow.

[mojosub]: https://github.com/lee101/mojosub
[mojo-embed]: https://github.com/lee101/mojo-embed
