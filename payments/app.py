"""HTTP routes for the payments API."""

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from payments.store import PaymentError, Store


class CreatePayment(BaseModel):
    amount: int = Field(description="Amount in integer cents")
    currency: str


# Build the FastAPI app with a fresh in-memory store.
def create_app() -> FastAPI:
    app = FastAPI(title="Ledger payments")
    app.state.store = Store()

    @app.post("/v1/payments", status_code=201)
    # Create a payment from an amount in integer cents.
    def create_payment(body: CreatePayment) -> dict:
        return _call(app.state.store.create_payment, body.amount, body.currency)

    @app.post("/v1/payments/{payment_id}/authorize")
    # Authorize a payment that is still in created status.
    def authorize(payment_id: str) -> dict:
        return _call(app.state.store.authorize, payment_id)

    @app.post("/v1/payments/{payment_id}/capture")
    # Capture a payment. The timeout header forces the seeded 504 path.
    def capture(
        payment_id: str,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        simulate_gateway_timeout: str | None = Header(default=None, alias="X-Simulate-Gateway-Timeout"),
    ) -> dict:
        body, status_code = _call(
            app.state.store.capture,
            payment_id,
            idempotency_key,
            _timeout_requested(simulate_gateway_timeout),
        )
        # Non-200 capture bodies are returned as HTTP errors, including the seeded 504.
        if status_code != 200:
            raise HTTPException(status_code=status_code, detail=body)
        return body

    @app.post("/v1/payments/{payment_id}/refund")
    # Refund a captured payment and append a ledger credit.
    def refund(payment_id: str) -> dict:
        return _call(app.state.store.refund, payment_id)

    @app.get("/v1/payments/{payment_id}")
    # Fetch one payment and its ledger rows.
    def get_payment(payment_id: str) -> dict:
        return _call(app.state.store.get_payment, payment_id)

    return app


# Treat only the header value true as a simulated gateway timeout.
def _timeout_requested(value: str | None) -> bool:
    return (value or "").lower() == "true"


# Run a store method and turn PaymentError into an HTTP error.
def _call(fn, *args):
    try:
        return fn(*args)
    except PaymentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
