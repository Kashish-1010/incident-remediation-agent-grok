"""Ingest one incident and ask Grok for a root-cause JSON object.

This phase can only read files. It does not write a patch or run tests.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ledger.agent.grok_client import GrokClient, GrokResponse, parse_json_content
from ledger.agent.tools import Toolset
from ledger.env import api_key

ROOT = Path(__file__).resolve().parents[2]
MAX_TOOL_ROUNDS = 3
REQUIRED_FIELDS = ("hypothesis", "confidence", "files", "evidence")
CONFIDENCE = {"high", "medium", "low"}


@dataclass
class RunPaths:
    run_dir: Path
    workspace: Path
    incident_log: Path


def investigate(
    incident_path: Path,
    *,
    client: GrokClient | None = None,
    repo_root: Path = ROOT,
    runs_root: Path | None = None,
) -> int:
    if client is None and not api_key():
        print("XAI_API_KEY is not set. Copy .env.example to .env and set the variable there, or export it in the shell.")
        return 1

    print("phase: ingest")
    paths = ingest(incident_path, repo_root=repo_root, runs_root=runs_root or (repo_root / "runs"))
    grok = client or GrokClient(transcript_dir=paths.run_dir / "api")
    print("phase: investigate")
    try:
        result = run_investigation(grok, paths)
    except InvestigationError as exc:
        (paths.run_dir / "error.json").write_text(json.dumps({"error": str(exc)}, indent=2) + "\n", encoding="utf-8")
        print(f"investigation failed: {exc}")
        print(f"artifacts: {paths.run_dir}")
        return 1
    (paths.run_dir / "root_cause.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"hypothesis: {result['hypothesis']}")
    print(f"confidence: {result['confidence']}")
    print(f"files: {', '.join(result['files'])}")
    print(f"artifacts: {paths.run_dir}")
    return 0


def ingest(incident_path: Path, *, repo_root: Path, runs_root: Path) -> RunPaths:
    incident_file = incident_path if incident_path.is_absolute() else repo_root / incident_path
    incident = json.loads(incident_file.read_text(encoding="utf-8"))
    log_relative = Path(incident["log"])
    log_path = log_relative if log_relative.is_absolute() else repo_root / log_relative
    log_text = log_path.read_text(encoding="utf-8")
    run_dir = _run_dir(runs_root, str(incident["id"]))
    workspace = run_dir / "workspace"
    run_dir.mkdir(parents=True)
    for name in ("payments", "tests"):
        shutil.copytree(
            repo_root / name,
            workspace / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    record = {
        "id": incident["id"],
        "service": incident["service"],
        "summary": incident["summary"],
        "time_window": incident["time_window"],
        "routes": incident["routes"],
        "timeout_header": incident.get("timeout_header"),
        "symptom": incident["symptom"],
        "expected_fix": incident["expected_fix"],
        "log_path": str(log_path),
        "log": log_text,
    }
    (run_dir / "ingest.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return RunPaths(run_dir=run_dir, workspace=workspace, incident_log=log_path)


class InvestigationError(Exception):
    pass


def run_investigation(client: GrokClient, paths: RunPaths) -> dict:
    tools = Toolset(paths.workspace, paths.incident_log)
    ingest_record = json.loads((paths.run_dir / "ingest.json").read_text(encoding="utf-8"))
    store_text = (paths.workspace / "payments" / "store.py").read_text(encoding="utf-8")
    prompt = _prompt(ingest_record, store_text)
    trace: list[dict] = []
    response = client.create([{"role": "user", "content": prompt}], tools=tools.schemas())
    trace.append(_trace_item(response))
    rounds = 0
    while response.function_calls:
        if rounds >= MAX_TOOL_ROUNDS:
            _write_trace(paths, trace)
            raise InvestigationError(f"stopped after {MAX_TOOL_ROUNDS} tool rounds")
        outputs = []
        for call in response.function_calls:
            try:
                arguments = call.arguments_json()
            except json.JSONDecodeError:
                arguments = None
            result = tools.execute(call.name, arguments)
            trace.append({"tool": call.name, "call_id": call.call_id, "result": result[:4000]})
            outputs.append({"type": "function_call_output", "call_id": call.call_id, "output": result})
        rounds += 1
        response = client.create(outputs, tools=tools.schemas(), previous_response_id=response.id)
        trace.append(_trace_item(response))
    _write_trace(paths, trace)
    return _final_json(client, response)


def _final_json(client: GrokClient, response: GrokResponse) -> dict:
    try:
        return _validate(parse_json_content(response.text))
    except (json.JSONDecodeError, ValueError) as exc:
        retry = client.create(
            [
                {
                    "role": "user",
                    "content": (
                        "Your previous reply was not valid root-cause JSON. "
                        f"Validation error: {exc}. "
                        "Reply with only a JSON object with keys hypothesis, confidence, files, and evidence. "
                        f"Previous reply:\n{response.text}"
                    ),
                }
            ]
        )
        try:
            return _validate(parse_json_content(retry.text))
        except (json.JSONDecodeError, ValueError) as second:
            raise InvestigationError(f"root-cause JSON was invalid after one re-ask: {second}") from second


def _validate(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("root cause must be a JSON object")
    missing = [key for key in REQUIRED_FIELDS if key not in value]
    if missing:
        raise ValueError(f"missing keys: {', '.join(missing)}")
    if not isinstance(value["hypothesis"], str) or not value["hypothesis"].strip():
        raise ValueError("hypothesis must be a non-empty string")
    if value["confidence"] not in CONFIDENCE:
        raise ValueError("confidence must be high, medium, or low")
    if not isinstance(value["files"], list) or not all(isinstance(item, str) for item in value["files"]):
        raise ValueError("files must be a list of strings")
    if not isinstance(value["evidence"], list) or not all(isinstance(item, str) for item in value["evidence"]):
        raise ValueError("evidence must be a list of strings")
    return {
        "hypothesis": value["hypothesis"].strip(),
        "confidence": value["confidence"],
        "files": value["files"],
        "evidence": value["evidence"],
    }


def _prompt(incident: dict, store_text: str) -> str:
    return (
        "You are investigating a production payments incident. "
        "Read the incident and the code. Use read_file, search, and list_dir only if you need more context. "
        "Those tools cannot modify files or run commands. "
        "When you are done, reply with only a JSON object. Keys: "
        "hypothesis (string), confidence (high, medium, or low), files (array of workspace-relative paths), "
        "evidence (array of short citations from the log or code).\n\n"
        f"Incident:\n{json.dumps({key: incident[key] for key in incident if key != 'log'}, indent=2)}\n\n"
        f"Log:\n{incident['log']}\n\n"
        f"payments/store.py:\n{store_text}"
    )


def _trace_item(response: GrokResponse) -> dict:
    return {
        "response_id": response.id,
        "text": response.text,
        "function_calls": [{"name": call.name, "call_id": call.call_id, "arguments": call.arguments} for call in response.function_calls],
    }


def _write_trace(paths: RunPaths, trace: list[dict]) -> None:
    (paths.run_dir / "investigate.json").write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")


def _run_dir(runs_root: Path, incident_id: str) -> Path:
    candidate = runs_root / incident_id
    if not candidate.exists():
        return candidate
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return runs_root / f"{incident_id}-{stamp}"
