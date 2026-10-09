"""Investigation orchestration and read-only tool boundaries. No live API calls."""

import json
from pathlib import Path

import pytest

from ledger.agent.grok_client import FunctionCall, GrokAPIError, GrokResponse
from ledger.agent.investigate import InvestigationError, ingest, investigate, run_investigation
from ledger.agent.tools import Toolset


class ScriptedClient:
    def __init__(self, responses: list[GrokResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, input_items, tools=None, previous_response_id=None):
        self.calls.append(
            {
                "input": input_items,
                "tools": tools,
                "previous_response_id": previous_response_id,
            }
        )
        return self._responses.pop(0)


def _response(text: str = "", calls: list[FunctionCall] | None = None, response_id: str = "resp") -> GrokResponse:
    return GrokResponse(id=response_id, output=[], raw={}, text=text, function_calls=calls or [])


def _repo(tmp_path: Path) -> Path:
    payments = tmp_path / "payments"
    payments.mkdir()
    (payments / "store.py").write_text("def capture():\n    return 'bug'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_payments.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    log = tmp_path / "incidents" / "logs"
    log.mkdir(parents=True)
    (log / "INC-1042.jsonl").write_text('{"event":"ledger_debit"}\n{"event":"ledger_debit"}\n', encoding="utf-8")
    incident = {
        "id": "INC-1042",
        "service": "payments",
        "summary": "double debit",
        "time_window": "t",
        "routes": ["POST /v1/payments/{id}/capture"],
        "symptom": "two debits",
        "expected_fix": "one debit",
        "log": "incidents/logs/INC-1042.jsonl",
    }
    (tmp_path / "incidents" / "INC-1042.json").write_text(json.dumps(incident), encoding="utf-8")
    secret = tmp_path / ".env"
    secret.write_text("XAI_API_KEY=super-secret\n", encoding="utf-8")
    return tmp_path


def test_investigation_runs_tools_then_saves_root_cause(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    client = ScriptedClient(
        [
            _response(
                calls=[FunctionCall(name="read_file", call_id="call_1", arguments='{"path": "payments/store.py"}')],
                response_id="resp_1",
            ),
            _response(
                text=json.dumps(
                    {
                        "hypothesis": "Timeout returns before the idempotency record is stored.",
                        "confidence": "high",
                        "files": ["payments/store.py"],
                        "evidence": ["two ledger_debit events"],
                    }
                ),
                response_id="resp_2",
            ),
        ]
    )
    code = investigate(repo / "incidents" / "INC-1042.json", client=client, repo_root=repo, runs_root=repo / "runs")
    assert code == 0
    assert client.calls[1]["previous_response_id"] == "resp_1"
    assert client.calls[1]["input"][0]["type"] == "function_call_output"
    assert "def capture" in client.calls[1]["input"][0]["output"]
    root = json.loads((repo / "runs" / "INC-1042" / "root_cause.json").read_text(encoding="utf-8"))
    assert root["confidence"] == "high"
    assert (repo / "runs" / "INC-1042" / "ingest.json").is_file()
    assert (repo / "runs" / "INC-1042" / "workspace" / "payments" / "store.py").is_file()
    store = (repo / "payments" / "store.py").read_text(encoding="utf-8")
    assert store == (repo / "runs" / "INC-1042" / "workspace" / "payments" / "store.py").read_text(encoding="utf-8")


def test_invalid_json_is_reasked_once(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    client = ScriptedClient(
        [
            _response(text="not json", response_id="resp_1"),
            _response(
                text='```json\n{"hypothesis": "retry debits again", "confidence": "medium", "files": [], "evidence": []}\n```',
                response_id="resp_2",
            ),
        ]
    )
    code = investigate(repo / "incidents" / "INC-1042.json", client=client, repo_root=repo, runs_root=repo / "runs")
    assert code == 0
    assert client.calls[1]["tools"] is None
    assert client.calls[1]["previous_response_id"] is None
    assert "not valid root-cause JSON" in client.calls[1]["input"][0]["content"]


def test_fourth_tool_round_stops(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    call = FunctionCall(name="list_dir", call_id="call_x", arguments='{"path": "."}')
    client = ScriptedClient([_response(calls=[call], response_id=f"resp_{n}") for n in range(4)])
    code = investigate(repo / "incidents" / "INC-1042.json", client=client, repo_root=repo, runs_root=repo / "runs")
    assert code == 1
    assert len(client.calls) == 4
    error = json.loads((repo / "runs" / "INC-1042" / "error.json").read_text(encoding="utf-8"))
    assert "3 tool rounds" in error["error"]


def test_missing_key_does_not_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr("ledger.agent.investigate.api_key", lambda: "")
    repo = _repo(tmp_path)
    code = investigate(repo / "incidents" / "INC-1042.json", repo_root=repo, runs_root=repo / "runs")
    assert code == 1
    assert not (repo / "runs").exists()


def test_tool_rejects_paths_outside_the_workspace(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    workspace = repo / "workspace"
    (workspace / "payments").mkdir(parents=True)
    (workspace / "payments" / "store.py").write_text("secret-line = 1\n", encoding="utf-8")
    tools = Toolset(workspace, repo / "incidents" / "logs" / "INC-1042.jsonl")

    assert "secret-line" in tools.execute("read_file", {"path": "payments/store.py"})
    assert "ledger_debit" in tools.execute("read_file", {"path": str(repo / "incidents" / "logs" / "INC-1042.jsonl")})

    outside = tools.execute("read_file", {"path": "../.env"})
    assert json.loads(outside)["error"].startswith("path is outside the workspace")
    absolute = tools.execute("read_file", {"path": str(repo / ".env")})
    assert "outside the workspace" in json.loads(absolute)["error"]
    assert "super-secret" not in outside
    assert "super-secret" not in absolute

    listed = tools.execute("list_dir", {"path": ".."})
    assert "outside the workspace" in json.loads(listed)["error"]
    searched = tools.execute("search", {"query": "XAI_API_KEY", "path": ".."})
    assert "outside the workspace" in json.loads(searched)["error"]


def test_unknown_tool_does_not_change_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "payments"
    target.mkdir()
    file = target / "store.py"
    file.write_text("original\n", encoding="utf-8")
    tools = Toolset(workspace, tmp_path / "log.jsonl")
    before = file.read_text(encoding="utf-8")
    result = tools.execute("apply_patch", {"path": "payments/store.py", "text": "changed"})
    assert json.loads(result)["error"] == "unknown tool: apply_patch"
    assert file.read_text(encoding="utf-8") == before
    command = tools.execute("run_pytest", {})
    assert "unknown tool" in command


def test_incident_log_cannot_escape_incidents(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    secret = repo / ".env"
    incident = json.loads((repo / "incidents" / "INC-1042.json").read_text(encoding="utf-8"))
    incident["log"] = "../.env"
    escaped = repo / "incidents" / "escaped.json"
    escaped.write_text(json.dumps(incident), encoding="utf-8")
    with pytest.raises(InvestigationError, match="incidents/"):
        ingest(escaped, repo_root=repo, runs_root=repo / "runs")
    assert secret.read_text(encoding="utf-8") == "XAI_API_KEY=super-secret\n"
    assert not (repo / "runs").exists()


def test_api_failure_writes_a_report_that_does_not_claim_success(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    class FailingClient:
        def create(self, *_args, **_kwargs):
            raise GrokAPIError("Grok request failed with HTTP 503")

    code = investigate(repo / "incidents" / "INC-1042.json", client=FailingClient(), repo_root=repo, runs_root=repo / "runs")
    assert code == 1
    report = (repo / "runs" / "INC-1042" / "report.md").read_text(encoding="utf-8")
    assert "Remediation succeeded" not in report
    assert "HTTP 503" in report


def test_run_investigation_error_on_second_bad_json(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    from ledger.agent.investigate import ingest

    paths = ingest(repo / "incidents" / "INC-1042.json", repo_root=repo, runs_root=repo / "runs")
    client = ScriptedClient([_response(text="nope"), _response(text="still nope")])
    with pytest.raises(InvestigationError, match="re-ask"):
        run_investigation(client, paths)
