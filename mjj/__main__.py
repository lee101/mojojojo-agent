"""Allow ``python -m mjj`` and provide a stable freezer entry point."""

from mjj.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
