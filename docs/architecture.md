# Architecture

A Python CLI runs a fixed investigation. Grok is called three times. A small FastAPI payments service, with one seeded capture-retry bug, is the system under investigation.

```text
incident JSON
    → CLI
    → orchestrator
    → POST https://api.x.ai/v1/responses   (investigate, then files, then hypothesis)
    → write test, pytest, write store.py, pytest
    → report.md filled by code
```

The agent is not part of the first milestone. This document is the target shape. The repo currently contains the payments app, the incident, `reproduce`, and the baseline tests.

## Layout

```text
payments/
  app.py                         # routes
  store.py                       # ledger, idempotency, seeded bug
  logging.py
ledger/
  __main__.py                    # python -m ledger
  cli.py
  reproduce.py
  agent/                         # later: orchestrator, grok_client, tools, prompts
incidents/
  INC-1042.json
  logs/INC-1042.jsonl            # written by reproduce
tests/                           # suite that passes with the bug present
runs/                            # gitignored per-run workspace and report
docs/
  requirements.md
  architecture.md
```

One process. Tests use FastAPI's `TestClient`. There is no worker, queue, database server, or UI.

## Execution flow

`investigate` creates `runs/<run-id>/`, copies `payments/` and `tests/` into `workspace/`, and runs the phases below. The CLI prints the phase name as it starts. Each phase writes its artifact before the next one starts.

| Phase | Who decides | Saved artifact |
| --- | --- | --- |
| 1. Ingest | Code | Normalized incident and log slice |
| 2. Investigate | One Grok call, up to 3 tool rounds | Root-cause JSON. The prompt already includes the log and `payments/store.py` |
| 3. Test | The second Grok call returns both files. Code writes the test only | `tests/test_inc_1042.py`, then a pytest log that is expected to fail |
| 4. Patch | Code writes the store file from that same response | Replaced `payments/store.py` |
| 5. Verify | Code runs pytest | Second pytest log. No repair turn |
| 6. Blast radius | Code | Changed files, and a fixed note that capture moves money |
| 7. Report | Code fills a template. The third Grok call supplies the hypothesis paragraph | `report.md` |

The committed payments code stays buggy. Re-running copies a clean tree.

`python -m ledger reproduce` is outside this pipeline. It runs create, authorize, capture with the timeout header, then capture again with the same idempotency key, prints the debit count, and overwrites the incident log.

## Grok API boundary

All model traffic goes through `agent/grok_client.py`.

- `POST https://api.x.ai/v1/responses` with `httpx`.
- `Authorization: Bearer $XAI_API_KEY`. Model `grok-4.7`, overridable with `XAI_MODEL`.
- Three calls per run. `previous_response_id` is used only for tool follow-ups inside investigate. The file call and the hypothesis call are new requests that include the saved artifact.
- Investigate tools are local functions. When `output` contains `function_call` items, the orchestrator runs them and posts `function_call_output` with the same `call_id`.
- The file call returns the full text of the two allowed files, not a unified diff.
- Every request and response body is written under `runs/<run-id>/api/` before it is parsed.
- A JSON body may be wrapped in a markdown fence. The client strips one fence and, if parsing still fails, re-asks once.
- HTTP 401 fails immediately. HTTP 429, 5xx, and timeouts retry twice with backoff.
- Dependencies for this client are `httpx` only. The xAI SDK and an OpenAI-compatible client are not used.

Server-side tools are not requested. The client does not stream.

## Tool permissions

| Tool | Who may call it | Constraint |
| --- | --- | --- |
| `read_file`, `search`, `list_dir` | Grok, investigate phase only | Workspace plus the incident log. A path outside that returns an error tool result. |
| File replace | Orchestrator, after the second Grok call | Only `payments/store.py` and `tests/test_inc_1042.py`. At most 150 changed lines against the original. |
| Pytest | Orchestrator | Subprocess `python -m pytest`, 60-second timeout. The model has no shell. |

Tool rounds in investigate are capped at 3. Unknown tool names return an error result and do not touch the filesystem.

## Design decisions

- **Three Grok calls.** Investigate, both files, and a short hypothesis. Blast radius and the report status come from code, so a live demo does not depend on extra calls or on the model declaring success.
- **Full-file replacements.** A unified diff is the usual way a model patch fails to apply. The tree is small enough to replace two files.
- **Test, then patch.** The first pytest log shows the new test failing on the seeded bug. The second shows it passing. That is the acceptance check.
- **No repair turn.** A failed second pytest is the result. Another patch cycle is out of scope.
- **HTTP, not an SDK.** Saved request and response bodies are the Grok boundary you can open during the walkthrough.
- **Isolated workspace.** The seeded bug remains the starting point for every run.
- **Pytest in a subprocess.** A timeout is `subprocess.run(..., timeout=60)`. That is not a shell tool for the model.
- **Timeout header.** `X-Simulate-Gateway-Timeout: true` is how tests and `reproduce` hit the 504 path without a real gateway.
- **One incident.** INC-1042 is a duplicate capture after that timeout.
