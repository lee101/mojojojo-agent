set -e
python -m pytest -q
test -f bench.py
python - <<'PY'
from array import array
import inspect
import json
import math
import time

from radiokit import kernels
from radiokit.analysis import analyze


def ref_rms(samples):
    return math.sqrt(sum(value * value for value in samples) / len(samples)) if samples else 0.0


def ref_goertzel(samples, sample_rate, target_hz):
    if not samples:
        return 0.0
    omega = 2.0 * math.pi * target_hz / sample_rate
    coefficient = 2.0 * math.cos(omega)
    previous = 0.0
    before_previous = 0.0
    for value in samples:
        current = value + coefficient * previous - before_previous
        before_previous = previous
        previous = current
    return (before_previous ** 2 + previous ** 2 - coefficient * previous * before_previous) / len(samples)


def ref_peak(samples, min_lag, max_lag):
    if len(samples) < 2 or min_lag >= len(samples):
        return 0
    upper = min(max_lag, len(samples) - 1)
    return max(
        range(min_lag, upper + 1),
        key=lambda lag: sum(samples[i] * samples[i - lag] for i in range(lag, len(samples))),
    )


samples = array("d", (
    math.sin(2.0 * math.pi * index / 23.0)
    + 0.2 * math.sin(2.0 * math.pi * index / 7.0)
    for index in range(4096)
))
assert str(inspect.signature(kernels.goertzel_power)) == (
    "(samples: list[float], sample_rate: float, target_hz: float) -> float"
)
assert str(inspect.signature(kernels.autocorrelation_peak)) == (
    "(samples: list[float], min_lag: int, max_lag: int) -> int"
)
assert math.isclose(kernels.rms(samples), ref_rms(samples), rel_tol=1e-9)
assert math.isclose(
    kernels.goertzel_power(samples=samples, sample_rate=230.0, target_hz=10.0),
    ref_goertzel(samples, 230.0, 10.0),
    rel_tol=1e-8,
)
assert kernels.autocorrelation_peak(samples=samples, min_lag=10, max_lag=80) == ref_peak(samples, 10, 80)

accelerated = [
    value
    for value in vars(kernels).values()
    if hasattr(value, "compiled") and hasattr(value, "fn") and hasattr(value, "stats")
]
assert len(accelerated) >= 2, "fewer than two mojosub kernels were exposed"
for function in accelerated:
    assert function.wait(90), f"{function.__name__} compile did not finish"
for _ in range(4):
    kernels.goertzel_power(samples, 230.0, 10.0)
    kernels.autocorrelation_peak(samples, 10, 80)
compiled = [function for function in accelerated if function.compiled]
assert len(compiled) >= 2, [function.stats.last_error for function in accelerated]
assert all(function.stats.verify_failures == 0 for function in accelerated)
assert sum(function.stats.mojo_calls for function in compiled) > 0

report = analyze(list(samples[:256]), 230.0, 10.0)
assert report.samples == 256 and report.dominant_lag > 0

large = array("d", (
    math.sin(2.0 * math.pi * index / 37.0)
    + 0.1 * math.cos(2.0 * math.pi * index / 11.0)
    for index in range(9000)
))
candidate = next(
    function for function in compiled if "autocorrelation_peak" in function.__name__
)
expected = ref_peak(large, 10, 240)
python_samples = []
mojo_samples = []
for _ in range(3):
    started = time.perf_counter()
    assert candidate.fn(large, 10, 240) == expected
    python_samples.append(time.perf_counter() - started)
    started = time.perf_counter()
    assert candidate(large, 10, 240) == expected
    mojo_samples.append(time.perf_counter() - started)
python_seconds = min(python_samples)
mojo_seconds = min(mojo_samples)
assert mojo_seconds < python_seconds, (python_seconds, mojo_seconds)
print(json.dumps({
    "compiled_kernels": 2,
    "python_seconds": python_seconds,
    "mojo_seconds": mojo_seconds,
    "speedup": python_seconds / mojo_seconds,
}))
PY
python bench.py | python -c '
import json, sys
lines = [line for line in sys.stdin.read().splitlines() if line.strip()]
report = json.loads(lines[-1])
assert report["compiled_kernels"] >= 2
assert report["python_seconds"] > 0 and report["mojo_seconds"] > 0
assert report["speedup"] > 1.0
'
