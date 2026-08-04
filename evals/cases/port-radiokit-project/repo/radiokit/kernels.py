import math


def rms(samples: list[float]) -> float:
    if len(samples) == 0:
        return 0.0
    total = 0.0
    for index in range(len(samples)):
        total += samples[index] * samples[index]
    return math.sqrt(total / float(len(samples)))


def goertzel_power(
    samples: list[float], sample_rate: float, target_hz: float
) -> float:
    """Power at one target frequency using the Goertzel recurrence."""
    if len(samples) == 0:
        return 0.0
    omega = 2.0 * math.pi * target_hz / sample_rate
    coefficient = 2.0 * math.cos(omega)
    previous = 0.0
    before_previous = 0.0
    for index in range(len(samples)):
        current = samples[index] + coefficient * previous - before_previous
        before_previous = previous
        previous = current
    return (
        before_previous * before_previous
        + previous * previous
        - coefficient * previous * before_previous
    ) / float(len(samples))


def autocorrelation_peak(
    samples: list[float], min_lag: int, max_lag: int
) -> int:
    """Return the lag with the largest unnormalized autocorrelation."""
    if len(samples) < 2 or min_lag >= len(samples):
        return 0
    upper = min(max_lag, len(samples) - 1)
    best_lag = min_lag
    best_score = -1.0e300
    for lag in range(min_lag, upper + 1):
        score = 0.0
        for index in range(lag, len(samples)):
            score += samples[index] * samples[index - lag]
        if score > best_score:
            best_score = score
            best_lag = lag
    return best_lag
