#!/usr/bin/env bash
# Profile the repository Mojo ABI with the dotfiles mojo toolkit.
# Requires pixi env built and optional ~/code/dotfiles/tools/mojo (or sibling).
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
toolkit=${MOJO_TOOLKIT:-}
if [[ -z $toolkit ]]; then
  for candidate in \
    "$root/../dotfiles/tools/mojo" \
    "$HOME/code/dotfiles/tools/mojo" \
    /nvme0n1-disk/code/dotfiles/tools/mojo
  do
    if [[ -x $candidate/bin/mojolint ]]; then
      toolkit=$candidate
      break
    fi
  done
fi
if [[ -z ${toolkit:-} ]]; then
  echo "mojo toolkit not found; set MOJO_TOOLKIT=/path/to/dotfiles/tools/mojo" >&2
  exit 2
fi

export PATH="$root/.pixi/envs/default/bin:$toolkit/bin:$PATH"
export MODULAR_HOME=${MODULAR_HOME:-$root/.pixi/envs/default/share/max}
src=$root/mjj/search/embed.mojo
lib=$root/build/libmjj_search.so

echo "## mojo-profile"
echo
echo "toolkit: $toolkit"
echo "source:  $src"
echo

pixi run --manifest-path "$root/pixi.toml" mojo-check
echo
mojolint "$src" || true
echo
mojoffi "$src" --check "$lib" || true
echo
for sym in mjj_search_i8_mmap mjj_tokenize mjj_static_embed mjj_static_embed_batch mjj_bm25_accumulate mjj_quantize_i8; do
  echo "### mojoasm $sym"
  mojoasm "$src" "$sym" || true
  echo
done
echo "### GPU note"
mojogpu --check 2>/dev/null | head -20 || true
echo
echo "Search/quantize/BM25 are memory-bound or scatter; GPU stays off the agent hot path."
