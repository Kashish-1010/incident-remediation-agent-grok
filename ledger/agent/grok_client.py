"""HTTP client for the xAI Responses API.

Verified against the official docs (October 2026):
https://docs.x.ai/developers/model-capabilities/text/generate-text
https://docs.x.ai/developers/tools/function-calling
https://docs.x.ai/developers/models

POST https://api.x.ai/v1/responses
model grok-4.7
Authorization: Bearer $XAI_API_KEY

A response has id and output. A message item carries content[].text.
A function call item has type "function_call", name, arguments (JSON string),
and call_id. The follow-up input item is
{"type": "function_call_output", "call_id", "output"} plus previous_response_id.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ledger.env import api_key as load_api_key

API_URL = "https://api.x.ai/v1/responses"
DEFAULT_MODEL = "grok-4.7"
RETRY_STATUSES = {429, 500, 502, 503, 504}
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class GrokError(Exception):
    """Base error for the Responses API client."""


class GrokAuthError(GrokError):
    """The API key is missing or rejected. Not retried."""


class GrokAPIError(GrokError):
    """The request failed after the allowed retries, or the status is not retried."""


@dataclass
class FunctionCall:
    name: str
    call_id: str
    arguments: str

    def arguments_json(self) -> object:
        return json.loads(self.arguments)


@dataclass
class GrokResponse:
    id: str
    output: list[dict]
    raw: dict
    text: str = ""
    function_calls: list[FunctionCall] = field(default_factory=list)


class GrokClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        transcript_dir: Path | None = None,
        timeout: float | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ) -> None:
        self.api_key = (api_key if api_key is not None else load_api_key()).strip()
        self.model = model or os.environ.get("XAI_MODEL", DEFAULT_MODEL)
        self.transcript_dir = transcript_dir
        self.timeout = timeout if timeout is not None else float(os.environ.get("XAI_TIMEOUT", "120"))
        self._sleep = sleep
        self._call_index = 0
        self._client = httpx.Client(timeout=self.timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def create(
        self,
        input_items: list[dict],
        tools: list[dict] | None = None,
        previous_response_id: str | None = None,
    ) -> GrokResponse:
        if not self.api_key:
            raise GrokAuthError(
                "XAI_API_KEY is not set. Copy .env.example to .env and set the variable there, or export it in the shell."
            )
        payload: dict = {"model": self.model, "input": input_items}
        if tools:
            payload["tools"] = tools
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        body = self._post(payload)
        return parse_response(body)

    def _post(self, payload: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        attempt = 0
        while True:
            try:
                response = self._client.post(API_URL, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                if attempt >= 2:
                    self._write_transcript(payload, {"error": "timeout", "detail": str(exc)}, None)
                    raise GrokAPIError("Grok request timed out after 2 retries") from exc
                self._sleep(0.5 * (2**attempt))
                attempt += 1
                continue

            if response.status_code == 401:
                self._write_transcript(payload, _error_body(response), response.status_code)
                raise GrokAuthError("Grok rejected the API key (HTTP 401). Check XAI_API_KEY.")

            if response.status_code in RETRY_STATUSES and attempt < 2:
                self._sleep(0.5 * (2**attempt))
                attempt += 1
                continue

            if response.status_code >= 400:
                self._write_transcript(payload, _error_body(response), response.status_code)
                raise GrokAPIError(f"Grok request failed with HTTP {response.status_code}")

            parsed = response.json()
            self._write_transcript(payload, parsed, response.status_code)
            return parsed

    def _write_transcript(self, request: dict, response_body: dict, status: int | None) -> None:
        if self.transcript_dir is None:
            return
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        self._call_index += 1
        stem = f"{self._call_index:03d}"
        secret = self.api_key
        request_record = {
            "url": API_URL,
            "headers": {"Authorization": "Bearer ***", "Content-Type": "application/json"},
            "body": request,
        }
        response_record = {"status": status, "body": response_body}
        (self.transcript_dir / f"{stem}-request.json").write_text(
            _dump(redact(request_record, secret)),
            encoding="utf-8",
        )
        (self.transcript_dir / f"{stem}-response.json").write_text(
            _dump(redact(response_record, secret)),
            encoding="utf-8",
        )


def parse_response(body: dict) -> GrokResponse:
    output = body.get("output") or []
    calls: list[FunctionCall] = []
    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            arguments = item.get("arguments", "")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments)
            calls.append(FunctionCall(name=item.get("name", ""), call_id=item.get("call_id", ""), arguments=arguments))
        elif item.get("type") == "message":
            texts.append(_message_text(item.get("content")))
    return GrokResponse(id=str(body.get("id", "")), output=output, raw=body, text="\n".join(part for part in texts if part), function_calls=calls)


def parse_json_content(text: str) -> object:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _FENCE.sub("", stripped).strip()
    return json.loads(stripped)


def redact(value: object, secret: str) -> object:
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, "***")
    if isinstance(value, list):
        return [redact(item, secret) for item in value]
    if isinstance(value, dict):
        return {key: redact(item, secret) for key, item in value.items()}
    return value


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def _error_body(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except json.JSONDecodeError:
        body = {"text": response.text}
    if not isinstance(body, dict):
        body = {"body": body}
    return body


def _dump(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"
