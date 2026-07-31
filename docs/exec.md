# Python execution

The `py` tool runs generated Python and returns its stdout, stderr, exit code,
wall time, selected path, execution tier (`interpreted`, `native`, or `mixed`),
and remote credit cost. `timeout` is a hard wall deadline in seconds.

Placement is conservative:

- Small pure snippets run in the current interpreter with captured, bounded
  output and an interrupting deadline.
- Pure numeric functions containing loops are decorated with mojosub's tiered
  JIT. The first call runs in CPython while compilation starts on a background
  thread; the agent loop never waits for `mojo build`. Generated functions are
  passed their original `source=`, and `MODULAR_HOME` is derived from the
  resolved `mojo` binary.
- Code that imports an unknown module, touches the network, writes files, or
  uses dynamic execution goes to `/usr/local/bin/mojojail` or the loopback
  worker on port 4342. The jail receives an interpreter and the program, never
  a compiler or a writable shared compile cache.
- `where: "remote"` posts to `https://mojojojo.app.nz/v1/run` using the
  `mj_live_` key in `MJJ_MOJOJOJO_KEY`, and reports the response's charged
  credits.

`MJJ_EXEC=inproc|accel|sandbox|remote` forces placement; the per-call `where`
field takes precedence. Optional packages imply a sandbox unless placement is
forced. If mojosub, Mojo, the jail, the worker, or the network is missing, the
code still runs in-process and the result names the requested path and fallback
reason.

Program output is bounded before it reaches the ledger, then clipped once more
through `ctx.ledger.clip("py", ...)`. Stderr is rendered last so the ledger's
head-and-tail clipping preserves a traceback.

## Measured latency

| path | cold | warm | hot |
| --- | ---: | ---: | ---: |
| inproc | 17.476 ms | 22.044 ms | 27.335 ms |
| accelerated | 31.116 ms interpreted | 35.114 ms mixed | **2.668 ms native** |
| sandbox (`mojojail`) | 214.971 ms | 217.743 ms | 242.681 ms |
| sandbox (loopback worker) | 477.468 ms | 420.983 ms | 429.198 ms |
| remote | unavailable | unavailable | unavailable |

Measured on this checkout on 2026-07-31 with CPython 3.12 and Mojo
`1.0.0b3.dev2026072406`. Every row used the same 200,000-iteration integer
kernel. Cold is the first run, warm is the second, and hot is the median of the
final six of nine runs (six of seven for the worker). The accelerated cold run
queued a real 4,479.549 ms compile on a background thread but returned before
it; the benchmark joined that thread *between* cold and warm solely so the next
measurement could exercise the cache. Warm ran the Python/native race, and hot
used its persisted native verdict. The inproc and jail hot medians regressed in
this sample; those losses are reported as measured.

No `MJJ_MOJOJOJO_KEY` was present, so the remote row was not run and no credits
were spent.

The measurement was produced with this stdlib-only driver from the repository
root (the timestamp-derived constant makes the accelerator source cold):

```python
import statistics, threading, time
from pathlib import Path
from mjj.exec.local import run_inproc, run_accelerated, _run_jail
from mjj.exec.remote import run_worker

marker = time.time_ns() % 1_000_000_000
source = f"""def kernel(n: int) -> int:
    out = {marker}
    for i in range(n):
        out += i * 3
    return out
print(kernel(200000))
"""
cwd = Path.cwd()

def measure(call, count=9):
    rows = [call() for _ in range(count)]
    assert all(row.ok for row in rows)
    return rows[0].wall_ms, rows[1].wall_ms, statistics.median(
        row.wall_ms for row in rows[3:]
    )

print("inproc", measure(lambda: run_inproc(source, timeout=30, cwd=cwd)))
print("jail", measure(lambda: _run_jail(source, timeout=30)))

before = set(threading.enumerate())
cold = run_accelerated(source, timeout=30, cwd=cwd)
for thread in set(threading.enumerate()) - before:
    thread.join(30)  # benchmark setup only; the executor never joins a build
rest = [run_accelerated(source, timeout=30, cwd=cwd) for _ in range(8)]
print("accelerated", cold.wall_ms, rest[0].wall_ms,
      statistics.median(row.wall_ms for row in rest[2:]))

print("worker", measure(lambda: run_worker(source, timeout=30), count=7))
```
