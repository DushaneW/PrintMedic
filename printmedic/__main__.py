"""Entry point: `python -m printmedic` (GUI) or `python -m printmedic --cli ...`."""

from __future__ import annotations

import sys


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--cli":
        from printmedic.cli import main as cli_main
        return cli_main(args[1:])
    from printmedic.ui.app import run
    return run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
