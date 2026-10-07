# Architecture

A Python CLI runs a fixed eight-phase investigation. Grok reasons inside a phase. A small FastAPI payments service, with one seeded capture-retry bug, is the system under investigation.

```text
incident JSON
    → CLI
    → orchestrator
    → POST https://api.x.ai/v1/responses
    → local tool results posted back on the same response chain
    → patch and pytest in an isolated workspace
    → report.md
```

## Layout

```text
ledger/
  cli.py                         # python -m ledger investigate <incident>
  payments/                      # app under investigation
    app.py
    domain.py                    # money in integer cents
    ledger_store.py
    idempotency.py               # bug lives here
    logging.py
  incidents/
    INC-1042.json
    logs/INC-1042.jsonl
  agent/
    orchestrator.py              # the only control flow
    grok_client.py               # HTTP client for /v1/responses
    tools.py                     # read_file, search, list_dir, apply_patch, run_pytest
    prompts.py
    schemas.py
  tests/                         # suite that passes with the bug present
  runs/                          # gitignored per-run workspace, API transcript, report
  docs/
    requirements.md
    architecture.md
```

One package. Pytest and a tiny log fixture import the payments app. There is no worker, queue, or UI.

## Execution flow

`investigate` creates `runs/<run-id>/`, copies `payments/` and `tests/` into `workspace/`, and runs the phases below. The CLI prints the phase name as it starts. Each phase writes its artifact before the next one starts.

| Phase | Who decides | Saved artifact |
| --- | --- | --- |
| 1. Ingest | Code | Normalized incident, log slice, file index |
| 2. Investigate | Grok, with tools | Notes citing log lines and file paths |
| 3. Root cause | Grok, JSON only | Hypothesis, confidence, files to change |
| 4. Patch | Grok proposes; code applies | Unified diff, after path and size checks |
| 5. Regression test | Grok writes; code writes the file | `tests/test_inc_1042.py` |
| 6. Verify | Code runs pytest | Output. One repair turn if the new test fails |
| 7. Blast radius | Code lists the diff; Grok assesses | Routes, money movement, rollback note |
| 8. Report | Code assembles; Grok writes the narrative | `report.md` |

The committed payments code stays buggy. Re-running copies a clean tree, so the demo does not depend on reverting a previous fix.

## Grok API boundary

All model traffic goes through `agent/grok_client.py`.

- `POST https://api.x.ai/v1/responses` with `httpx`.
- `Authorization: Bearer $XAI_API_KEY`. Model `grok-4.7`, overridable with `XAI_MODEL`.
- Body fields used: `model`, `input`, `tools` (investigate only), `previous_response_id` (follow-ups).
- Investigate tools are local functions in the request. When `output` contains `function_call` items, the orchestrator runs them and posts `function_call_output` with the same `call_id`.
- Root cause, patch, test, blast radius, and report send no tools and require a JSON object in the message text.
- Every request and response body is written under `runs/<run-id>/api/` before it is parsed.
- Dependencies are `httpx` only. The xAI SDK and an OpenAI-compatible client are not used.

Server-side tools (web search, code interpreter) are not requested. The client does not stream.

## Tool permissions

| Tool | Who may call it | Constraint |
| --- | --- | --- |
| `read_file`, `search`, `list_dir` | Grok, investigate phase only | Workspace plus the incident log. A path outside that returns an error tool result. |
| `apply_patch` | Orchestrator, after Grok proposes a diff | `payments/` and `tests/` only. No deletes. At most 150 changed lines. |
| `run_pytest` | Orchestrator, verify phase | In-process pytest, 60-second timeout. No shell. |

Tool rounds in investigate are capped at 8. Unknown tool names return an error result and do not touch the filesystem.

## Design decisions

- **Fixed phases instead of a free-form loop.** The demo has to be narrated. A phase graph in code makes the transcript linear and limits how far a bad model turn can wander.
- **HTTP, not an SDK.** The API call is the thing being demonstrated. Saving raw request and response bodies makes that boundary visible.
- **Isolated workspace.** The seeded bug remains the starting point for every run. The report is the human gate; the prototype does not open a pull request or copy the fix back.
- **One repair turn.** Enough to recover from a bad test assertion. A longer loop is a different product.
- **Pytest in-process.** Verification stays a function call with a timeout, so the agent never gains a general shell.
- **One incident.** INC-1042 is a duplicate capture after a timeout. That is enough to exercise logs, a small code change, a regression test, and a blast-radius note about money movement.
