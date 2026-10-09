"""Build the blast-radius note and the remediation report from saved artifacts.

This module does not call Grok. A successful sequence is red, then patch, then green.
Anything else is reported as incomplete or failed.
"""

from __future__ import annotations

import json
from pathlib import Path

from ledger.agent.investigate import RunPaths

STORE_PATH = "payments/store.py"


def write_report(paths: RunPaths) -> Path:
    report = paths.run_dir / "report.md"
    report.write_text(render_report(paths.run_dir), encoding="utf-8")
    print("phase: report")
    print(f"report: {report}")
    return report


def render_report(run_dir: Path) -> str:
    ingest = _read_json(run_dir / "ingest.json")
    root = _read_json(run_dir / "root_cause.json")
    sequence = _read_json(run_dir / "sequence.json")
    error = _read_json(run_dir / "error.json")
    succeeded = _succeeded(sequence, error, run_dir)
    steps = sequence.get("steps") if isinstance(sequence.get("steps"), list) else []
    diff = _read_text(run_dir / "store.diff")
    changed = _changed_files(steps, diff)

    lines = [
        f"# Remediation report: {ingest.get('id', run_dir.name)}",
        "",
        "## Status",
        "",
        _status_paragraph(succeeded, error, steps),
        "",
        "## Incident",
        "",
        f"- Service: {ingest.get('service', 'unknown')}",
        f"- Window: {ingest.get('time_window', 'unknown')}",
        f"- Summary: {ingest.get('summary', 'missing incident summary')}",
        f"- Symptom: {ingest.get('symptom', 'missing symptom')}",
        f"- Routes: {', '.join(ingest.get('routes') or []) or 'unknown'}",
        "",
        "## Root cause",
        "",
        f"- Hypothesis: {root.get('hypothesis', 'not recorded')}",
        f"- Confidence: {root.get('confidence', 'not recorded')}",
        "",
        "## Evidence",
        "",
    ]
    evidence = root.get("evidence") if isinstance(root.get("evidence"), list) else []
    if evidence:
        lines.extend(f"- {item}" for item in evidence)
    else:
        lines.append("- No evidence was recorded.")
    lines.extend(
        [
            "",
            "## Regression test before the fix",
            "",
            _pytest_section(steps, "red", run_dir / "pytest-red.txt"),
            "",
            "## Files changed",
            "",
            _files_section(changed, diff),
            "",
            "## Full suite after the fix",
            "",
            _pytest_section(steps, "green", run_dir / "pytest-green.txt"),
            "",
            "## Blast radius",
            "",
            _blast_radius(ingest, diff, changed),
            "",
            "## Rollback",
            "",
            _rollback(changed),
            "",
            "## Approval",
            "",
            "A person must approve this change before it ships. This report does not approve, merge, or deploy it.",
            "",
        ]
    )
    return "\n".join(lines)


def _succeeded(sequence: dict, error: dict, run_dir: Path) -> bool:
    if error:
        return False
    steps = sequence.get("steps")
    if not isinstance(steps, list) or len(steps) != 3:
        return False
    red, patch, green = steps
    red_log = _read_text(run_dir / "pytest-red.txt")
    green_log = _read_text(run_dir / "pytest-green.txt")
    required = (
        red_log.startswith("exit_code: 1"),
        green_log.startswith("exit_code: 0"),
        (run_dir / "store.diff").is_file(),
        (run_dir / "root_cause.json").is_file(),
    )
    return all(
        [
            isinstance(red, dict) and red.get("step") == "red" and red.get("exit_code") == 1,
            isinstance(patch, dict) and patch.get("step") == "patch" and patch.get("path") == STORE_PATH,
            isinstance(green, dict) and green.get("step") == "green" and green.get("exit_code") == 0,
            *required,
        ]
    )


def _status_paragraph(succeeded: bool, error: dict, steps: list) -> str:
    if succeeded:
        return "Remediation succeeded. The new test failed on the original code, the patch applied, and the full suite passed."
    if error.get("error"):
        return f"Remediation did not succeed. {error['error']}"
    if not steps:
        return "Remediation did not succeed. The run is incomplete: the red, patch, and green sequence is missing."
    return "Remediation did not succeed. The red, patch, and green sequence did not finish cleanly."


def _pytest_section(steps: list, name: str, path: Path) -> str:
    step = next((item for item in steps if isinstance(item, dict) and item.get("step") == name), None)
    if step is None:
        return "Not run."
    exit_code = step.get("exit_code")
    excerpt = _excerpt(path)
    return f"Exit code {exit_code}.\n\n```text\n{excerpt}\n```"


def _files_section(changed: list[str], diff: str) -> str:
    if not changed:
        return "No files were changed."
    summary = f"Changed files: {', '.join(changed)}."
    if not diff:
        return summary + "\n\nDiff summary: the patch artifact is missing."
    added, removed = _diff_counts(diff)
    body = diff if len(diff) <= 4000 else diff[:4000] + "\n... diff truncated ..."
    return f"{summary}\n\nDiff summary: {added} lines added, {removed} lines removed.\n\n```diff\n{body.rstrip()}\n```"


def _blast_radius(ingest: dict, diff: str, changed: list[str]) -> str:
    routes = ingest.get("routes") or ["POST /v1/payments/{id}/capture"]
    route_text = ", ".join(routes)
    if changed == [STORE_PATH] or (not changed and STORE_PATH in (diff or "")):
        refund_lines = [
            line
            for line in diff.splitlines()
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")) and "def refund" in line
        ]
        refund = "The diff edits refund." if refund_lines else "The refund function was not edited."
        return (
            f"Capture on {route_text} appends a ledger debit and moves customer funds. "
            f"The change is limited to {STORE_PATH}, which decides whether a retried capture writes another debit. "
            f"{refund} Authorize and fetch do not move money. "
            "A wrong idempotency replay can return the wrong status or drop a debit, so this is a payments-path change."
        )
    if not changed:
        return "No production file was changed, so the running payments behavior is the seeded code."
    return (
        f"Changed files: {', '.join(changed)}. Capture moves customer funds. "
        "Review every changed file before shipping."
    )


def _rollback(changed: list[str]) -> str:
    if STORE_PATH in changed:
        return (
            "Roll back by restoring the pre-patch payments/store.py. "
            "This prototype keeps the ledger in memory, so there is no migration to undo. "
            "Restarting the process drops idempotency records from the failed run."
        )
    return "No production patch was applied. Nothing needs to be rolled back."


def _changed_files(steps: list, diff: str) -> list[str]:
    files = []
    for step in steps:
        if isinstance(step, dict) and step.get("step") == "patch" and step.get("path"):
            files.append(str(step["path"]))
    if not files and diff.startswith("--- a/"):
        files.append(STORE_PATH)
    return files


def _diff_counts(diff: str) -> tuple[int, int]:
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _excerpt(path: Path, limit: int = 40) -> str:
    if not path.is_file():
        return "output was not saved"
    text = path.read_text(encoding="utf-8").strip()
    rows = text.splitlines()
    if len(rows) <= limit:
        return text
    return "\n".join(rows[:limit] + ["... truncated ..."])


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")
