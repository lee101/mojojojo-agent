import math

from radiokit.kernels import autocorrelation_peak, goertzel_power, rms


def test_rms_and_empty_input() -> None:
    assert rms([]) == 0.0
    assert math.isclose(rms([3.0, 4.0]), math.sqrt(12.5))


def test_tone_power_prefers_present_frequency() -> None:
    samples = [math.sin(2.0 * math.pi * 8.0 * i / 64.0) for i in range(256)]
    assert goertzel_power(samples, 64.0, 8.0) > 50.0
    assert goertzel_power(samples, 64.0, 13.0) < 1.0


def test_autocorrelation_finds_period() -> None:
    samples = [math.sin(2.0 * math.pi * i / 16.0) for i in range(256)]
    assert autocorrelation_peak(samples, 12, 20) == 16
