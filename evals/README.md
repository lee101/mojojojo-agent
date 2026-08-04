# Harness evals

These evals answer one question: did a harness change complete more real work
for fewer tokens? They run the agent in a temporary repository and finish with
an independent verifier.

```bash
uv run python evals/run.py port-mandelbrot-function \
  --artifacts build/evals/candidate
uv run python evals/run.py port-radiokit-project \
  --artifacts build/evals/held-out
```

The function case is a fast construction check. The project case exercises a
multi-module API, CLI behavior, randomized numeric agreement, two compiled
kernels, and native timing. Run both: a prompt or tool tuned to the small case
must still generalize to the project.

## Case contract

Each directory under `cases/` contains:

| path | role |
| --- | --- |
| `repo/` | clean starting repository copied for every run |
| `prompt.txt` | task shown to the agent |
| `setup.sh` | optional environment check; it must not reveal dependency source |
| `check.sh` | independent acceptance test, including compile and timing proof |

The runner excludes user skills and host project instructions. It exposes
mojosub through `PYTHONPATH`, outside the task repository, so dependency files
do not inflate repository search. Use `--without-skills` for a same-fixture
workflow A/B; the manifest records the variant, model, effort, and case hashes.

## Read the result in layers

`--artifacts DIR` writes a compact `summary.json` plus one directory per case:

- `manifest.json`: outcome, hashes, latency, token use, cache reads, and tools;
- `trace.jsonl`: bounded step records for action-level diagnosis;
- verifier stdout and stderr, each capped at 1 MiB;
- the failed workspace, or every workspace with `--keep-workspaces`.

Generated artifacts stay under ignored `build/`; do not commit one stochastic
sample as a product claim. Compare case hashes and config first, then report
pass rate, total input plus output tokens, tool-result tokens, tool calls, and
latency. A failed run has no `tokens_per_pass`.

## Change loop

Use a propose-evaluate-accept loop:

1. Tie the proposal to a failure visible in a trace.
2. Make the smallest harness change and state its permanent token cost.
3. Run the same construction case before and after.
4. Run a larger or held-out case.
5. Keep correctness gates fixed. Publish regressions and reject candidates
   that save no tokens or weaken observability.

This follows the filesystem artifacts, layered observability, held-out checks,
and verifier-grounded iteration described in
[Lilian Weng's harness engineering notes](https://lilianweng.github.io/posts/2026-07-04-harness/).
