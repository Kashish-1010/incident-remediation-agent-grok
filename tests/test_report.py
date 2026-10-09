"""The report is code-owned and only claims success after red, patch, and green."""

import json
from pathlib import Path

from ledger.agent.investigate import RunPaths
from ledger.agent.report import render_report, write_report


def _run(tmp_path: Path) -> Path:
    run = tmp_path / "INC-1042"
    run.mkdir()
    (run / "ingest.json").write_text(
        json.dumps(
            {
                "id": "INC-1042",
                "service": "payments",
                "summary": "Customers are debited twice.",
                "time_window": "2026-04-02T14:10:00Z/2026-04-02T14:12:00Z",
                "routes": ["POST /v1/payments/{id}/capture"],
                "symptom": "Two ledger debits.",
            }
        ),
        encoding="utf-8",
    )
    (run / "root_cause.json").write_text(
        json.dumps(
            {
                "hypothesis": "The timeout path returns before the idempotency record is stored.",
                "confidence": "high",
                "files": ["payments/store.py"],
                "evidence": ["two ledger_debit events"],
            }
        ),
        encoding="utf-8",
    )
    return run


def _sequence(red: int, green: int | None) -> dict:
    steps = [{"step": "red", "exit_code": red, "artifact": "pytest-red.txt"}]
    if green is not None:
        steps.append({"step": "patch", "path": "payments/store.py", "artifact": "store.diff"})
        steps.append({"step": "green", "exit_code": green, "artifact": "pytest-green.txt"})
    return {"steps": steps}


def test_successful_run_requires_approval_and_states_success(tmp_path: Path) -> None:
    run = _run(tmp_path)
    (run / "sequence.json").write_text(json.dumps(_sequence(1, 0)), encoding="utf-8")
    (run / "pytest-red.txt").write_text("exit_code: 1\nFAILED tests/test_inc_1042.py\n", encoding="utf-8")
    (run / "pytest-green.txt").write_text("exit_code: 0\n6 passed\n", encoding="utf-8")
    (run / "store.diff").write_text("--- a/payments/store.py\n+++ b/payments/store.py\n@@\n-return stored, 200\n+return stored['body'], stored['status_code']\n", encoding="utf-8")
    report = write_report(RunPaths(run, run / "workspace", tmp_path / "log.jsonl"))
    text = report.read_text(encoding="utf-8")
    assert "Remediation succeeded" in text
    assert "Customers are debited twice." in text
    assert "confidence" in text.lower() or "Confidence: high" in text
    assert "two ledger_debit events" in text
    assert "FAILED tests/test_inc_1042.py" in text
    assert "6 passed" in text
    assert "payments/store.py" in text
    assert "moves customer funds" in text
    assert "The refund function was not edited." in text
    assert "A person must approve this change before it ships." in text
    assert "does not approve, merge, or deploy" in text


def test_failed_green_suite_is_not_called_a_success(tmp_path: Path) -> None:
    run = _run(tmp_path)
    (run / "sequence.json").write_text(json.dumps(_sequence(1, 1)), encoding="utf-8")
    (run / "pytest-red.txt").write_text("exit_code: 1\nFAILED tests/test_inc_1042.py\n", encoding="utf-8")
    (run / "pytest-green.txt").write_text("exit_code: 1\nFAILED tests/test_payments.py\n", encoding="utf-8")
    (run / "store.diff").write_text("--- a/payments/store.py\n+++ b/payments/store.py\n", encoding="utf-8")
    (run / "error.json").write_text(json.dumps({"error": "full pytest suite failed after the patch"}), encoding="utf-8")
    text = render_report(run)
    assert "Remediation succeeded" not in text
    assert "did not succeed" in text
    assert "full pytest suite failed after the patch" in text
    assert "A person must approve this change before it ships." in text


def test_incomplete_run_does_not_claim_success(tmp_path: Path) -> None:
    run = _run(tmp_path)
    text = render_report(run)
    assert "Remediation succeeded" not in text
    assert "incomplete" in text
    assert "Not run." in text
    assert "No production file was changed" in text
