"""Command line entrypoint. The final remediation report is a later milestone."""

import argparse
import json
from pathlib import Path

from ledger.env import key_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    reproduce_parser = sub.add_parser("reproduce", help="Run the seeded double-debit capture and write the incident log")
    reproduce_parser.add_argument("--log", type=Path, default=None)
    sub.add_parser("check-key", help="Show whether XAI_API_KEY is loaded, without printing it or calling the API")
    investigate_parser = sub.add_parser("investigate", help="Ingest an incident, find the root cause, prove a failing test, and patch")
    investigate_parser.add_argument("incident", type=Path)
    remediate_parser = sub.add_parser("remediate", help="Continue a saved run from root_cause.json through the red-green patch")
    remediate_parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    if args.command == "investigate":
        from ledger.agent.investigate import investigate

        return investigate(args.incident, continue_to_remediation=True)
    if args.command == "remediate":
        from ledger.agent.grok_client import GrokClient
        from ledger.agent.investigate import RunPaths
        from ledger.agent.remediate import remediate
        from ledger.env import api_key

        if not api_key():
            print("XAI_API_KEY is not set. Copy .env.example to .env and set the variable there, or export it in the shell.")
            return 1
        run_dir = args.run_dir
        ingest = json.loads((run_dir / "ingest.json").read_text(encoding="utf-8"))
        paths = RunPaths(run_dir=run_dir, workspace=run_dir / "workspace", incident_log=Path(ingest["log_path"]))
        return remediate(paths, GrokClient(transcript_dir=run_dir / "api"))
    if args.command == "reproduce":
        from ledger.reproduce import DEFAULT_LOG, reproduce

        return reproduce(args.log or DEFAULT_LOG)
    if args.command == "check-key":
        status = key_status()
        print(status)
        return 0 if "is loaded" in status else 1
    return 2
