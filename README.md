# Ledger incident prototype

A CLI agent investigates one seeded payments bug, asks Grok for a root cause and a fix, and writes a report for a person to review. There is no UI, deploy step, or pull request.

The committed payments code stays buggy. Each run copies `payments/` and `tests/` into `runs/<id>/workspace/` and edits only that copy.

## The bug

`POST /v1/payments/{id}/capture` appends a ledger debit, then returns 504 when the request sends `X-Simulate-Gateway-Timeout: true`, before it stores the `Idempotency-Key`. The payment stays `authorized`. A retry with the same key does not find a stored response, so it debits again.

Amounts are integer cents. The happy-path suite in `tests/test_payments.py` passes and does not cover that retry. `payments/store.py` holds the bug.

## Architecture

One Python process. Phase order is fixed in code. Grok does not choose the next phase.

```text
incidents/INC-1042.json
    → python -m ledger investigate
    → copy payments/ and tests/ into runs/<id>/workspace/
    → POST https://api.x.ai/v1/responses   (ledger/agent/grok_client.py)
    → root cause, then a failing test, then a store.py replacement
    → pytest red, then pytest green
    → report.md written by code
```

| Phase | Who decides | What you should see |
| --- | --- | --- |
| ingest | Code | `ingest.json`, workspace copy |
| investigate | Grok, up to 3 rounds of `read_file`, `search`, `list_dir` | `root_cause.json`, `runs/<id>/api/` |
| regression-test | Grok returns the full text of `tests/test_inc_1042.py` | The test is written, then pytest fails |
| patch | Grok returns the full text of `payments/store.py` only after that failure | `store.diff` |
| verify-green | Code runs pytest | `pytest-green.txt` |
| report | Code, from the artifacts | `report.md` |

Tradeoffs that keep the demo short:

- Three model calls, not an open-ended tool loop. The investigate call may use tools. The test and patch calls return one JSON file each. The report does not call the model.
- Full-file replacements instead of a unified diff from the model. The orchestrator still writes `store.diff` and rejects a replacement over 150 changed lines.
- The patch call is skipped if the new test passes or errors for a reason other than a failed assertion. There is no repair turn.
- The workspace is a copy, so a second run does not need the first patch reverted. If `runs/INC-1042/` already exists, the new directory is `runs/INC-1042-<timestamp>/`.

`docs/architecture.md` is the longer design note. `docs/requirements.md` is the scope. `docs/security-review.md` is what is and is not safe. `docs/demo-runbook.md` is the live walkthrough.

## Grok API

All model HTTP traffic is in `ledger/agent/grok_client.py`.

- `POST https://api.x.ai/v1/responses`
- `Authorization: Bearer $XAI_API_KEY`
- Model `grok-4.7`, overridable with `XAI_MODEL`
- Investigate follow-ups send `previous_response_id` and `function_call_output`. The test and patch calls are new requests.
- HTTP 401 fails immediately. HTTP 429, 5xx, and timeouts retry twice.
- Request and response bodies are saved under `runs/<id>/api/`. The Authorization header and the exact API key string are replaced with `***`.

## Setup

From the repository root. On Ubuntu, `python3 -m venv` needs the venv package once: `sudo apt install python3.12-venv`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

`pytest` on this tree passes. It does not exercise the timeout retry.

Create a key at https://console.x.ai/ and put it only in `.env`. That file is gitignored. Do not commit it.

```bash
cp .env.example .env
```

Set `XAI_API_KEY` in `.env`. A variable already exported in the shell wins over `.env`. Reasoning calls often need more than the 120 second default. For the demo, set `XAI_TIMEOUT=300` in `.env`.

```bash
python -m ledger check-key
```

This prints the key length and the first four characters. It does not print the key and it does not call the API. Exit code 0 means the variable is loaded.

## Demo

Run these from the repository root with the virtualenv active.

Show the double debit without the model:

```bash
python -m ledger reproduce
```

Expect `attempt_1 status=504`, `attempt_2 status=200`, `debit_count=2`, and the same idempotency key on both attempts. The command rewrites `incidents/logs/INC-1042.jsonl`.

Run the full workflow:

```bash
python -m ledger investigate incidents/INC-1042.json
```

The process prints `phase: ingest`, `investigate`, `regression-test`, `verify-red`, `patch`, `verify-green`, and `report`. A recent run took about three minutes. The last lines are the report path and `artifacts: runs/<id>`.

Expected red → patch → green behavior:

- `pytest-red.txt` starts with `exit_code: 1`. The new test fails because the retry does not replay the 504 and a second debit is present.
- `store.diff` changes only `payments/store.py` inside the workspace. The idempotency record keeps the status code, so the retry returns the original response and does not append another debit.
- `pytest-green.txt` starts with `exit_code: 0`.
- `report.md` says remediation succeeded only when that sequence matches the pytest logs. It also says a person must approve the change before it ships. The CLI does not merge or deploy.

To continue a saved run after the root cause already exists, without repeating ingest:

```bash
python -m ledger remediate runs/<id>
```

That command still calls Grok for the test and the patch, then writes the report.

## Artifacts

Under `runs/<id>/`:

| File | Contents |
| --- | --- |
| `ingest.json` | Incident fields and the log text |
| `workspace/` | Copy of `payments/` and `tests/`, plus the generated test and patch |
| `api/*-request.json`, `api/*-response.json` | Raw Grok calls, with the API key redacted |
| `root_cause.json` | Hypothesis, confidence, files, evidence |
| `investigate.json` | Tool trace for the investigate phase |
| `pytest-red.txt` | Regression test before the patch |
| `store.diff` | Unified diff of `payments/store.py` |
| `pytest-green.txt` | Full workspace suite after the patch |
| `sequence.json` | `red`, `patch`, `green` and their exit codes |
| `report.md` | Incident, evidence, both pytest results, diff summary, blast radius, rollback, approval line |
| `error.json` | Present when a phase stops. The report then says remediation did not succeed |

`runs/` is gitignored.

## Security boundaries

- Tools the model may call: `read_file`, `search`, `list_dir`. At most 3 rounds. Paths must stay in the workspace, except the incident log file.
- The incident log must be a non-symlink file under `incidents/`.
- The model cannot run a shell. Pytest is a subprocess the orchestrator starts, with the workspace on `PYTHONPATH`, and a 60 second timeout.
- Replacements are only `tests/test_inc_1042.py` and, after the red failure, `payments/store.py`.
- Logs and source are labeled untrusted in the prompt. That does not stop the model from following hostile text inside an allowed file.
- The report's success line is code. It requires red exit 1, a `payments/store.py` patch, green exit 0, and pytest logs that start with those exit codes.

## Before production

Read `docs/security-review.md`. The short version: this demo has no auth, no sandbox around pytest, no second reviewer, and no approval gate that blocks shipping. Prompt injection in a log can still change the contents of the allowed patch. A test and a patch can agree on the wrong ledger behavior and still go green. Secret redaction is an exact match on `XAI_API_KEY`. The ledger is in memory, so the rollback note does not apply to a real database.
