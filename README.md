# mojojojo-agent

A coding agent harness that spends tokens like they cost money.

Same job as Codex or Claude Code: read a repository, change it, run the tests,
report back. The difference is where the work happens. Searching a codebase,
executing code, and matching diffs are **computation**, not reasoning — so this
harness does them natively (Mojo, via [mojosub][] and [mojo-embed][]) and sends
the model an answer instead of a pile of context to read.

```bash
uv tool install mojojojo-agent
mjj auth --probe                     # reuses your existing ChatGPT max-plan sign-in
mjj exec "make the failing test in tests/test_router.py pass"
```

Status: **early**. The loop, the credential path and the model client work
end to end; tools, search and the sandboxed executor are landing now.

## Why it is cheaper

| what a harness does | usual approach | here |
| --- | --- | --- |
| find code | dump files into context | ranked `path:line` hits from a native int8 index |
| run code | shell out to CPython | subset-compiled to Mojo, cached by content hash, ~1.2 us/call |
| edit code | rewrite the file | `apply_patch` envelope, per-file `+n/-n` summary |
| tool output | truncate at N bytes | one ledger, head+tail kept, exactly what was dropped is stated |
| reasoning | re-derived each turn | reasoning items echoed back verbatim so the cache hits |

Every number this project publishes is reproducible from `bench/`. Losses get
published next to wins.

## Credentials

The primary path is the ChatGPT max-plan sign-in you already have — the one
`codex` wrote to `~/.codexinfinity/auth.json`. `mjj` reads it, refreshes the
OAuth tokens against `auth.openai.com`, and writes the rotated tokens back so
every other tool on the machine keeps working. Set `MJJ_OPENAI_API_KEY` to use
a plain API key instead.

```bash
mjj auth            # what credential is in play, what plan, when it expires
mjj auth --probe    # one real round trip
```

## Layout

See [AGENTS.md](AGENTS.md) — it is the working contract for this repo, and the
map of which module owns what.

## Licence

MIT.

[mojosub]: https://github.com/lee101/mojosub
[mojo-embed]: https://github.com/lee101/mojo-embed
