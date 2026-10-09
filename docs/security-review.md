# Security review

Reviewed the investigation and remediation prototype after the end-to-end run. This is a local demo, not a production control. High-risk gaps that were small enough to fix in this prototype are fixed. The rest are recorded here.

## Fixed in this pass

- **Incident log path.** `log` in the incident file must be a relative file under `incidents/`, and it must not be a symlink. A value such as `../.env` is rejected before the file is read, so a crafted incident cannot pull the API key into the prompt or `ingest.json`.
- **Malformed Grok bodies.** A 200 response that is not a JSON object raises `GrokAPIError` instead of crashing in the parser.
- **API failures.** `GrokAPIError` and `GrokAuthError` during investigation or remediation are written to `error.json` and `report.md`. The report does not say remediation succeeded.
- **Success claim.** The report requires `pytest-red.txt` to start with `exit_code: 1` and `pytest-green.txt` to start with `exit_code: 0`, in addition to `sequence.json`. A sequence that disagrees with the pytest logs is a failure.
- **Untrusted prompt data.** Logs, source, and pytest output are wrapped as data between `UNTRUSTED START` and `UNTRUSTED END`. That does not stop a model from following hostile text. The write path is still enforced in code.

## Remaining risks

| Severity | Risk |
| --- | --- |
| High for production, accepted here | Prompt injection in logs or source can still steer the hypothesis, the test, and the patch contents. The allowlist stops the write path, not a harmful but valid `payments/store.py`. |
| High for production, accepted here | The patched tree is not reviewed by a second model-independent test beyond pytest. A test and a patch can agree on the wrong money behavior and the suite still goes green. |
| Medium | Transcript redaction is an exact string replace of `XAI_API_KEY`. A key split across fields, or a different secret in the log, is stored in `runs/`. |
| Medium | `check-key` prints the key length and the first four characters. |
| Medium | Tool reads follow the workspace copy. A symlink planted inside `payments/` or `tests/` before the run resolves and is rejected if it points outside, but `copytree` copies symlink targets' contents into the workspace. |
| Medium | Pytest runs with the workspace on `PYTHONPATH`. A patched `payments` module can run arbitrary code during the suite. That is the point of the prototype and it is not sandboxed. |
| Medium | Retries cover 429, 5xx, and timeouts only. A hung connection that never raises `TimeoutException` waits for the httpx timeout, then retries. |
| Low | The report quotes the model hypothesis and pytest output. Those sections can contain misleading text. The status line does not. |
| Low | Run artifacts under `runs/` are gitignored but are local plaintext, including model transcripts. |

## Not production-ready

- No authentication, tenancy, or audit log beyond local files.
- No human approval gate that blocks an action. The report only says a person must approve. The CLI never deploys or opens a pull request.
- No secret scanning of incident logs before they are sent to the API.
- No sandbox around pytest or the model-supplied Python.
- One seeded incident and an in-memory ledger. Idempotency and rollback notes do not apply to a real database.
- The model is trusted to propose the only code change. A production workflow needs a separate reviewer and a change window.
