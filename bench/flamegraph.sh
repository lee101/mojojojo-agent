#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
corpus=${1:-$root}
output=${2:-$root/build/profiles/search-flamegraph.svg}
mkdir -p "$(dirname "$output")"
temporary_dir=$(mktemp -d)
temporary=$temporary_dir/search-flamegraph.svg
trap 'rm -f "$temporary"; rmdir "$temporary_dir"' EXIT

set +e
uvx py-spy==0.4.2 record \
  --rate 250 \
  --format flamegraph \
  --output "$temporary" \
  -- python -m mjj.kernels.bench \
    --profile-root "$corpus" \
    --search-repeats 40
status=$?
set -e

# py-spy 0.4.2 can report ECHILD after the profilee exits even though it wrote
# a complete SVG. Treat the artifact, not that late wait error, as success.
if [[ -s "$temporary" ]] \
  && head -c 5 "$temporary" | grep -q '<?xml' \
  && tail -c 32 "$temporary" | grep -q '</svg>'; then
  mv "$temporary" "$output"
  echo "flame graph: $output"
  exit 0
fi
exit "$status"
