# Getting started

## Install

Linux and macOS:

```bash
curl -fsSL https://mojojojo.cc/install.sh | sh
```

Windows PowerShell:

```powershell
irm https://mojojojo.cc/install.ps1 | iex
```

Both installers verify the published SHA-256 checksum and smoke-test the new
binary before atomically replacing an existing installation. To pin or stage a
release, download the installer and pass options directly:

```bash
./install.sh --version v0.4.0 --install-dir "$HOME/.local/bin"
```

```powershell
.\install.ps1 -Version v0.4.0 -InstallDir "$env:LOCALAPPDATA\Programs\mjj"
```

The equivalent environment variables are `MJJ_VERSION`, `MJJ_INSTALL_DIR`,
`MJJ_REPO`, and `MJJ_BASE_URL`. PowerShell updates the user PATH by default;
pass `-NoPathUpdate` or set `MJJ_NO_PATH_UPDATE=1` to opt out.

The installers select your platform, download the latest release, verify its
published SHA-256 checksum, and install in a user-owned directory. They do not
require a system Python. You can instead install the feature-complete Python
package with `uv tool install 'mojojojo-agent[full]'`, install the zero-runtime-
dependency base with `uv tool install mojojojo-agent` on Python 3.11+, or
download an archive from
[GitHub Releases](https://github.com/lee101/mojojojo-agent/releases).

The lean base uses a normal line composer and accepts already-bounded common
image formats. The `full` extra adds searchable completion, portable hotkeys,
ANSI previews, orientation handling, resizing, and quality-85 WebP encoding.
Kitty `icat` previews do not require the image extra.

Confirm the installation:

```bash
mjj --version
mjj auth
```

## Authenticate

Use an existing ChatGPT/Codex login:

```bash
mjj login chatgpt
mjj auth --probe
```

For a headless machine, use `mjj login chatgpt --device`. Provider API keys can
be entered without echoing them in the terminal:

```bash
mjj login openpaths
mjj login openrouter
mjj login openai
```

`auto` prefers an explicitly scoped mjj OpenAI key, then
`OPENPATHS_API_KEY`, an existing ChatGPT/Codex sign-in, and finally
`OPENAI_API_KEY`. Select a provider explicitly when reproducible routing matters.

## Run the agent

Change into a repository and launch the interactive app:

```bash
cd your-project
mjj
```

Type `/` to search commands and press Tab to complete commands or valid values.
`/model` opens an arrow-key picker: use Up/Down to highlight a model and Enter
to select it. The numbered fallback also accepts a number, full name, unique
fragment, `next`, or `prev`; arbitrary model IDs are accepted. Shift+Up/Down
changes the reasoning level and the prompt toolbar always shows the active model
and reasoning. F2 or Alt+M cycles models, F3 or Alt+R cycles reasoning, and F4
or Alt+V cycles verbosity. Empty-composer left/right also changes reasoning.
Alt+Enter inserts a newline. Type `@` to attach a fuzzy-matched repository file,
`!command` to run a shell command and retain its output as model context, or
`!!command` to run it only locally. Useful first commands are `/status`,
`/settings`, `/permissions`, `/review`, `/model`, `/reasoning`, `/auth`, and
`/help`.

For scripts and CI, use headless mode:

```bash
mjj exec "find the cause of the failing test, fix it, and run the focused tests"
mjj exec --json "review the current diff"
mjj exec -o final.txt "implement the requested endpoint"
mjj exec --permission-mode read-only @src/app.py "review this file"
```

The final answer goes to stdout. Progress and tool activity go to stderr, so
command substitution and JSONL consumers receive predictable output.

`@path`, `@path:START-END`, and quoted `@"path with spaces"` arguments attach
bounded text context. Mentioned images are precompressed and sent to vision.
Use `/init` to have the agent inspect a repository and generate an `AGENTS.md`,
`/diff` for a bounded working-tree diff, and `/review [focus]` for a findings-first
review that explicitly avoids edits. `/checkpoints` shows automatic patch
snapshots and `/undo` restores the latest conflict-free one.

## Providers, reasoning, and images

```bash
mjj exec --provider openpaths --model openpaths/auto-code \
  --effort high "fix the tests"
mjj exec --provider openpaths --model grok-4.5 "implement and test the change"
mjj exec --provider openrouter --model x-ai/grok-4.5 "review this repository"
mjj exec --provider openai --model gpt-5.3-codex "repair the failing build"
mjj exec --provider openrouter --model openrouter/auto "review this repository"
mjj exec --image screenshot.png "match the current page to this reference"
```

The `/model` picker includes these Grok 4.5 and Codex shortcuts. MJJ detects
the concrete model on each request and adds only one short family-specific
execution hint. Auto routers and unknown models keep the neutral base prompt
with no added tokens.

Images are orientation-corrected, bounded to a 2048-pixel edge, and encoded in
memory as WebP quality 85 before they are sent to model vision. Repeat
`--image` to attach more than one.

In the interactive app, `/image PATH` also previews the queued image and
`/preview PATH` displays it without spending model context. When the agent uses
`display_image`, the result is rendered inline between tool progress and the
next assistant text. Kitty uses `kitten icat`; other color terminals get a
bounded ANSI preview. Pipes, logs, `mjj exec`, and non-TTY output receive no
graphics control sequences.

## Sessions and autonomous work

Runs are saved as append-only JSONL unless `--ephemeral` is used:

```bash
mjj sessions
mjj exec --resume SESSION_ID "continue"
mjj exec --fork SESSION_ID --name alternate "try the other implementation"
mjj export transcript.html --session SESSION_ID
```

The interactive equivalents include `/history`, `/resume`, `/tree`, `/clone`,
`/name`, `/export`, and `/import`.

Autonomy is opt-in. Bound it while learning the workflow:

```bash
mjj exec --auto-next-steps --auto-max-turns 3 \
  "implement this change and validate it"
```

Add `--auto-next-idea` to select a useful follow-on improvement after the
original objective is complete. A zero maximum means continue until interrupted.

For work that must survive sessions and stop on a verifiable condition, use a
durable goal:

```bash
mjj exec --goal "Implement PLAN.md and stop when the test suite passes" \
  --auto-max-turns 8
mjj goal                         # inspect it without calling a model
```

In the terminal app, `/goal OBJECTIVE` starts immediately. `/goal pause`,
`/goal resume`, `/goal complete EVIDENCE`, `/goal blocked REASON`, and
`/goal clear` control the lifecycle. See [durable goals](goals.md).

## Project instructions and skills

Mojo Agent loads bounded `AGENTS.override.md`, `AGENTS.md`, `CLAUDE.md`, and
legacy `CONTEXT.md` instructions from the Git root toward the working directory.
It can also reuse a personal MJJ, OpenCode, or Claude rule file. Put reusable,
on-demand workflows in `.agents/skills/` or `.mjj/skills/`, then inspect skill
discovery with `mjj skills`. See [project and user
instructions](project-instructions.md) for precedence and opt-outs.

See [configuration](config.md) and [skills](skills.md) for precedence and scope.

## Diagnose a problem

These commands do not reveal secrets:

```bash
mjj auth
mjj config
mjj tools
mjj skills
mjj auth --probe
```

- If `mjj` is not found immediately after the Windows installer, open a new
  terminal so the updated user PATH is loaded.
- If a provider rejects compaction, Mojo Agent retries without it. Set
  `MJJ_COMPACT_THRESHOLD=0` only when you need to disable compaction explicitly.
- Optional Mojo acceleration may be unavailable on a machine; the Python
  implementation remains functional by design.
- Report reproducible defects through the
  [public issue tracker](https://github.com/lee101/mojojojo-agent/issues) with
  `mjj --version`, the operating system, and redacted diagnostic output.
