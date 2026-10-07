"""Command line entrypoint. The investigate agent is a later milestone."""

import argparse
from pathlib import Path

from ledger.reproduce import DEFAULT_LOG, reproduce


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    reproduce_parser = sub.add_parser("reproduce", help="Run the seeded double-debit capture and write the incident log")
    reproduce_parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = parser.parse_args(argv)
    if args.command == "reproduce":
        return reproduce(args.log)
    return 2
