"""Allow ``python -m schedulesync``."""

from schedulesync.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
