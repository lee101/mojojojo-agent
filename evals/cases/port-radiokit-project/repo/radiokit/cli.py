import argparse
import json

from .analysis import analyze
from .io import read_samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--rate", type=float, required=True)
    parser.add_argument("--tone", type=float, required=True)
    args = parser.parse_args(argv)
    report = analyze(read_samples(args.path), args.rate, args.tone)
    print(json.dumps(report.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
