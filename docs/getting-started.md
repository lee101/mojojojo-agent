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

The installers select your platform, download the latest release, verify its
published SHA-256 checksum, and install in a user-owned directory. They do not
require a system Python. You can instead install the Python package with
`uv tool install mojojojo-agent` or download an archive from
[GitHub Releases](https://github.com/lee101/mojojojo-agent/releases).

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

Type `/` to discover commands. On an empty composer, left/right changes the
reasoning level and Shift+Up/Down changes the model. Alt+Enter inserts a newline.
Type `@` to attach a fuzzy-matched repository file, `!command` to run a shell
command and retain its output as model context, or `!!command` to run it only
locally. Useful first commands are `/status`, `/settings`, `/permissions`,
`/review`, `/model`, `/effort`, `/auth`, and `/help`.

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
mjj exec --provider openrouter --model openrouter/auto "review this repository"
mjj exec --image screenshot.png "match the current page to this reference"
```

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

## Project instructions and skills

Mojo Agent loads bounded `AGENTS.md` instructions from the Git root toward the
working directory. Put reusable, on-demand workflows in `.agents/skills/` or
`.mjj/skills/`, then inspect discovery with `mjj skills`.

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
