"""Ledger and idempotency. The capture timeout path is the seeded bug."""

from uuid import uuid4

from payments.logging import log_event


class PaymentError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class Store:
    def __init__(self) -> None:
        self.payments: dict[str, dict] = {}
        self.ledger: list[dict] = []
        self.idempotency: dict[str, dict] = {}
        self.events: list[dict] = []

    def create_payment(self, amount: int, currency: str) -> dict:
        if amount <= 0:
            raise PaymentError(400, "amount must be a positive number of cents")
        if currency != "usd":
            raise PaymentError(400, "only usd is supported")
        payment = {
            "id": f"pay_{uuid4().hex[:8]}",
            "amount": amount,
            "currency": currency,
            "status": "created",
        }
        self.payments[payment["id"]] = payment
        log_event(self.events, "payment_created", payment_id=payment["id"], amount=amount)
        return self._view(payment["id"])

    def authorize(self, payment_id: str) -> dict:
        payment = self._require(payment_id)
        if payment["status"] != "created":
            raise PaymentError(409, f"cannot authorize a payment in status {payment['status']}")
        payment["status"] = "authorized"
        log_event(self.events, "payment_authorized", payment_id=payment_id)
        return self._view(payment_id)

    def capture(self, payment_id: str, idempotency_key: str, simulate_timeout: bool) -> tuple[dict, int]:
        if not idempotency_key:
            raise PaymentError(400, "Idempotency-Key is required")
        payment = self._require(payment_id)
        log_event(
            self.events,
            "capture_started",
            payment_id=payment_id,
            idempotency_key=idempotency_key,
            amount=payment["amount"],
        )
        # A stored key should replay the first response. The timeout path never stores it.
        stored = self.idempotency.get(idempotency_key)
        if stored is not None:
            log_event(
                self.events,
                "idempotency_hit",
                payment_id=payment_id,
                idempotency_key=idempotency_key,
            )
            return stored, 200

        if payment["status"] != "authorized":
            raise PaymentError(409, f"cannot capture a payment in status {payment['status']}")

        entry = {
            "id": f"led_{uuid4().hex[:8]}",
            "payment_id": payment_id,
            "type": "debit",
            "amount": payment["amount"],
        }
        self.ledger.append(entry)
        log_event(
            self.events,
            "ledger_debit",
            payment_id=payment_id,
            entry_id=entry["id"],
            amount=entry["amount"],
            idempotency_key=idempotency_key,
        )

        # Bug: the timeout path returns before the idempotency record is stored,
        # and leaves the payment authorized so a retry debits again.
        if simulate_timeout:
            log_event(
                self.events,
                "gateway_timeout",
                payment_id=payment_id,
                idempotency_key=idempotency_key,
                status_code=504,
            )
            return {"error": "gateway_timeout", "payment_id": payment_id}, 504

        payment["status"] = "captured"
        body = self._view(payment_id)
        self.idempotency[idempotency_key] = body
        log_event(
            self.events,
            "idempotency_stored",
            payment_id=payment_id,
            idempotency_key=idempotency_key,
        )
        log_event(
            self.events,
            "capture_succeeded",
            payment_id=payment_id,
            idempotency_key=idempotency_key,
            status_code=200,
        )
        return body, 200

    def refund(self, payment_id: str) -> dict:
        payment = self._require(payment_id)
        if payment["status"] != "captured":
            raise PaymentError(409, f"cannot refund a payment in status {payment['status']}")
        entry = {
            "id": f"led_{uuid4().hex[:8]}",
            "payment_id": payment_id,
            "type": "credit",
            "amount": payment["amount"],
        }
        self.ledger.append(entry)
        payment["status"] = "refunded"
        log_event(
            self.events,
            "ledger_credit",
            payment_id=payment_id,
            entry_id=entry["id"],
            amount=entry["amount"],
        )
        return self._view(payment_id)

    def get_payment(self, payment_id: str) -> dict:
        self._require(payment_id)
        return self._view(payment_id)

    def _require(self, payment_id: str) -> dict:
        payment = self.payments.get(payment_id)
        if payment is None:
            raise PaymentError(404, "payment not found")
        return payment

    def _view(self, payment_id: str) -> dict:
        payment = self.payments[payment_id]
        ledger = [entry for entry in self.ledger if entry["payment_id"] == payment_id]
        return {
            "id": payment["id"],
            "amount": payment["amount"],
            "currency": payment["currency"],
            "status": payment["status"],
            "ledger": ledger,
        }
