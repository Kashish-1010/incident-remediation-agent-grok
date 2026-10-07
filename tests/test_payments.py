"""Happy-path coverage. The timeout retry is intentionally not tested here."""

import pytest
from fastapi.testclient import TestClient

from payments.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_create_and_fetch(client: TestClient) -> None:
    created = client.post("/v1/payments", json={"amount": 1800, "currency": "usd"})
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "created"
    assert body["ledger"] == []

    fetched = client.get(f"/v1/payments/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


def test_authorize_capture_writes_one_debit(client: TestClient) -> None:
    payment_id = _authorized(client, 4200)
    captured = client.post(
        f"/v1/payments/{payment_id}/capture",
        headers={"Idempotency-Key": "idem_happy"},
    )
    assert captured.status_code == 200
    body = captured.json()
    assert body["status"] == "captured"
    assert [entry["type"] for entry in body["ledger"]] == ["debit"]
    assert body["ledger"][0]["amount"] == 4200


def test_second_capture_after_success_is_idempotent(client: TestClient) -> None:
    payment_id = _authorized(client, 900)
    headers = {"Idempotency-Key": "idem_once"}
    first = client.post(f"/v1/payments/{payment_id}/capture", headers=headers)
    second = client.post(f"/v1/payments/{payment_id}/capture", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(second.json()["ledger"]) == 1


def test_refund_appends_a_credit(client: TestClient) -> None:
    payment_id = _authorized(client, 1500)
    client.post(f"/v1/payments/{payment_id}/capture", headers={"Idempotency-Key": "idem_refund"})
    refunded = client.post(f"/v1/payments/{payment_id}/refund")
    assert refunded.status_code == 200
    body = refunded.json()
    assert body["status"] == "refunded"
    assert [entry["type"] for entry in body["ledger"]] == ["debit", "credit"]
    assert body["ledger"][1]["amount"] == 1500


def test_capture_requires_an_idempotency_key(client: TestClient) -> None:
    payment_id = _authorized(client, 100)
    response = client.post(f"/v1/payments/{payment_id}/capture")
    assert response.status_code == 422


def _authorized(client: TestClient, amount: int) -> str:
    created = client.post("/v1/payments", json={"amount": amount, "currency": "usd"})
    assert created.status_code == 201
    payment_id = created.json()["id"]
    authorized = client.post(f"/v1/payments/{payment_id}/authorize")
    assert authorized.status_code == 200
    return payment_id
