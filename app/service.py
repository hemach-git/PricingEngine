"""Workflows: ingest -> extract -> recommend -> human review -> audit. Every write is audited."""
import json
import re
import uuid
from decimal import Decimal

from .audit import record, utc_now
from .config import get_settings
from .db import db
from .extract import CONTROL_CHARS, InputError, NoPriceFound, clean_input, extract_offer
from .observability import track
from .rules import MarginViolation, assert_margin_safe, recommend

SKU_RE = re.compile(r"^[A-Za-z0-9-]{1,32}$")
NAME_RE = re.compile(r"^[A-Za-z0-9 .&'-]{1,60}$")


def _check(value, pattern, label) -> str:
    value = (value or "").strip()
    if not pattern.match(value):
        raise InputError(f"invalid {label}")
    return value


def insert_scrape(conn, sku, competitor, raw_text, scraped_at, is_synthetic=0) -> int:
    cur = conn.execute(
        "INSERT INTO competitor_scrapes (sku, competitor, raw_text, scraped_at, is_synthetic) VALUES (?,?,?,?,?)",
        (sku, competitor, raw_text, scraped_at, int(is_synthetic)),
    )
    return cur.lastrowid


def store_extraction(conn, scrape_id, sku, competitor, result, created_at) -> None:
    o = result["offer"]
    conn.execute(
        """INSERT INTO extracted_offers (scrape_id, sku, competitor, base_price, coupon_amount, final_price,
           extractor, confidence, model, input_tokens, output_tokens, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (scrape_id, sku, competitor, float(o.base_price), float(o.coupon_amount), float(o.final_price),
         result["extractor"], o.confidence, result.get("model"), result.get("input_tokens"),
         result.get("output_tokens"), created_at),
    )


def ingest_scrape(sku, competitor, raw_text, actor="analyst", request_id=None,
                  scraped_at=None, is_synthetic=0) -> dict:
    settings = get_settings()
    with track("ingest_scrape", request_id) as ctx:
        sku = _check(sku, SKU_RE, "SKU")
        competitor = _check(competitor, NAME_RE, "competitor name")
        text = clean_input(raw_text, settings.max_raw_text_len)
        ts = scraped_at or utc_now()
        with db() as conn:
            if not conn.execute("SELECT 1 FROM inventory WHERE sku = ?", (sku,)).fetchone():
                raise InputError("unknown SKU")
            scrape_id = insert_scrape(conn, sku, competitor, text, ts, is_synthetic)
            offer_out, status, result = None, "extracted", None
            try:
                result = extract_offer(text, settings)
                store_extraction(conn, scrape_id, sku, competitor, result, ts)
                o = result["offer"]
                offer_out = {"base_price": float(o.base_price), "coupon_amount": float(o.coupon_amount),
                             "final_price": float(o.final_price), "confidence": o.confidence}
                ctx["input_tokens"] = result.get("input_tokens")
                ctx["output_tokens"] = result.get("output_tokens")
            except NoPriceFound:
                status = "unparsed"
            except ValueError:  # extracted values failed validation; never stored as prices
                status = "rejected"
            ctx["outcome"] = status
            record(conn, actor=actor, event="scrape_ingested", request_id=ctx["request_id"], sku=sku,
                   details={"scrape_id": scrape_id, "status": status,
                            "extractor": result["extractor"] if result else None,
                            "fallback_reason": result.get("fallback_reason") if result else None,
                            "input_chars": len(text)})
        return {"request_id": ctx["request_id"], "scrape_id": scrape_id, "status": status,
                "extractor": result["extractor"] if result else None, "offer": offer_out}


def run_pricing(actor="pricing-engine", request_id=None) -> dict:
    s = get_settings()
    with track("pricing_run", request_id) as ctx:
        run_id = uuid.uuid4().hex[:12]
        config = {"max_change_pct": s.max_change_pct, "undercut_pct": s.undercut_pct,
                  "extractor_mode": "llm" if s.llm_enabled else "regex",
                  "llm_model": s.llm_model if s.llm_enabled else None}
        counts = {"reprice": 0, "hold": 0}
        with db() as conn:
            record(conn, actor=actor, event="run_started", request_id=ctx["request_id"],
                   details={"run_id": run_id, "config": config})
            inv = conn.execute("SELECT sku, cost_price, min_margin_percent, current_price FROM inventory ORDER BY sku").fetchall()
            latest = conn.execute(
                """SELECT o.sku, o.final_price FROM extracted_offers o
                   JOIN (SELECT sku, competitor, MAX(id) AS mid FROM extracted_offers GROUP BY sku, competitor) m
                     ON o.id = m.mid"""
            ).fetchall()
            by_sku: dict[str, list[float]] = {}
            for r in latest:
                by_sku.setdefault(r["sku"], []).append(r["final_price"])
            for row in inv:
                rec = recommend(row["cost_price"], row["min_margin_percent"], row["current_price"],
                                [Decimal(str(p)) for p in by_sku.get(row["sku"], [])],
                                s.max_change_pct, s.undercut_pct)
                conn.execute("UPDATE recommendations SET status = 'superseded' WHERE sku = ? AND status = 'pending'",
                             (row["sku"],))
                status = "pending" if rec.action == "reprice" else "hold"
                cur = conn.execute(
                    """INSERT INTO recommendations (run_id, sku, current_price, competitor_low, floor_price,
                       proposed_price, action, status, reason, flags, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, row["sku"], float(rec.current_price),
                     float(rec.competitor_low) if rec.competitor_low is not None else None,
                     float(rec.floor_price), float(rec.proposed_price), rec.action, status,
                     rec.reason, json.dumps(list(rec.flags)), utc_now()),
                )
                counts[rec.action] += 1
                record(conn, actor=actor, event="recommendation_created", request_id=ctx["request_id"],
                       sku=row["sku"], recommendation_id=cur.lastrowid,
                       details={"action": rec.action, "current": float(rec.current_price),
                                "proposed": float(rec.proposed_price), "floor": float(rec.floor_price),
                                "flags": list(rec.flags)})
            record(conn, actor=actor, event="run_completed", request_id=ctx["request_id"],
                   details={"run_id": run_id, "counts": counts})
        ctx["outcome"] = "completed"
        return {"run_id": run_id, "counts": counts, "request_id": ctx["request_id"]}


def review_recommendation(rec_id, decision, reviewer, note="", request_id=None) -> dict:
    with track("review", request_id) as ctx:
        if decision not in ("approve", "reject"):
            raise InputError("decision must be approve or reject")
        reviewer = _check(reviewer, NAME_RE, "reviewer name")
        note = CONTROL_CHARS.sub(" ", note or "").strip()[:300]
        with db() as conn:
            rec = conn.execute("SELECT * FROM recommendations WHERE id = ?", (rec_id,)).fetchone()
            if rec is None:
                raise InputError("recommendation not found")
            if rec["status"] != "pending":
                raise InputError(f"recommendation is {rec['status']}; only pending items can be reviewed")
            now = utc_now()
            if decision == "approve":
                assert_margin_safe(rec["proposed_price"], rec["floor_price"])  # re-check at approval time
                conn.execute("UPDATE inventory SET current_price = ?, updated_at = ? WHERE sku = ?",
                             (rec["proposed_price"], now, rec["sku"]))
                new_status = "approved"
            else:
                new_status = "rejected"
            conn.execute("UPDATE recommendations SET status = ?, reviewer = ?, review_note = ?, decided_at = ? WHERE id = ?",
                         (new_status, reviewer, note, now, rec_id))
            record(conn, actor=reviewer, event=f"recommendation_{new_status}", request_id=ctx["request_id"],
                   sku=rec["sku"], recommendation_id=rec_id,
                   details={"proposed": rec["proposed_price"], "note": note})
        ctx["outcome"] = new_status
        return {"recommendation_id": rec_id, "status": new_status, "request_id": ctx["request_id"]}
