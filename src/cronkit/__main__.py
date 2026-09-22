"""Allow ``python -m cronkit``."""

from cronkit.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
