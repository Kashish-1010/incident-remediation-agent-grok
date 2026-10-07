# Ledger incident prototype

A small payments API with a seeded bug: capturing a payment after a simulated gateway timeout stores the debit and returns 504 before saving the idempotency record. The retry debits the customer again.

The investigation agent is not in this milestone. `docs/requirements.md` and `docs/architecture.md` describe the target CLI.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m ledger reproduce
```

`reproduce` prints two ledger debits and rewrites `incidents/logs/INC-1042.jsonl`. Run it from the repository root.

## API key

The client calls `POST https://api.x.ai/v1/responses` with model `grok-4.7`. It reads `XAI_API_KEY` from the environment. A `.env` file in the repository root is loaded only for variables that are not already set. `.env` is gitignored. Do not put a real key in `.env.example` or in source.

```bash
cp .env.example .env
```

Open `.env` and set `XAI_API_KEY` to the key from the [xAI console](https://console.x.ai/). Leave the rest as-is unless you need a different model or a longer timeout. Official samples use a long timeout because this model reasons. The default here is 120 seconds (`XAI_TIMEOUT`).

Check that the process loaded the key. This prints the length and the first four characters. It does not print the key and it does not call the API.

```bash
python -m ledger check-key
```

A shell export wins over `.env`:

```bash
export XAI_API_KEY="your key"
python -m ledger check-key
```

The timeout path is `X-Simulate-Gateway-Timeout: true` on `POST /v1/payments/{id}/capture`, together with an `Idempotency-Key` header. Amounts are integer cents.
