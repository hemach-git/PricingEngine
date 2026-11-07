import pytest

from app.db import db
from app.extract import InputError
from app.observability import read_recent
from app.service import ingest_scrape, review_recommendation, run_pricing


def add_sku(sku="SKU-T1", cost=38, margin=30, price=59.99):
    with db() as conn:
        conn.execute(
            "INSERT INTO inventory (sku, title, cost_price, min_margin_percent, current_price, stock_count, is_synthetic, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (sku, "Test item", cost, margin, price, 10, 1, "2026-01-01T00:00:00+00:00"),
        )


def scalar(sql, params=()):
    with db() as conn:
        return conn.execute(sql, params).fetchone()[0]


def test_ingest_extracts_and_audits():
    add_sku()
    r = ingest_scrape("SKU-T1", "ShopNorth", "Item X on sale for $42.99 with $5 coupon", request_id="abc123")
    assert r["status"] == "extracted"
    assert r["offer"]["final_price"] == 37.99
    assert r["request_id"] == "abc123"
    assert scalar("SELECT COUNT(*) FROM audit_log WHERE request_id = 'abc123'") >= 1


def test_unknown_sku_rejected():
    add_sku()
    with pytest.raises(InputError):
        ingest_scrape("SKU-NOPE", "ShopNorth", "$10")


def test_invalid_competitor_name_rejected():
    add_sku()
    with pytest.raises(InputError):
        ingest_scrape("SKU-T1", "<script>alert(1)</script>", "$10")


def test_unparsed_text_is_stored_but_never_priced():
    add_sku()
    r = ingest_scrape("SKU-T1", "ShopNorth", "Sold out")
    assert r["status"] == "unparsed"
    assert scalar("SELECT COUNT(*) FROM extracted_offers") == 0


def test_run_creates_pending_and_price_unchanged_until_approval():
    add_sku()
    ingest_scrape("SKU-T1", "ShopNorth", "Deal $55.00 today")
    out = run_pricing(request_id="run1")
    assert out["counts"]["reprice"] == 1
    with db() as conn:
        rec = conn.execute("SELECT * FROM recommendations WHERE sku = 'SKU-T1'").fetchone()
        price = conn.execute("SELECT current_price FROM inventory WHERE sku = 'SKU-T1'").fetchone()[0]
    assert rec["status"] == "pending"
    assert rec["proposed_price"] == 54.45
    assert price == 59.99


def test_approve_applies_price_and_writes_audit():
    add_sku()
    ingest_scrape("SKU-T1", "ShopNorth", "Deal $55.00 today")
    run_pricing()
    with db() as conn:
        rid = conn.execute("SELECT id FROM recommendations WHERE status = 'pending'").fetchone()[0]
    review_recommendation(rid, "approve", "Jane Reviewer", "ok")
    assert scalar("SELECT current_price FROM inventory WHERE sku = 'SKU-T1'") == 54.45
    assert scalar("SELECT COUNT(*) FROM audit_log WHERE event = 'recommendation_approved'") == 1


def test_reject_leaves_price_unchanged():
    add_sku()
    ingest_scrape("SKU-T1", "ShopNorth", "Deal $55.00 today")
    run_pricing()
    with db() as conn:
        rid = conn.execute("SELECT id FROM recommendations WHERE status = 'pending'").fetchone()[0]
    review_recommendation(rid, "reject", "Jane Reviewer")
    assert scalar("SELECT current_price FROM inventory WHERE sku = 'SKU-T1'") == 59.99


def test_decided_recommendation_cannot_be_reviewed_twice():
    add_sku()
    ingest_scrape("SKU-T1", "ShopNorth", "Deal $55.00 today")
    run_pricing()
    with db() as conn:
        rid = conn.execute("SELECT id FROM recommendations WHERE status = 'pending'").fetchone()[0]
    review_recommendation(rid, "approve", "Jane Reviewer")
    with pytest.raises(InputError):
        review_recommendation(rid, "reject", "Jane Reviewer")


def test_rerun_supersedes_old_pending():
    add_sku()
    ingest_scrape("SKU-T1", "ShopNorth", "Deal $55.00 today")
    run_pricing()
    run_pricing()
    assert scalar("SELECT COUNT(*) FROM recommendations WHERE status = 'superseded'") == 1
    assert scalar("SELECT COUNT(*) FROM recommendations WHERE status = 'pending'") == 1


def test_request_logs_have_id_duration_and_status():
    add_sku()
    ingest_scrape("SKU-T1", "ShopNorth", "Mug $9", request_id="log123")
    with pytest.raises(InputError):
        ingest_scrape("SKU-NOPE", "ShopNorth", "Mug $9", request_id="log456")
    logs = {r["request_id"]: r for r in read_recent()}
    assert logs["log123"]["status"] == "ok"
    assert logs["log123"]["outcome"] == "extracted"
    assert logs["log123"]["duration_ms"] >= 0
    assert logs["log456"]["status"] == "error"
    assert "Mug" not in str(logs)  # raw input text is never logged
