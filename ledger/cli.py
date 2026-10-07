"""Command line entrypoint. Patching and remediation are later milestones."""

import argparse
from pathlib import Path

from ledger.env import key_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    reproduce_parser = sub.add_parser("reproduce", help="Run the seeded double-debit capture and write the incident log")
    reproduce_parser.add_argument("--log", type=Path, default=None)
    sub.add_parser("check-key", help="Show whether XAI_API_KEY is loaded, without printing it or calling the API")
    investigate_parser = sub.add_parser("investigate", help="Ingest an incident and ask Grok for a root cause")
    investigate_parser.add_argument("incident", type=Path)
    args = parser.parse_args(argv)
    if args.command == "investigate":
        from ledger.agent.investigate import investigate

        return investigate(args.incident)
    if args.command == "reproduce":
        from ledger.reproduce import DEFAULT_LOG, reproduce

        return reproduce(args.log or DEFAULT_LOG)
    if args.command == "check-key":
        status = key_status()
        print(status)
        return 0 if "is loaded" in status else 1
    return 2
