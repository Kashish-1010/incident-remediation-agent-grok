# Demo runbook

Ten to twelve minutes, from a repository checkout with `.venv` active and `XAI_API_KEY` set. Set `XAI_TIMEOUT=300` in `.env` before you start. Run every command from the repository root.

A full `investigate` call takes about three minutes. Start it when the runbook says so, and talk through the files while it runs. Do not start a second run in the same directory name; a repeat writes `runs/INC-1042-<timestamp>/`.

## 0. Before the room (2 minutes, off camera)

```bash
source .venv/bin/activate
python -m ledger check-key
pytest -q
```

`check-key` must exit 0 and must not print the key. `pytest` must pass. That suite does not cover the bug.

## 1. The bug (2 minutes)

Open `payments/store.py` and find `capture`. The debit is appended, then `simulate_timeout` returns 504 before `self.idempotency[idempotency_key]` is set. The status stays `authorized`.

Open `incidents/INC-1042.json`. The fix bar is one sentence: the same `Idempotency-Key` returns the original capture response and does not append a second debit, including when the first attempt sent `X-Simulate-Gateway-Timeout: true`.

```bash
python -m ledger reproduce
```

Point at `attempt_1 status=504`, `attempt_2 status=200`, and `debit_count=2`. Both lines use the same idempotency key. Mention that `tests/test_payments.py` stays green because it never sends the timeout header.

## 2. Start the workflow (1 minute, then it runs)

Say what the command will do before you run it: copy the tree, call Grok, prove a new test fails, ask for a patch only after that failure, run the suite, write a report. The committed `payments/store.py` is not modified.

```bash
python -m ledger investigate incidents/INC-1042.json
```

Leave it running. The phases print as they start.

## 3. While it runs (3 minutes)

Open `ledger/agent/grok_client.py`. The only HTTP call is `POST https://api.x.ai/v1/responses`. Show `API_URL`, the `Authorization` header, and `_write_transcript`. Say that 401 is not retried and that the transcript stores `Bearer ***`.

Open `ledger/agent/investigate.py` and name the phase order: ingest, investigate with at most `LEDGER_MAX_TOOL_ROUNDS` tool rounds (default 8), then one no-tool fallback if the model is still calling tools, then remediation. Tools are `read_file`, `search`, and `list_dir` in `ledger/agent/tools.py`. A path outside the workspace returns an error tool result. The model has no shell.

Open `ledger/agent/remediate.py` and show that the test file is written and pytest runs before `_patch_prompt` is sent. If the red exit code is not 1, the patch call does not happen.

## 4. API calls and the patch (3 minutes)

When the process prints `artifacts:`, open that directory. Use the path from the last line, not an older `runs/INC-1042*` folder.

Open `api/001-request.json`. Show the URL, `Bearer ***`, and that the body includes the log and `payments/store.py`. Open the matching `001-response.json` and then `root_cause.json`. The hypothesis should name the timeout path and a second debit. Confidence on a good run is `high`, and `files` includes `payments/store.py`.

If later request files exist, open the one whose body is the test prompt, then the one whose body is the patch prompt. The patch request is absent when the red test did not fail.

Open `pytest-red.txt`. The first line is `exit_code: 1`. The failure is the retry: status 200 instead of 504, or two debits.

Open `store.diff`. It is only `payments/store.py`. On a good run the idempotency record stores `body` and `status_code`, and the timeout path stores that record before returning. The debit is not appended a second time.

Open `pytest-green.txt`. The first line is `exit_code: 0`.

Open `sequence.json`. The steps are `red` (exit 1), `patch`, `green` (exit 0).

## 5. The report (2 minutes)

Open `report.md`.

- Status says remediation succeeded only because red, patch, and green all matched. Say that a mismatched pytest log would flip this sentence. The check is in `ledger/agent/report.py`, not in a Grok call.
- Blast radius: capture on `POST /v1/payments/{id}/capture` moves funds. Refund was not edited.
- Rollback: restore the pre-patch `payments/store.py`. The ledger is in memory.
- Approval: a person must approve before shipping. The process does not open a pull request or deploy.

If you still have a minute, open `docs/security-review.md` and say the two production gaps you would not skip: the model can still write a harmful `payments/store.py`, and pytest is not sandboxed.

## If the live run fails

Do not describe it as a successful fix. Open `error.json` and the status section of `report.md`. A missing `XAI_API_KEY` exits before any phase. A timeout after two retries stops the phase and does not claim success. A regression test that passes on the unpatched code stops before the patch call.

You can still walk an older successful directory under `runs/` if one exists. Say that it is a previous run, and read its `sequence.json` before using its `report.md`.
