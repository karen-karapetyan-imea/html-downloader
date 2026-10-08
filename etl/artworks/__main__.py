"""`artwork-etl` CLI (`python -m artworks ...` from the etl/ folder is equivalent)."""

import sys

from artworks.spec import ARTWORKS
from etl_core import cli


def main(argv: list[str] | None = None) -> int:
    return cli.main(ARTWORKS, argv)


if __name__ == "__main__":
    sys.exit(main())
