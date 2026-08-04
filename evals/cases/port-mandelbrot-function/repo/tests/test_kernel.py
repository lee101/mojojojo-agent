from kernel import mandelbrot_checksum


def test_checksum_is_deterministic() -> None:
    assert mandelbrot_checksum(8, 6, 12) == 1321
    assert mandelbrot_checksum(8, 6, 12) == 1321


def test_empty_grid() -> None:
    assert mandelbrot_checksum(0, 10, 20) == 0
