#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
lock=${TMPDIR:-/tmp}/mjj-agent-bench.lock
exec 9>"$lock"
if command -v flock >/dev/null 2>&1; then
  flock 9
fi

cd "$root"
uv run python bench/startup_bench.py
uv run python bench/search_bench.py
uv run python bench/retrieval_bench.py
uv run python bench/allocation_bench.py
(
  cd visualbench
  npm run bench
)
