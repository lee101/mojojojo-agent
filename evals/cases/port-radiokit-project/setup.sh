set -e
test -n "$MJJ_EVAL_MOJOSUB_ROOT"
test -d "$MJJ_EVAL_MOJOSUB_ROOT/mojosub"
python -c 'import mojosub'
