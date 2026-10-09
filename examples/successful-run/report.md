# Remediation report: INC-1042

## Status

Remediation succeeded. The new test failed on the original code, the patch applied, and the full suite passed.

## Incident

- Service: payments
- Window: 2026-04-02T14:10:00Z/2026-04-02T14:12:00Z
- Summary: Customers are debited twice when capture is retried after a gateway timeout.
- Symptom: Two ledger debits exist for one capture idempotency key. The first attempt returned 504 and the retry returned 200.
- Routes: POST /v1/payments/{id}/capture

## Root cause

- Hypothesis: Capture debits the ledger before handling a simulated gateway timeout, then returns 504 without storing the Idempotency-Key or moving the payment out of authorized. A retry with the same key misses the idempotency record and appends a second debit.
- Confidence: high

## Evidence

- Log: ledger_debit led_70c69e64 then gateway_timeout 504, then a second capture_started and ledger_debit led_48c45974 for idem_inc_1042 before idempotency_stored.
- store.py: ledger.append and ledger_debit run before the simulate_timeout branch, which returns 504 without writing self.idempotency or setting status to captured.
- store.py comment: timeout path returns before the idempotency record is stored and leaves the payment authorized so a retry debits again.

## Regression test before the fix

Exit code 1.

```text
exit_code: 1
F                                                                        [100%]
=================================== FAILURES ===================================
____ test_capture_retry_after_gateway_timeout_does_not_append_second_debit _____

    def test_capture_retry_after_gateway_timeout_does_not_append_second_debit():
        client = TestClient(create_app())
    
        created = client.post("/v1/payments", json={"amount": 2500, "currency": "usd"})
        assert created.status_code == 201
        payment_id = created.json()["id"]
    
        authorized = client.post(f"/v1/payments/{payment_id}/authorize")
        assert authorized.status_code == 200
    
        idempotency_key = "idem_inc_1042"
        first = client.post(
            f"/v1/payments/{payment_id}/capture",
            headers={
                "Idempotency-Key": idempotency_key,
                "X-Simulate-Gateway-Timeout": "true",
            },
        )
        assert first.status_code == 504
        assert first.json() == {
            "detail": {"error": "gateway_timeout", "payment_id": payment_id}
        }
    
        second = client.post(
            f"/v1/payments/{payment_id}/capture",
            headers={"Idempotency-Key": idempotency_key},
        )
>       assert second.status_code == first.status_code
E       assert 200 == 504
E        +  where 200 = <Response [200 OK]>.status_code
E        +  and   504 = <Response [504 Gateway Timeout]>.status_code

tests/test_inc_1042.py:33: AssertionError
=============================== warnings summary ===============================
../../../..site-packages/fastapi/testclient.py:1
... truncated ...
```

## Files changed

Changed files: payments/store.py.

Diff summary: 15 lines added, 7 lines removed.

```diff
--- a/payments/store.py
+++ b/payments/store.py
@@ -1,4 +1,4 @@
-"""Ledger and idempotency. The capture timeout path is the seeded bug."""
+"""Ledger and idempotency for payment capture."""
 
 from uuid import uuid4
 
@@ -61,7 +61,7 @@
                 payment_id=payment_id,
                 idempotency_key=idempotency_key,
             )
-            return stored, 200
+            return stored["body"], stored["status_code"]
 
         if payment["status"] != "authorized":
             raise PaymentError(409, f"cannot capture a payment in status {payment['status']}")
@@ -82,21 +82,29 @@
             idempotency_key=idempotency_key,
         )
 
-        # Bug: the timeout path returns before the idempotency record is stored,
-        # and leaves the payment authorized so a retry debits again.
         if simulate_timeout:
+            body = {"error": "gateway_timeout", "payment_id": payment_id}
+            status_code = 504
+            self.idempotency[idempotency_key] = {"body": body, "status_code": status_code}
             log_event(
                 self.events,
                 "gateway_timeout",
                 payment_id=payment_id,
                 idempotency_key=idempotency_key,
-                status_code=504,
+                status_code=status_code,
             )
-            return {"error": "gateway_timeout", "payment_id": payment_id}, 504
+            log_event(
+                self.events,
+                "idempotency_stored",
+                payment_id=payment_id,
+                idempotency_key=idempotency_key,
+                status_code=status_code,
+            )
+            return body, status_code
 
         payment["status"] = "captured"
         body = self._view(payment_id)
-        self.idempotency[idempotency_key] = body
+        self.idempotency[idempotency_key] = {"body": body, "status_code": 200}
         log_event(
             self.events,
             "idempotency_stored",
```

## Full suite after the fix

Exit code 0.

```text
exit_code: 0
.....................................                                    [100%]
=============================== warnings summary ===============================
../../../..site-packages/fastapi/testclient.py:1
  site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
37 passed, 1 warning in 1.39s
```

## Blast radius

Capture on POST /v1/payments/{id}/capture appends a ledger debit and moves customer funds. The change is limited to payments/store.py, which decides whether a retried capture writes another debit. The refund function was not edited. Authorize and fetch do not move money. A wrong idempotency replay can return the wrong status or drop a debit, so this is a payments-path change.

## Rollback

Roll back by restoring the pre-patch payments/store.py. This prototype keeps the ledger in memory, so there is no migration to undo. Restarting the process drops idempotency records from the failed run.

## Approval

A person must approve this change before it ships. This report does not approve, merge, or deploy it.
