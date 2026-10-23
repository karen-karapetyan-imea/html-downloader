"""`lot-etl` CLI (`python -m lots ...` from the etl/ folder is equivalent)."""

import sys

from etl_core import cli
from lots.spec import LOTS


def main(argv: list[str] | None = None) -> int:
    return cli.main(LOTS, argv)


if __name__ == "__main__":
    sys.exit(main())
