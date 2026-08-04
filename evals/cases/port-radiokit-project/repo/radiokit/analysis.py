from dataclasses import asdict, dataclass

from .kernels import autocorrelation_peak, goertzel_power, rms


@dataclass(frozen=True)
class SignalReport:
    samples: int
    sample_rate: float
    rms: float
    target_hz: float
    target_power: float
    dominant_lag: int

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)


def analyze(
    samples: list[float], sample_rate: float, target_hz: float
) -> SignalReport:
    max_lag = min(len(samples) - 1, max(2, int(sample_rate)))
    return SignalReport(
        samples=len(samples),
        sample_rate=sample_rate,
        rms=rms(samples),
        target_hz=target_hz,
        target_power=goertzel_power(samples, sample_rate, target_hz),
        dominant_lag=autocorrelation_peak(samples, 2, max_lag),
    )
