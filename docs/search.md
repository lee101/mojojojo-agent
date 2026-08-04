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

`mjj/search/vectors.py` looks for the library named by
`MJJ_MOJO_EMBED_LIB`, the adjacent checkout
`../mojo-embed/build/libmojo_embed.so`, a packaged copy, then the system
library path. Its `embed_search_i8` C ABI scans the vector and factor regions
directly from the mmap with no NumPy or row copies. If the library is missing
or its buffers cannot be bound, the same exact dot-product ranking runs using
stdlib Python arrays. Search continues; only latency changes.

`mjj/search/embed.mojo` supplies an optional `mjj_search_i8_mmap` wrapper for a
repository-specific build. It is never compiled in the agent loop, and the
prebuilt upstream ABI already has every entry point normal installs need.

## Measured benchmark

Run:

```bash
python bench/search_bench.py
```

The benchmark creates its index in a temporary directory and uses
`/nvme0n1-disk/code/mojojojo` as the real corpus. Fresh build and unchanged
incremental times are single measured runs; the incremental run reuses the
live index, as repeated tool calls do. Each query is warmed, then reports the
median of seven runs. MJJ time includes literal search, BM25, int8 scan, fusion,
source-context reads, and formatting. The `rg` comparison is
`rg -n -F QUERY .` from the corpus root. The naming-variant row also compares
the ranked evidence with reading its complete top-result file. Returned tokens
use the harness's dependency-free four-characters-per-token estimate.

Measured on 2026-08-04 with CPython 3.12.3, Linux 6.8, an Intel Xeon E5-2697
v4, and the dependency-free Python vector fallback:

```text
Corpus: `/nvme0n1-disk/code/mojojojo`
Index: 270 files, 3377 chunks, python backend; query time is median of 7 runs.

| Case | MJJ time | rg time | MJJ tokens | rg tokens |
|---|---:|---:|---:|---:|
| Fresh index build | 4756.03 ms | — | — | — |
| Unchanged incremental index | 22.09 ms | — | — | — |
| `errInsufficientCredits` | 9.97 ms | 9.35 ms | 56 | 64 |
| `workerBootstrap` | 9.91 ms | 9.37 ms | 39 | 42 |
| `mojojail` | 124.86 ms | 10.11 ms | 132 | 850 |
| `billed_ms` | 112.70 ms | 10.58 ms | 131 | 498 |

| Naming-variant case | MJJ time | MJJ tokens | rg tokens | Top result | Top-file read tokens |
|---|---:|---:|---:|---|---:|
| `worker_bootstrap` | 127.11 ms | 243 | 0 | `vector_scaling.go` | 1685 |
```

Decisive literal queries now skip BM25/vector work and land within about 1 ms
of raw `rg` on this run. Broad queries retain the hybrid ranking and withhold
718 and 367 estimated tokens respectively, but are roughly 12× slower than
`rg` on the Python vector fallback. The naming variant has no literal `rg` hit,
still ranks `vector_scaling.go` first, and returns 1442 fewer estimated tokens
than reading that file.

`bench/retrieval_bench.py` adds an adversarial 120-match corpus, ignored and
2 MiB files, a binary decoy, cursored pages, and schema accounting. On the same
machine, two 56-token pages withheld 1301 of 1413 raw-match tokens; an excluded
2 MiB tail match took 38.575 ms. The always-present `check` tool schema costs
122 estimated cached-context tokens. These are harness measurements, not model
recall or artistic-quality claims, and should be regenerated on other machines.
