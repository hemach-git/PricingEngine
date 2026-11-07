"""Append-only audit trail. Every price-affecting event writes a row here."""
import json
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(conn, *, actor, event, request_id=None, sku=None, recommendation_id=None, details=None) -> None:
    conn.execute(
        "INSERT INTO audit_log (ts, request_id, actor, event, sku, recommendation_id, details) VALUES (?,?,?,?,?,?,?)",
        (utc_now(), request_id, actor, event, sku, recommendation_id,
         json.dumps(details or {}, default=str, sort_keys=True)),
    )
