# Repository search

`search` returns addresses and a little evidence, not file dumps. Its default
`auto` mode fuses three rankings into one deduplicated list:

1. a literal or regex scan, using `rg` when installed and a bounded Python
   scan otherwise;
2. BM25-style scoring over identifier, signature, and path tokens;
3. an exact cosine-like scan over 256-dimensional int8 vectors.

Each hit contains `path:line`, a short signature or matching line, and no more
than two nearby source lines. The complete result is passed through
`ctx.ledger.clip("search", ...)` exactly once.

```text
mjj/search/index.py:497: def build_index(
  498 |     root: str | Path,
```

The tool builds `.mjj/index` on its first call. An index can also be prepared
explicitly:

```bash
python -m mjj.search.index .
```

Tool calls accept:

```json
{"query": "refreshAccessToken", "mode": "auto", "path": "mjj", "limit": 8}
```

- `auto` uses and fuses all three signals.
- `literal` uses only the `rg`/Python literal path. Set `"regex": true` for a
  regular expression.
- `semantic` uses only the static-vector ranking. It is useful for naming
  variants and approximate identifiers when no literal survives.
- `path` limits results to one workspace-relative file or directory.
- `limit` is between 1 and 20; the default is 8.

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
python -m mjj.search.index . --force
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
`rg -n -F QUERY .` from the corpus root. Returned tokens use the harness's
dependency-free four-characters-per-token estimate.

Measured on 2026-07-31 with CPython 3.12.3, Linux 6.8, an Intel Xeon E5-2697
v4, and the adjacent prebuilt mojo-embed library:

```text
Corpus: `/nvme0n1-disk/code/mojojojo`
Index: 97 files, 1812 chunks, mojo-embed backend; query time is median of 7 runs.

| Case | MJJ time | rg time | MJJ tokens | rg tokens |
|---|---:|---:|---:|---:|
| Fresh index build | 2972.08 ms | — | — | — |
| Unchanged incremental index | 15.69 ms | — | — | — |
| `errInsufficientCredits` | 31.70 ms | 36.27 ms | 287 | 20 |
| `workerBootstrap` | 39.90 ms | 40.49 ms | 293 | 42 |
| `mojojail` | 36.86 ms | 30.55 ms | 301 | 850 |
| `billed_ms` | 36.78 ms | 38.67 ms | 334 | 545 |
```

The losses matter. Raw `rg` returned fewer tokens for the two rare identifiers
because it had only one or two exact lines to show; ranked context is overhead
in that case. MJJ was also slower on the `mojojail` query in this run. For the
broader `mojojail` and `billed_ms` queries, bounded ranking withheld 549 and
211 estimated tokens respectively. This benchmark does not claim model-level
semantic recall, and the exact timings should be regenerated on another
machine rather than copied as universal numbers.
