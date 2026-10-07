"""Single-line JSON events kept on the in-memory store."""

from datetime import datetime, timezone


def log_event(sink: list[dict], event: str, **fields: object) -> dict:
    record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event,
        **fields,
    }
    sink.append(record)
    return record
