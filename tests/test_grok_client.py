"""Grok client tests. HTTP is mocked; these tests do not call api.x.ai."""

import json
from pathlib import Path

import httpx
import pytest

from ledger.agent.grok_client import (
    API_URL,
    GrokAPIError,
    GrokAuthError,
    GrokClient,
    parse_json_content,
)
from ledger.env import key_status, load_dotenv

SECRET = "xai-test-secret-value"


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_parses_message_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == API_URL
        assert request.headers["Authorization"] == f"Bearer {SECRET}"
        body = json.loads(request.content)
        assert body["model"] == "grok-4.7"
        assert body["input"][0]["role"] == "user"
        return httpx.Response(
            200,
            json={
                "id": "resp_1",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "{\"ok\": true}"}]}
                ],
            },
        )

    client = GrokClient(api_key=SECRET, transport=_transport(handler), sleep=lambda _s: None)
    response = client.create([{"role": "user", "content": "hi"}])
    assert response.id == "resp_1"
    assert response.text == "{\"ok\": true}"
    assert parse_json_content(response.text) == {"ok": True}
    assert response.function_calls == []


def test_parses_parallel_function_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["previous_response_id"] == "resp_1"
        assert body["tools"][0]["name"] == "read_file"
        assert body["input"][0]["type"] == "function_call_output"
        return httpx.Response(
            200,
            json={
                "id": "resp_2",
                "output": [
                    {
                        "type": "function_call",
                        "name": "read_file",
                        "call_id": "call_a",
                        "arguments": "{\"path\": \"payments/store.py\"}",
                    },
                    {
                        "type": "function_call",
                        "name": "search",
                        "call_id": "call_b",
                        "arguments": "{\"query\": \"idempotency\"}",
                    },
                ],
            },
        )

    client = GrokClient(api_key=SECRET, transport=_transport(handler), sleep=lambda _s: None)
    response = client.create(
        [{"type": "function_call_output", "call_id": "call_a", "output": "{}"}],
        tools=[{"type": "function", "name": "read_file", "parameters": {"type": "object"}}],
        previous_response_id="resp_1",
    )
    assert [call.name for call in response.function_calls] == ["read_file", "search"]
    assert response.function_calls[0].arguments_json() == {"path": "payments/store.py"}


def test_strips_a_json_fence() -> None:
    assert parse_json_content("```json\n{\"a\": 1}\n```") == {"a": 1}


def test_missing_key_does_not_call_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    client = GrokClient(api_key="", transport=_transport(handler))
    with pytest.raises(GrokAuthError, match="XAI_API_KEY"):
        client.create([{"role": "user", "content": "hi"}])
    assert called is False


def test_non_json_body_is_an_api_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    client = GrokClient(api_key=SECRET, transport=_transport(handler), sleep=lambda _s: None)
    with pytest.raises(GrokAPIError, match="not JSON"):
        client.create([{"role": "user", "content": "hi"}])


def test_401_is_not_retried() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    client = GrokClient(api_key=SECRET, transport=_transport(handler), sleep=lambda _s: None)
    with pytest.raises(GrokAuthError, match="401"):
        client.create([{"role": "user", "content": "hi"}])
    assert attempts == 1


def test_429_then_success_retries_and_stops() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json={"id": "resp_ok", "output": []})

    client = GrokClient(api_key=SECRET, transport=_transport(handler), sleep=lambda _s: None)
    response = client.create([{"role": "user", "content": "hi"}])
    assert response.id == "resp_ok"
    assert attempts == 3


def test_timeout_retries_twice_then_fails() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.TimeoutException("slow")

    client = GrokClient(api_key=SECRET, transport=_transport(handler), sleep=lambda _s: None)
    with pytest.raises(GrokAPIError, match="timed out"):
        client.create([{"role": "user", "content": "hi"}])
    assert attempts == 3


def test_transcript_redacts_the_key(tmp_path: Path) -> None:
    # The saved request must not contain the bearer token or the raw key.
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "resp_1", "output": [], "echo": SECRET},
        )

    client = GrokClient(api_key=SECRET, transcript_dir=tmp_path, transport=_transport(handler), sleep=lambda _s: None)
    client.create([{"role": "user", "content": f"key {SECRET}"}])
    request_text = (tmp_path / "001-request.json").read_text(encoding="utf-8")
    response_text = (tmp_path / "001-response.json").read_text(encoding="utf-8")
    assert SECRET not in request_text
    assert SECRET not in response_text
    assert "Bearer ***" in request_text
    assert "***" in request_text


def test_dotenv_loads_only_when_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('XAI_API_KEY="from-file"\nXAI_MODEL=grok-4.7\n', encoding="utf-8")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_MODEL", raising=False)
    load_dotenv(env_file)
    assert key_status().startswith("XAI_API_KEY is loaded")
    monkeypatch.setenv("XAI_API_KEY", "from-shell")
    load_dotenv(env_file)
    assert os_environ_key() == "from-shell"


def os_environ_key() -> str:
    import os

    return os.environ["XAI_API_KEY"]
