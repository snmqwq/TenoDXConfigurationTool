"""Console entry point for the standalone full DFU workflow build."""

from __future__ import annotations

import sys

from tenodx_config.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["dfu", *sys.argv[1:]]))
