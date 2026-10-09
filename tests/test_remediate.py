"""Red, patch, green remediation. The Grok client and pytest are scripted."""

import json
from pathlib import Path

from ledger.agent.grok_client import GrokAPIError, GrokResponse
from ledger.agent.investigate import RunPaths
from ledger.agent.pytest_runner import run_pytest
from ledger.agent.remediate import remediate


class ScriptedClient:
    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.calls: list[dict] = []
        self._call_index = 0

    def create(self, input_items, tools=None, previous_response_id=None):
        self.calls.append({"input": input_items, "tools": tools, "previous_response_id": previous_response_id})
        text = self._texts.pop(0)
        return GrokResponse(id="resp", output=[], raw={}, text=text, function_calls=[])


def _paths(tmp_path: Path, store: str = "def capture():\n    debit_twice()\n") -> RunPaths:
    run = tmp_path / "INC-1042"
    workspace = run / "workspace"
    (workspace / "payments").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "payments" / "store.py").write_text(store, encoding="utf-8")
    (run / "root_cause.json").write_text(
        json.dumps(
            {
                "hypothesis": "timeout returns before the idempotency record is stored",
                "confidence": "high",
                "files": ["payments/store.py"],
                "evidence": ["two ledger debits"],
            }
        ),
        encoding="utf-8",
    )
    (run / "ingest.json").write_text(
        json.dumps({"expected_fix": "one debit on retry", "log_path": str(tmp_path / "log.jsonl")}),
        encoding="utf-8",
    )
    return RunPaths(run_dir=run, workspace=workspace, incident_log=tmp_path / "log.jsonl")


def _file(path: str, content: str) -> str:
    return json.dumps({"path": path, "content": content})


def test_red_patch_green_sequence(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    original = (paths.workspace / "payments" / "store.py").read_text(encoding="utf-8")
    pytest_calls: list[list[str] | None] = []

    def runner(workspace: Path, arguments: list[str] | None = None, timeout: int = 60):
        pytest_calls.append(arguments)
        if arguments == ["tests/test_inc_1042.py"]:
            return 1, "FAILED tests/test_inc_1042.py::test_retry - assert 2 == 1\n"
        text = (workspace / "payments" / "store.py").read_text(encoding="utf-8")
        assert "fixed" in text
        return 0, "2 passed\n"

    client = ScriptedClient(
        [
            _file("tests/test_inc_1042.py", "def test_retry():\n    assert False\n"),
            _file("payments/store.py", "def capture():\n    return 'fixed'\n"),
        ]
    )
    code = remediate(paths, client, pytest_runner=runner)
    assert code == 0
    assert len(client.calls) == 2
    assert "X-Simulate-Gateway-Timeout" in client.calls[0]["input"][0]["content"]
    assert "Failing pytest output" in client.calls[1]["input"][0]["content"]
    assert pytest_calls == [["tests/test_inc_1042.py"], None]
    sequence = json.loads((paths.run_dir / "sequence.json").read_text(encoding="utf-8"))
    assert [step["step"] for step in sequence["steps"]] == ["red", "patch", "green"]
    assert sequence["steps"][0]["exit_code"] == 1
    assert sequence["steps"][2]["exit_code"] == 0
    assert "FAILED" in (paths.run_dir / "pytest-red.txt").read_text(encoding="utf-8")
    assert "exit_code: 0" in (paths.run_dir / "pytest-green.txt").read_text(encoding="utf-8")
    assert original != (paths.workspace / "payments" / "store.py").read_text(encoding="utf-8")
    assert (paths.run_dir / "store.diff").is_file()


def test_passing_regression_test_stops_before_the_patch(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    original = (paths.workspace / "payments" / "store.py").read_text(encoding="utf-8")
    client = ScriptedClient([_file("tests/test_inc_1042.py", "def test_retry():\n    assert True\n")])

    def runner(_workspace: Path, arguments: list[str] | None = None, timeout: int = 60):
        return 0, "1 passed\n"

    code = remediate(paths, client, pytest_runner=runner)
    assert code == 1
    assert len(client.calls) == 1
    assert (paths.workspace / "payments" / "store.py").read_text(encoding="utf-8") == original
    assert not (paths.run_dir / "store.diff").exists()
    error = json.loads((paths.run_dir / "error.json").read_text(encoding="utf-8"))
    assert "passed before the patch" in error["error"]


def test_suite_failure_after_patch_is_not_success(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    client = ScriptedClient(
        [
            _file("tests/test_inc_1042.py", "def test_retry():\n    assert False\n"),
            _file("payments/store.py", "def capture():\n    return 'fixed'\n"),
        ]
    )

    def runner(_workspace: Path, arguments: list[str] | None = None, timeout: int = 60):
        if arguments:
            return 1, "FAILED tests/test_inc_1042.py::test_retry\n"
        return 1, "FAILED tests/test_payments.py::test_happy\n"

    code = remediate(paths, client, pytest_runner=runner)
    assert code == 1
    sequence = json.loads((paths.run_dir / "sequence.json").read_text(encoding="utf-8"))
    assert sequence["steps"][2]["exit_code"] == 1
    error = json.loads((paths.run_dir / "error.json").read_text(encoding="utf-8"))
    assert "did not succeed" in error["error"]
    assert "FAILED tests/test_payments.py" in (paths.run_dir / "pytest-green.txt").read_text(encoding="utf-8")


def test_wrong_patch_path_is_rejected(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    original = (paths.workspace / "payments" / "store.py").read_text(encoding="utf-8")
    client = ScriptedClient(
        [
            _file("tests/test_inc_1042.py", "def test_retry():\n    assert False\n"),
            _file("payments/app.py", "hacked = True\n"),
            _file("payments/app.py", "hacked = True\n"),
        ]
    )

    def runner(_workspace: Path, arguments: list[str] | None = None, timeout: int = 60):
        return 1, "FAILED tests/test_inc_1042.py::test_retry\n"

    code = remediate(paths, client, pytest_runner=runner)
    assert code == 1
    assert (paths.workspace / "payments" / "store.py").read_text(encoding="utf-8") == original
    assert not (paths.workspace / "payments" / "app.py").exists()


def test_oversized_patch_is_not_written(tmp_path: Path) -> None:
    paths = _paths(tmp_path, store="line\n" * 5)
    original = (paths.workspace / "payments" / "store.py").read_text(encoding="utf-8")
    huge = "changed\n" * 200
    client = ScriptedClient(
        [
            _file("tests/test_inc_1042.py", "def test_retry():\n    assert False\n"),
            _file("payments/store.py", huge),
        ]
    )

    def runner(_workspace: Path, arguments: list[str] | None = None, timeout: int = 60):
        return 1, "FAILED tests/test_inc_1042.py::test_retry\n"

    code = remediate(paths, client, pytest_runner=runner)
    assert code == 1
    assert (paths.workspace / "payments" / "store.py").read_text(encoding="utf-8") == original
    assert "150" in json.loads((paths.run_dir / "error.json").read_text(encoding="utf-8"))["error"]


def test_api_error_during_patch_does_not_claim_success(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    class FailAfterTest(ScriptedClient):
        def create(self, input_items, tools=None, previous_response_id=None):
            if self.calls:
                raise GrokAPIError("Grok request timed out after 2 retries")
            return super().create(input_items, tools, previous_response_id)

    client = FailAfterTest([_file("tests/test_inc_1042.py", "def test_retry():\n    assert False\n")])

    def runner(_workspace: Path, arguments: list[str] | None = None, timeout: int = 60):
        return 1, "FAILED tests/test_inc_1042.py::test_retry\n"

    code = remediate(paths, client, pytest_runner=runner)
    assert code == 1
    report = (paths.run_dir / "report.md").read_text(encoding="utf-8")
    assert "Remediation succeeded" not in report
    assert "timed out" in report


def test_pytest_timeout_is_reported(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_sleep.py").write_text("import time\n\ndef test_sleep():\n    time.sleep(5)\n", encoding="utf-8")
    code, output = run_pytest(tmp_path, ["tests/test_sleep.py"], timeout=1)
    assert code == 124
    assert "timed out" in output
