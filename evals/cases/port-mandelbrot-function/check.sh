set -e
python -m pytest -q
test -f bench.py
python - <<'PY'
import json
import inspect
import time

import kernel


def reference(width, height, max_iter):
    total = 0
    for row in range(height):
        cy = -1.2 + 2.4 * float(row) / float(height)
        for column in range(width):
            cx = -2.0 + 3.0 * float(column) / float(width)
            x = 0.0
            y = 0.0
            iteration = 0
            while x * x + y * y <= 4.0 and iteration < max_iter:
                next_x = x * x - y * y + cx
                y = 2.0 * x * y + cy
                x = next_x
                iteration += 1
            total += iteration * (row + 1) + column
    return total


public = kernel.mandelbrot_checksum
assert str(inspect.signature(public)) == "(width: int, height: int, max_iter: int) -> int"
assert public(width=8, height=6, max_iter=12) == 1321
for args in ((8, 6, 12), (23, 17, 35), (71, 43, 60)):
    assert public(*args) == reference(*args)
accelerated = [
    value
    for value in vars(kernel).values()
    if hasattr(value, "compiled")
    and hasattr(value, "fn")
    and "mandelbrot_checksum" in value.__name__
]
assert len(accelerated) == 1, "expected one observable mojosub kernel"
candidate = accelerated[0]
assert candidate.wait(90), "native compile did not finish"
for _ in range(4):
    assert public(71, 43, 60) == reference(71, 43, 60)
assert candidate.compiled, candidate.stats.last_error
assert candidate.stats.verify_failures == 0
assert candidate.stats.mojo_calls > 0

args = (160, 120, 100)
expected = reference(*args)
python_samples = []
mojo_samples = []
for _ in range(3):
    started = time.perf_counter()
    assert candidate.fn(*args) == expected
    python_samples.append(time.perf_counter() - started)
    started = time.perf_counter()
    assert candidate(*args) == expected
    mojo_samples.append(time.perf_counter() - started)
python_seconds = min(python_samples)
mojo_seconds = min(mojo_samples)
assert mojo_seconds < python_seconds, (python_seconds, mojo_seconds)
print(json.dumps({
    "compiled": True,
    "python_seconds": python_seconds,
    "mojo_seconds": mojo_seconds,
    "speedup": python_seconds / mojo_seconds,
}))
PY
python bench.py | python -c '
import json, sys
lines = [line for line in sys.stdin.read().splitlines() if line.strip()]
report = json.loads(lines[-1])
assert report["compiled"] is True
assert report["python_seconds"] > 0 and report["mojo_seconds"] > 0
assert report["speedup"] > 1.0
'
