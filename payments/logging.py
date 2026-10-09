"""Single-line JSON events kept on the in-memory store."""

from datetime import datetime, timezone


# Append one timestamped JSON event to the in-memory log.
def log_event(sink: list[dict], event: str, **fields: object) -> dict:
    # One dict per event so reproduce can write a JSONL incident log.
    record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event,
        **fields,
    }
    sink.append(record)
    return record
