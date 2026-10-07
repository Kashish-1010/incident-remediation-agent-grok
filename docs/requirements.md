# Requirements

Prototype of a CLI incident-investigation agent for a small payments API. One seeded incident, one Grok-backed run, one report for a person to review. No UI.

## Functional

1. **Payments API.** FastAPI app with create, authorize, capture, refund, and fetch. Amounts are integer cents. State changes append a ledger row. Logs are single-line JSON.
2. **Seeded bug.** Capture writes the debit, then can return 504 on a simulated gateway timeout before the idempotency record is stored. A retry with the same `Idempotency-Key` debits again. The committed test suite passes and does not cover that retry.
3. **Incident input.** `incidents/INC-1042.json` carries the symptom, service, time window, and routes. `incidents/logs/INC-1042.jsonl` contains two capture attempts, one 504, one 200, two ledger debits, and one idempotency key.
4. **CLI.** `python -m ledger investigate incidents/INC-1042.json` is the only trigger. It prints each phase name and, at the end, the path to `report.md`.
5. **Fixed pipeline.** Ingest, investigate, root cause, patch, regression test, verify, blast radius, report. Phase order is code. Grok does not choose the next phase.
6. **Grok over HTTP.** Model calls are `POST https://api.x.ai/v1/responses` via `httpx`, model `grok-4.7`, `Authorization: Bearer $XAI_API_KEY`. Only the investigate phase sends tools (`read_file`, `search`, `list_dir`). Tool follow-ups send `previous_response_id` and `function_call_output`. Later phases ask for a JSON object and send no tools. Request and response bodies are stored under `runs/<run-id>/api/`.
7. **Workspace.** The run copies the payments tree and tests into `runs/<run-id>/workspace` and edits that copy. The committed bug stays in place.
8. **Fix and test.** The patch may change only files under `payments/` and `tests/`. A second capture with the same idempotency key must return the original capture and add no second ledger debit. The new test is `tests/test_inc_1042.py` in the workspace.
9. **Verification.** Pytest runs in the workspace. If the new test fails, one repair turn is allowed, then pytest runs once more.
10. **Report.** `runs/<run-id>/report.md` includes the timeline, root-cause hypothesis with file citations, the diff, the pytest result, the blast radius (routes and money movement), and a line that a person must approve before anything ships.

## Non-functional

- One Python package: Python 3.11+, FastAPI, pytest, httpx. No database server, queue, or UI.
- Setup is a venv, an install, `XAI_API_KEY`, and the CLI command.
- Only `agent/grok_client.py` builds HTTP requests to Grok.
- Patches cannot delete files, cannot leave `payments/` and `tests/`, and cannot exceed 150 changed lines.
- Pytest runs in-process with a 60-second timeout. There is no shell tool.
- Each phase writes its artifact before the next phase starts.
- Logs and the report never contain the API key.

## Acceptance criteria

- With `XAI_API_KEY` unset, the CLI exits non-zero, names the missing variable, and does not call the network.
- `pytest` on the committed tree passes. A timeout-then-retry capture produces two ledger debits.
- A full investigate of INC-1042 finishes with `report.md`, an API transcript, a workspace diff, and a pytest log.
- In that workspace, the new test fails on the original code and passes after the patch. The rest of the suite still passes.
- The diff stays inside `payments/` and `tests/`.
- A second run investigates a clean copy and does not require reverting the previous run.

## Assumptions

- The demo has a working `XAI_API_KEY` and can reach `api.x.ai`.
- `grok-4.7` is the model name. `XAI_MODEL` overrides it without a code change.
- The incident log file is the evidence. The agent does not attach to a live process.
- Grok returns parseable JSON on non-tool phases and only the three declared tools during investigation. A bad response fails the phase.
- Human review is reading `report.md`. The run stops there.
- One seeded bug is the demo. Happy-path tests stay green so the new test is what proves the fix.

## Failure handling

| Failure | Behavior |
| --- | --- |
| Missing `XAI_API_KEY` | Exit before any phase and name the variable. |
| HTTP 401, 429, or 5xx, or a timeout | Retry that request twice with backoff. Then stop the phase, save the error body, and skip later phases. |
| Tool path outside the workspace, or an unknown tool | Return an error tool result. Do not read or write that path. Cap tool rounds at 8, then fail the phase. |
| Root-cause, patch, or test response is not valid JSON | Re-ask once with the validation error. If it is still invalid, stop. |
| Patch breaks path or size rules, or does not apply | Reject it and stop. Leave the workspace unchanged for that phase. |
| New test still fails after the repair turn | Write pytest output into the report and exit non-zero. Do not claim the incident is fixed. |
| Pytest hangs or fails for an environmental reason | Stop it at 60 seconds, record the failure, and do not start a repair turn. |
| Run directory already exists | Write to `runs/INC-1042-<timestamp>/`. Keep the earlier transcript. |

## Out of scope

- A frontend, auth, multi-tenant data, a real card network, or deployment.
- A second incident, a free-form agent loop, or server-side Grok tools (web search, code interpreter).
- Opening a pull request, committing the fix onto the demo branch, or copying the patch back onto the committed tree.
- More than one repair turn, token streaming, or prompt caching.
- Ranking several hypotheses. One hypothesis is enough.
