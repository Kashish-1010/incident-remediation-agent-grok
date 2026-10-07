"""Run the seeded capture-retry bug and write the incident log."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from payments.app import create_app

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG = ROOT / "incidents" / "logs" / "INC-1042.jsonl"
IDEMPOTENCY_KEY = "idem_inc_1042"


def reproduce(log_path: Path = DEFAULT_LOG) -> int:
    app = create_app()
    client = TestClient(app)
    created = client.post("/v1/payments", json={"amount": 2500, "currency": "usd"})
    created.raise_for_status()
    payment_id = created.json()["id"]
    authorized = client.post(f"/v1/payments/{payment_id}/authorize")
    authorized.raise_for_status()

    headers = {"Idempotency-Key": IDEMPOTENCY_KEY}
    first = client.post(
        f"/v1/payments/{payment_id}/capture",
        headers={**headers, "X-Simulate-Gateway-Timeout": "true"},
    )
    second = client.post(f"/v1/payments/{payment_id}/capture", headers=headers)
    view = client.get(f"/v1/payments/{payment_id}")
    view.raise_for_status()
    debits = [entry for entry in view.json()["ledger"] if entry["type"] == "debit"]

    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(event, separators=(",", ":")) for event in app.state.store.events]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"payment_id={payment_id}")
    print(f"attempt_1 status={first.status_code} idempotency_key={IDEMPOTENCY_KEY}")
    print(f"attempt_2 status={second.status_code} idempotency_key={IDEMPOTENCY_KEY}")
    print(f"debit_count={len(debits)}")
    for entry in debits:
        print(f"debit id={entry['id']} amount={entry['amount']}")
    print(f"log={log_path}")
    return 0 if len(debits) == 2 and first.status_code == 504 and second.status_code == 200 else 1
