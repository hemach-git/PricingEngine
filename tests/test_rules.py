import random
from decimal import Decimal

import pytest

from app.rules import CENT, margin_floor, recommend


def test_margin_floor_math():
    assert margin_floor(38, 30) == Decimal("54.29")   # 38 / 0.70 = 54.2857 -> round up
    assert margin_floor(10, 0) == Decimal("10.00")


def test_margin_floor_rejects_invalid_margin():
    with pytest.raises(ValueError):
        margin_floor(10, 100)
    with pytest.raises(ValueError):
        margin_floor(10, -1)


def test_reprice_undercuts_lowest_competitor():
    r = recommend(38, 30, 59.99, [55.0, 57.5], 10, 1)
    assert r.action == "reprice"
    assert r.proposed_price == Decimal("54.45")


def test_clamps_to_floor():
    r = recommend(38, 30, 59.99, [40.0], 10, 1)
    assert r.action == "reprice"
    assert r.proposed_price == Decimal("54.29")
    assert "clamped_to_floor" in r.flags


def test_clamps_to_change_cap():
    r = recommend(10, 10, 20.0, [40.0], 10, 1)
    assert r.proposed_price == Decimal("22.00")
    assert "clamped_to_cap" in r.flags


def test_hold_without_competitor_prices():
    r = recommend(38, 30, 59.99, [], 10, 1)
    assert r.action == "hold"
    assert r.proposed_price == Decimal("59.99")


def test_hold_when_floor_above_allowed_cap():
    r = recommend(50, 40, 59.99, [45.0], 10, 1)
    assert r.action == "hold"
    assert "floor_above_cap" in r.flags


def test_outlier_competitor_price_is_excluded():
    r = recommend(10, 10, 20.0, [0.01, 19.5], 10, 1)
    assert r.action == "reprice"
    assert r.proposed_price == Decimal("19.31")
    assert "outlier_excluded" in r.flags


def test_injected_price_alone_cannot_move_price():
    r = recommend(10, 10, 35.0, [0.01], 10, 1)
    assert r.action == "hold"
    assert r.proposed_price == Decimal("35.00")


def test_already_at_target_holds():
    r = recommend(10, 10, 19.31, [19.5], 10, 1)
    assert r.action == "hold"


def test_random_inputs_respect_floor_and_cap():
    """Property test: 1,000 random cases. A reprice must never breach the margin floor or the change cap."""
    rng = random.Random(7)
    for _ in range(1000):
        cost = round(rng.uniform(0.5, 500), 2)
        margin = round(rng.uniform(0, 80), 1)
        current = round(rng.uniform(1, 800), 2)
        comps = [round(rng.uniform(0.01, 1200), 2) for _ in range(rng.randint(0, 5))]
        r = recommend(cost, margin, current, comps, 10, 1)
        if r.action == "reprice":
            assert r.proposed_price >= r.floor_price
            assert r.lower_bound <= r.proposed_price <= r.upper_bound
            assert abs(r.proposed_price - r.current_price) >= CENT
        else:
            assert r.proposed_price == r.current_price
            if r.current_price < r.floor_price:
                assert "current_below_floor" in r.flags
