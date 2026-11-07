import json
from decimal import Decimal

import pytest

from app.config import get_settings
from app.extract import InputError, NoPriceFound, ParsedOffer, clean_input, extract_offer, regex_extract


def test_coupon_example_from_brief():
    o = regex_extract("Item X on sale for $42.99 with $5 coupon")
    assert o.base_price == Decimal("42.99")
    assert o.coupon_amount == Decimal("5")
    assert o.final_price == Decimal("37.99")


def test_percent_coupon():
    o = regex_extract("Desk Lamp $35 - save 10% off today")
    assert o.coupon_amount == Decimal("3.50")


def test_thousands_separator():
    assert regex_extract("Laptop Stand $1,249.99 free returns").base_price == Decimal("1249.99")


def test_no_price_raises():
    with pytest.raises(NoPriceFound):
        regex_extract("Sold out, check back soon")


def test_zero_price_is_not_a_price():
    with pytest.raises(NoPriceFound):
        regex_extract("Price: $0.00 clearance")


def test_coupon_above_base_is_rejected():
    with pytest.raises(ValueError):
        regex_extract("Headphones $150 with $200 coupon")


def test_clean_input_rejects_empty_and_too_long():
    with pytest.raises(InputError):
        clean_input("   ", 500)
    with pytest.raises(InputError):
        clean_input("x" * 501, 500)
    with pytest.raises(InputError):
        clean_input(None, 500)


def test_clean_input_strips_control_characters():
    assert clean_input("Mug\x00\x07 $9", 500) == "Mug $9"


def test_parsed_offer_rejects_negative_price():
    with pytest.raises(ValueError):
        ParsedOffer(base_price=Decimal("-1"), coupon_amount=Decimal("0"), confidence=0.5)


def test_offline_mode_uses_regex_and_reports_no_tokens():
    result = extract_offer("Mug $9.00", get_settings())
    assert result["extractor"] == "regex"
    assert result["input_tokens"] is None


def test_llm_failure_falls_back_to_regex(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    def boom(*_a, **_k):
        raise RuntimeError("simulated outage")

    monkeypatch.setattr("app.extract.llm_extract", boom)
    result = extract_offer("Mug $9.00", get_settings())
    assert result["extractor"] == "regex"
    assert result["fallback_reason"] == "RuntimeError"


def test_llm_output_is_validated_not_trusted(monkeypatch):
    """A model reply with a coupon above the base price must not become a stored price."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            body = {"base_price": 10, "coupon_amount": 50, "confidence": 0.9}
            return {"content": [{"type": "text", "text": json.dumps(body)}], "usage": {}}

    monkeypatch.setattr("app.extract.httpx.post", lambda *a, **k: FakeResp())
    result = extract_offer("Mug $10", get_settings())  # invalid LLM output -> falls back to regex
    assert result["extractor"] == "regex"
    assert result["offer"].base_price == Decimal("10")
