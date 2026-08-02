# Contributing

Bug reports and focused pull requests are welcome. Token efficiency is a
correctness property here: tool output must stay bounded, optional native
components must degrade to Python, and performance claims need a reproducer.

```bash
uv sync
uv run pytest -q
uv build
```

Run `bench/search_bench.py` or the relevant case in `evals/run.py` when a
change affects search quality, latency, task success, or token use. Include
losses beside wins in the pull request and avoid committing credentials,
session rollouts, generated indexes, native libraries, or build artifacts.
