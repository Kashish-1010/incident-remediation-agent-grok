# Requirements

Prototype of a CLI incident-investigation agent for a small payments API. One seeded incident, one Grok-backed run, one report for a person to review. No UI.

## Functional

1. **Payments API.** FastAPI app with create, authorize, capture, refund, and fetch. Amounts are integer cents. Capture and refund append ledger rows. Logs are single-line JSON. The app lives in three modules: `payments/app.py`, `payments/store.py`, `payments/logging.py`.
2. **Seeded bug.** Capture writes the debit, then can return 504 when the request sends `X-Simulate-Gateway-Timeout: true`, before the idempotency record is stored. Payment status stays `authorized` on that path. A retry with the same `Idempotency-Key` debits again. The committed test suite passes and does not cover that retry.
3. **Incident input.** `incidents/INC-1042.json` carries the symptom, service, time window, routes, the timeout header, and this fix bar: the same `Idempotency-Key` returns the original capture response and does not append a second debit, including when the first attempt took the timeout path. `python -m ledger reproduce` runs that scenario and writes `incidents/logs/INC-1042.jsonl`.
4. **CLI.** `python -m ledger investigate incidents/INC-1042.json` is the agent trigger. It prints each phase name and, at the end, the path to `report.md`. `reproduce` is a separate command used to show the bug.
5. **Fixed pipeline.** Ingest, investigate, test, patch, verify, blast radius, report. Phase order is code. Grok does not choose the next phase.
6. **Grok over HTTP.** Model calls are `POST https://api.x.ai/v1/responses` via `httpx`, model `grok-4.7`, `Authorization: Bearer $XAI_API_KEY`. Investigate sends tools (`read_file`, `search`, `list_dir`), at most 3 rounds, and its first prompt already includes the incident log and `payments/store.py`. Tool follow-ups in that phase send `previous_response_id` and `function_call_output`. The next call returns the full text of `tests/test_inc_1042.py`. Only after that test fails does the following call return the full text of `payments/store.py`. A later call writes the hypothesis paragraph for the report. Request and response bodies are stored under `runs/<run-id>/api/`. JSON replies are parsed by stripping a markdown fence if present, then one re-ask on invalid JSON.
7. **Workspace.** The run copies the payments tree and tests into `runs/<run-id>/workspace` and edits that copy. The committed bug stays in place.
8. **Fix and test.** The model may replace only `tests/test_inc_1042.py` and, after that test fails, `payments/store.py`. Code writes the test, runs it, and saves `pytest-red.txt`. A pass, a timeout, or a failure that is not the seeded assertion stops the run before any patch call. The patch is then applied and the full suite is saved as `pytest-green.txt`. `sequence.json` records red, then patch, then green. There is no repair turn. A green failure is reported as failure.
9. **Verification.** The orchestrator runs `python -m pytest` in a subprocess with a 60-second timeout. The model has no shell tool. A failing second pytest is written into the report and the process exits non-zero.
10. **Report.** Code fills `runs/<run-id>/report.md` from the phase artifacts: timeline, diff, both pytest results, blast radius (changed files plus a fixed note that capture moves money), and a line that a person must approve before anything ships. Grok contributes only the hypothesis paragraph. A failed pytest is never reported as fixed.

## Non-functional

- One Python package: Python 3.11+, FastAPI, pytest, httpx. No database server, queue, or UI.
- Setup is a venv, an install, `XAI_API_KEY`, and the CLI command.
- Only `agent/grok_client.py` builds HTTP requests to Grok.
- Replacements cannot leave `payments/` and `tests/`, and the replacement cannot change more than 150 lines relative to the original file.
- Each phase writes its artifact before the next phase starts.
- Logs and the report never contain the API key.

## Acceptance criteria

- With `XAI_API_KEY` unset, the CLI exits non-zero, names the missing variable, and does not call the network.
- `pytest` on the committed tree passes. `python -m ledger reproduce` prints two ledger debits and refreshes the incident log.
- A full investigate of INC-1042 finishes with `report.md`, an API transcript, a workspace diff, and both pytest logs.
- In that workspace, the new test fails before the store patch and passes after it. The rest of the suite still passes.
- The only replaced files are `payments/store.py` and `tests/test_inc_1042.py`.
- A second run investigates a clean copy and does not require reverting the previous run.

## Assumptions

- The demo has a working `XAI_API_KEY` and can reach `api.x.ai`.
- `grok-4.7` is the model name. `XAI_MODEL` overrides it without a code change.
- The incident log file is the evidence. The agent does not attach to a live process.
- Grok returns parseable JSON on the non-tool calls and only the three declared tools during investigation. A bad response fails the phase.
- Human review is reading `report.md`. The run stops there.
- One seeded bug is the demo. Happy-path tests stay green so the new test is what proves the fix.
- `X-Simulate-Gateway-Timeout: true` is the only way to force the 504 path.

## Failure handling

| Failure | Behavior |
| --- | --- |
| Missing `XAI_API_KEY` | Exit before any phase and name the variable. |
| HTTP 401 | Stop immediately, save the error body, and name `XAI_API_KEY`. |
| HTTP 429 or 5xx, or a timeout | Retry that request twice with backoff. Then stop the phase, save the error body, and skip later phases. |
| Tool path outside the workspace, or an unknown tool | Return an error tool result. Do not read or write that path. After 3 tool rounds, fail the phase. |
| A model response is not valid JSON | Re-ask once with the validation error. If it is still invalid, stop. |
| Replacement breaks the path or size rules | Reject it and stop. Leave the workspace unchanged for that write. |
| Pytest fails after the patch | Write both pytest logs into the report and exit non-zero. Do not claim the incident is fixed. |
| Pytest times out | Stop it at 60 seconds, record the failure, and do not start another model call. |
| Run directory already exists | Write to `runs/INC-1042-<timestamp>/`. Keep the earlier transcript. |

## Out of scope

- A frontend, auth, multi-tenant data, a real card network, or deployment.
- A second incident, a free-form agent loop, server-side Grok tools, or a unified-diff applier.
- A repair turn after a failed test.
- Opening a pull request, committing the fix onto the demo branch, or copying the patch back onto the committed tree.
- Token streaming, prompt caching, or ranking several hypotheses.
