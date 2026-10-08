"""`artist-etl` CLI (`python -m artists ...` from the etl/ folder is equivalent)."""

import sys

from artists.spec import ARTISTS
from etl_core import cli


def main(argv: list[str] | None = None) -> int:
    return cli.main(ARTISTS, argv)


if __name__ == "__main__":
    sys.exit(main())
