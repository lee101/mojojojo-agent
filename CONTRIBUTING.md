# Contributing

Mojo Agent is MIT-licensed and developed in the open. Bug reports, focused
documentation fixes, provider compatibility work, and measured improvements to
token use are welcome through the
[public repository](https://github.com/lee101/mojojojo-agent).

## Development setup

Python 3.10 or newer and [uv](https://docs.astral.sh/uv/) are required:

```bash
git clone https://github.com/lee101/mojojojo-agent.git
cd mojojojo-agent
uv sync
# Optional multi-language tree-sitter validation:
uv sync --extra syntax
uv run pytest -q
uv run mjj --version
```

Mojo, mojosub, mojo-embed, credentials, and network access are optional for the
offline test suite. Missing acceleration must degrade to a tested fallback.

## Working agreements

- Keep tool output bounded through `mjj/ledger.py`.
- Preserve Responses items verbatim between turns, especially encrypted
  reasoning and function-call output.
- Do not weaken hosted workspace or execution boundaries to make local behavior
  more convenient.
- Publish benchmark numbers only with a reproducer under `bench/` or `evals/`.
- Keep optional provider and native dependencies from becoming startup
  requirements.
- Never commit credentials, auth caches, session transcripts, or `.env` files.

Repository-specific implementation notes live in [AGENTS.md](AGENTS.md).

## Tests

Run the complete offline suite before submitting a change:

```bash
uv run pytest -q
```

For documentation changes, the suite verifies that repository-local Markdown
links resolve. For package changes, also verify the distributable:

```bash
uv build
uv run --isolated --with ./dist/mojojojo_agent-*.whl mjj --version
```

If a change affects benchmarks or hosted behavior, include the focused command
and result in the pull-request description.

## Pull requests

Keep a pull request focused on one coherent outcome. Explain what changed, why,
the user-visible effect, and the checks run. Link an issue when one exists.
Generated release archives and local benchmark outputs should not be committed.
