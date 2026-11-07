"""Structured request logs: one JSON line per operation with request_id, duration, status, and tokens.
Raw input text is never logged."""
import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import get_settings


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def _write(record: dict) -> None:
    path = get_settings().log_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


@contextmanager
def track(operation: str, request_id: str | None = None):
    ctx = {"request_id": request_id or new_request_id(), "operation": operation, "status": "ok",
           "outcome": None, "error": None, "input_tokens": None, "output_tokens": None}
    start = time.perf_counter()
    try:
        yield ctx
    except Exception as exc:
        ctx["status"] = "error"
        ctx["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        raise
    finally:
        ctx["duration_ms"] = round((time.perf_counter() - start) * 1000, 1)
        ctx["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            _write(ctx)
        except OSError:
            pass  # logging must never break a request


def read_recent(limit: int = 500) -> list[dict]:
    path = get_settings().log_path
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
