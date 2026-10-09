# Saved successful run

Sanitized artifacts from one live `python -m ledger investigate incidents/INC-1042.json` execution.
The run id was `INC-1042-20261009T055509Z`.

Included: root cause, tool budget, red and green pytest logs, store.diff, sequence, report, and the first Grok API exchange.
Omitted: the workspace copy and the rest of the API transcript.

`api/001-request.json` is the investigate call. Its Authorization header is `Bearer ***`.
This run used no tools (`tool-budget.json`). The regression test failed, then the suite passed.
