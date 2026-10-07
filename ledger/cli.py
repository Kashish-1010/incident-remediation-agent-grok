"""Command line entrypoint. The investigate agent is a later milestone."""

import argparse
from pathlib import Path

from ledger.env import key_status
from ledger.reproduce import DEFAULT_LOG, reproduce


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    reproduce_parser = sub.add_parser("reproduce", help="Run the seeded double-debit capture and write the incident log")
    reproduce_parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    sub.add_parser("check-key", help="Show whether XAI_API_KEY is loaded, without printing it or calling the API")
    args = parser.parse_args(argv)
    if args.command == "reproduce":
        return reproduce(args.log)
    if args.command == "check-key":
        print(key_status())
        return 0 if "is loaded" in key_status() else 1
    return 2
