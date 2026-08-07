# Repository search

`search` returns addresses and a little evidence, not file dumps. Its default
`auto` mode chooses the cheapest trustworthy tier. A bounded exact result is
returned directly without paying for an embedding scan. Broad or non-literal
queries fuse three rankings into one deduplicated list:

1. a literal or regex scan, using `rg` when installed and a bounded Python
   scan otherwise;
2. BM25-style scoring over identifier, signature, and path tokens;
3. an exact cosine-like scan over 256-dimensional int8 vectors.

Each hit contains `path:line` and a short signature or matching line. Exact
literal hits stop there. Non-literal hits may add one nearby evidence line.
Multiple hits in one file share one path header instead of repeating it. The
complete result is passed through `ctx.ledger.clip("search", ...)` exactly
once.

If normal indexed search finds nothing, one last-resort pass considers ignored
text and files between 2 and 32 MiB. It still excludes dependency/build trees
unless the caller explicitly scopes one, refuses known binary suffixes and
NUL-containing data, caps naming comparisons at 16 KiB per line, and caps all
rendered evidence lines. This slower tier never runs after a normal hit.

```text
mjj/search/index.py:
  257: def search(
  467: def format_hits(self, hits: Sequence[SearchHit]) -> str:
```

The requested `limit` is a ceiling, not a quota. The fused score distribution
decides how much to return: a sharp confidence cliff after the first one to
three hits stops the list there, while a flatter distribution keeps spending
the allowance on broader or naming-variant queries.

When a broad result has another page, its final line provides a numeric
`cursor`. Repeating the same search with that cursor retrieves the next ranked
page without making the first response pay for every match:

```json
{"query": "shared_result", "limit": 8, "cursor": 8}
```

The tool builds `.mjj/index` on its first call. The identical path is exposed
as a disk-search CLI, and an index can be prepared explicitly:

```bash
mjj search refreshAccessToken mjj --limit 8 --stats
mjj search 'class .*Server' --regex --json
mjj index
```

Like `rg`, `mjj search` exits `0` for matches, `1` for no matches, and `2` for
invalid input or an indexing error. `mjj-search` is a standalone alias useful
in scripts that do not need the rest of the agent CLI.

Tool calls accept:

```json
{"query": "refreshAccessToken", "mode": "auto", "path": "mjj", "limit": 8}
```

- `auto` returns a decisive literal set immediately, otherwise fuses the
  available signals.
- `literal` uses only the `rg`/Python literal path. Set `"regex": true` for a
  regular expression.
- `semantic` uses only the static-vector ranking. It is useful for naming
  variants and approximate identifiers when no literal survives.
- `path` limits results to one workspace-relative file or directory.
- `limit` is between 1 and 20; the default ceiling is 8.
- `cursor` is the continuation offset from a previous broad result.

## What “semantic” means here

There is deliberately no embedding model. A chunk and a query are represented
by hashed identifier tokens plus 3- and 4-character n-grams, projected into
256 dimensions, L2-normalised, and symmetrically quantised to int8.

This is **lexical-semantic, not model-semantic**. It catches forms such as
`refresh_access_token` versus `refreshAccessToken`, and often tolerates a
small typo because character n-grams overlap. It does not understand
paraphrase: “renew the login credential” is not expected to find
`refresh_access_token` unless the repository supplies lexical overlap.
Collisions from feature hashing are also possible.

The representation is deterministic across machines and runs. Indexing never
uses a network service, downloads weights, or sends source code elsewhere.

### Why a similarity threshold is not enough

Hashed static vectors do not discriminate well enough to score a nonsense
query below a real one. Measured against `/nvme0n1-disk/code/mojojojo`:

| query | top similarity |
| --- | ---: |
| `insufficient credits error` | 0.466 |
| `worker_bootstrap` | 0.380 |
| `zzzqqq_nonexistent` (nonsense) | 0.315 |
| `how does billing round fractional milliseconds` | 0.273 |

The nonsense query outscores a genuine conceptual one, so **no cutoff on
similarity can separate them**. Without a second signal the tool returns its
nearest arbitrary chunk for every query, which is worse than returning
nothing: it costs tokens and it teaches the model to distrust the tool.

So a lexical and semantic candidate must be *grounded* — it has to share at
least one distinctive word with the query, where distinctive excludes language
keywords and English glue (`def`, `class`, `return`, `the`, `how`, …). One
keyword in the query must not make every function in the tree a match. Literal
matches skip the check; a substring hit is evidence on its own.

Result: `zzzqqq_nonexistent`, `kubernetes ingress controller`, and
`def rolling_sharpe` in a repository without one all return `no matches`,
while `workerBootstrap` still finds `worker_bootstrap`. Pinned by
`tests/test_search_grounding.py`.

## Chunks and incremental updates

The index is chunked at functions, classes, common language declarations, and
Markdown headings. Long regions are split at 120 lines. A chunk stores:

- its relative path and inclusive line span;
- one shortened signature line;
- compact term frequencies;
- one 256-byte int8 vector and one float32 scale/norm factor.

Candidate files come from `git ls-files -co --exclude-standard` when Git is
available. The stdlib walker fallback applies `.gitignore` rules itself.
`.git`, `.mjj`, `node_modules`, virtual environments, common build directories,
known binary suffixes, NUL-containing content, symlinks, and files larger than
2 MiB do not produce chunks.

The incremental key is `(relative path, mtime_ns, size)`. Unchanged files are
not opened or embedded again; their chunk metadata and vector rows are reused.
If the whole manifest is unchanged, the index file is not rewritten. As with
the tool itself, incremental refresh keeps the live mmap and lazily built BM25
postings when nothing changed. As with any stat-keyed cache, a program that
changes content while preserving both mtime and size must force a rebuild:

```bash
mjj index --force
```

## Persistence and native scan

`.mjj/index` is a versioned, mmap-able file:

```text
little-endian header
compact JSON: root, file identities, chunk metadata and term frequencies
padding to a 64-byte boundary
count × 256 contiguous int8 vector bytes
count contiguous float32 factors
```

Writes go to a sibling temporary file, are flushed, and replace the old index
atomically. Corrupt, truncated, wrong-root, wrong-version, and wrong-dimension
files are rebuilt.

`mjj/search/vectors.py` loads the native backend when tokenize, embed, or a
vector matrix needs it. It looks for the library named by `MJJ_MOJO_EMBED_LIB`,
MJJ's own `build/libmjj_search.so`, the adjacent `../mojo-embed` checkout, a
packaged copy, then the system library path. Its C ABI tokenises identifiers,
projects static embeddings, and scans the vector and factor regions directly
from the mmap with no NumPy or row copies. If the library is missing or its
buffers cannot be bound, the same pure-Python paths run. Search continues; only
latency changes. `MJJ_ACCEL=0` disables the tokenize/embed exports while leaving
the optional int8 scan available when the library is present.

`mjj/search/embed.mojo` supplies `mjj_search_i8_mmap`, `mjj_tokenize`, and
`mjj_static_embed` for a repository-specific build. It is never compiled in the
agent loop. Embedding L2 normalisation stays in CPython so persisted index
factors remain bit-compatible with the Python fallback.

## Measured benchmark

Run:

```bash
python bench/search_bench.py --corpus /path/to/corpus
python bench/allocation_bench.py --corpus /path/to/corpus
python -m mjj.kernels.bench
pixi run mojo-check
```

The search benchmark creates its index in a temporary directory. Fresh build and
unchanged incremental times are single measured runs; the incremental run reuses
the live index, as repeated tool calls do. Each query is warmed, then reports the
median of seven runs. MJJ time includes literal search, BM25, int8 scan, fusion,
source-context reads, and formatting. The `rg` comparison is
`rg -n -F QUERY .` from the corpus root. The naming-variant row also compares
the ranked evidence with reading its complete top-result file. Returned tokens
use the harness's dependency-free four-characters-per-token estimate.

### Mojo tokenize / embed microbench (2026-08-07)

Measured on this checkout with `build/libmjj_search.so` from `pixi run mojo-check`
(`MJJ_ACCEL=1`). Kernel rows are medians from `python -m mjj.kernels.bench`;
allocation rows use `bench/allocation_bench.py`'s 7409-character sample.

| candidate | Python µs | Mojo µs | decision |
| --- | ---: | ---: | --- |
| identifier tokenizer | 1223.3 | 894.7 | keep |
| static embedding 256d | 5674.5 | 492.1 | keep |

Cold index of this repository (207 files / 2083 chunks, median of 3 builds):

| path | median build |
| --- | ---: |
| Python fallback (`MJJ_ACCEL=0`) | 917.695 ms |
| Mojo tokenize + embed | 603.173 ms |

### Hybrid search vs `rg` (2026-08-07, this repository)

```text
Corpus: `/vfast/data/code/mojojojo-agent`
Index: 207 files, 2083 chunks, mojo-embed backend; query time is median of 7 runs.

| Case | MJJ time | rg time | MJJ tokens | rg tokens |
|---|---:|---:|---:|---:|
| Fresh index build | 641.28 ms | — | — | — |
| Unchanged incremental index | 9.89 ms | — | — | — |
| `errInsufficientCredits` | 6.20 ms | 6.19 ms | 33 | 35 |
| `workerBootstrap` | 6.10 ms | 5.88 ms | 158 | 171 |
| `mojojail` | 6.23 ms | 5.81 ms | 146 | 203 |
| `billed_ms` | 6.24 ms | 5.23 ms | 27 | 29 |

| Naming-variant case | MJJ time | MJJ tokens | rg tokens | Top result | Top-file read tokens |
|---|---:|---:|---:|---|---:|
| `worker_bootstrap` | 6.04 ms | 132 | 199 | `docs/search.md` | 2920 |
```

### Prior audit host (2026-08-05, `/nvme0n1-disk/code/mojojojo`)

Measured on 2026-08-05 with the repository-local Mojo `mjj_search_i8_mmap`
backend only (tokenize/embed still Python):

```text
Corpus: `/nvme0n1-disk/code/mojojojo`
Index: 271 files, 3383 chunks, mojo-embed backend; query time is median of 5 runs.

| Case | MJJ time | rg time | MJJ tokens | rg tokens |
|---|---:|---:|---:|---:|
| Fresh index build | 3556.69 ms | — | — | — |
| Unchanged incremental index | 20.67 ms | — | — | — |
| `errInsufficientCredits` | 11.08 ms | 10.42 ms | 56 | 64 |
| `workerBootstrap` | 10.83 ms | 10.91 ms | 39 | 42 |
| `mojojail` | 10.56 ms | 9.31 ms | 132 | 850 |
| `billed_ms` | 10.85 ms | 9.75 ms | 131 | 498 |

| Naming-variant case | MJJ time | MJJ tokens | rg tokens | Top result | Top-file read tokens |
|---|---:|---:|---:|---|---:|
| `worker_bootstrap` | 12.58 ms | 243 | 0 | `vector_scaling.go` | 1791 |
```

Decisive literal queries skip BM25/vector work. Broad queries retain the hybrid
ranking while withholding 718 and 367 estimated tokens in the two broad rows of
the 2026-08-05 audit. The naming variant there has no literal `rg` hit, still
ranks `vector_scaling.go` first, and returns 1548 fewer estimated tokens than
reading that file.

`bench/retrieval_bench.py` adds an adversarial 120-match corpus, ignored and
2 MiB files, a binary decoy, cursored pages, and schema accounting. On the same
machine, two 56-token pages withheld 1301 of 1413 raw-match tokens; an excluded
2 MiB tail match took 38.575 ms. The always-present `check` tool schema costs
122 estimated cached-context tokens. These are harness measurements, not model
recall or artistic-quality claims, and should be regenerated on other machines.

### Allocation and flame profiles

`bench/allocation_bench.py` measures the tokenizer and static embedding with a
fixed synthetic workload. With `--corpus`, it also reports a content SHA-256,
file/chunk counts, cold-index median, traced heap peak, and retained allocation
sites. `bench/flamegraph.sh` records a 250 Hz CPU flame graph through pinned
py-spy; the Memray commands in [DEV.md](../DEV.md) record Python allocation
events and an allocation flame graph.

One controlled run on 2026-08-04 used CPython 3.13.13, Linux 6.8, CPU 0 of an
Intel Xeon E5-2697 v4, and the same 179-file/1,786-chunk corpus for both
revisions. Its corpus SHA-256 was
`c04d039feeddccb75f76c4c5bfab6a9476ddeae6da194a310eeca14b9d4e322c`.
Medians are seven inner timing samples and three cold index builds:

| measurement | `ebecf73` | optimized | change |
| --- | ---: | ---: | ---: |
| tokenizer | 2438.668 us | 1158.170 us | -52.5% |
| static embedding | 9614.326 us | 7241.230 us | -24.7% |
| cold index build | 2492.845 ms | 1783.851 ms | -28.4% |
| cold index traced heap peak | 15,226,642 B | 15,022,523 B | -1.3% |

Memray 1.19.3 with `--trace-python-allocators` over ten identical tokenizer
and embedding iterations recorded 993,125 allocation events / 130.867 MB for
`ebecf73`, versus 681,786 / 96.618 MB after the change: 31.3% fewer events and
26.2% fewer allocated bytes. Peak memory for that small direct-call workload
was effectively flat (1.357 MB versus 1.366 MB). A full index produced by both
implementations was byte-identical, SHA-256
`47df8db11ed1ca67911c066dd91417510ddfda7a73beb5800c3f491f3293bf30`.
These are reproducible measurements on one machine, not universal latency
claims; compare only reports with the same corpus digest and execution setup.
