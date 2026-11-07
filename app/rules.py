"""Deterministic pricing math. The LLM never touches these numbers."""
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
OUTLIER_LOW = Decimal("0.5")   # competitor prices outside 0.5x..2x of our price are treated as bad data
OUTLIER_HIGH = Decimal("2.0")


class MarginViolation(RuntimeError):
    """A price would fall below the margin floor. Safety net; the rules should make this unreachable."""


def _d(x) -> Decimal:
    return Decimal(str(x))


def money(x) -> Decimal:
    return _d(x).quantize(CENT, rounding=ROUND_HALF_UP)


def margin_floor(cost_price, min_margin_percent) -> Decimal:
    """Lowest price that keeps gross margin >= min_margin_percent: price = cost / (1 - m).
    Rounded UP to the cent so the floor is never below the target margin."""
    m = _d(min_margin_percent) / HUNDRED
    if not 0 <= m < 1:
        raise ValueError("min_margin_percent must be >= 0 and < 100")
    return (_d(cost_price) / (1 - m)).quantize(CENT, rounding=ROUND_CEILING)


def assert_margin_safe(price, floor) -> None:
    if _d(price) < _d(floor):
        raise MarginViolation(f"price {price} is below margin floor {floor}")


@dataclass(frozen=True)
class Recommendation:
    action: str                 # "reprice" or "hold"
    current_price: Decimal
    floor_price: Decimal
    lower_bound: Decimal        # effective lower limit = max(margin floor, cap below current)
    upper_bound: Decimal        # cap above current
    competitor_low: Decimal | None
    target_price: Decimal | None
    proposed_price: Decimal
    reason: str
    flags: tuple[str, ...] = ()


def recommend(cost_price, min_margin_percent, current_price, competitor_prices,
              max_change_pct, undercut_pct) -> Recommendation:
    current = money(current_price)
    floor = margin_floor(cost_price, min_margin_percent)
    cap = _d(max_change_pct) / HUNDRED
    lower_cap = money(current * (1 - cap))
    upper = money(current * (1 + cap))
    lower = max(floor, lower_cap)

    flags: list[str] = []
    if current < floor:
        flags.append("current_below_floor")
    valid = [money(p) for p in competitor_prices if _d(p) > 0]
    in_band = [p for p in valid if current * OUTLIER_LOW <= p <= current * OUTLIER_HIGH]
    if len(in_band) < len(valid):
        flags.append("outlier_excluded")

    def hold(reason, low=None, target=None, extra=()):
        return Recommendation("hold", current, floor, lower, upper, low, target, current,
                              reason, tuple(flags) + tuple(extra))

    if floor > upper:
        return hold(f"Margin floor {floor} is above the highest allowed price {upper}. Manual review required.",
                    extra=("floor_above_cap",))
    if not in_band:
        return hold("No usable competitor price found; price left unchanged.")

    low = min(in_band)
    target = money(low * (1 - _d(undercut_pct) / HUNDRED))
    proposed = target
    if target < lower:
        proposed = lower
        flags.append("clamped_to_floor" if floor >= lower_cap else "clamped_to_cap")
    elif target > upper:
        proposed = upper
        flags.append("clamped_to_cap")

    assert_margin_safe(proposed, floor)
    if abs(proposed - current) < CENT:
        return hold("Current price already matches the target.", low=low, target=target)

    reason = (f"Target {target} is {undercut_pct}% under the lowest valid competitor price {low}. "
              f"Proposed {proposed} (from {current}) stays within the +/-{max_change_pct}% cap "
              f"and at or above the margin floor {floor}.")
    return Recommendation("reprice", current, floor, lower, upper, low, target, proposed,
                          reason, tuple(flags))
