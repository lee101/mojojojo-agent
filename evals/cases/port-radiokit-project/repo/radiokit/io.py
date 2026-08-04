from pathlib import Path


def read_samples(path: str | Path) -> list[float]:
    text = Path(path).read_text(encoding="utf-8")
    values = text.replace(",", " ").split()
    return [float(value) for value in values]
