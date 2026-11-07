"""Repeatable evaluation harness. Usage: python -m evals.run_evals [--llm]
Offline regex mode is the default. --llm needs ANTHROPIC_API_KEY set in the environment.
Exit code 1 when the pass rate is below the threshold."""
import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.extract import InputError, NoPriceFound, clean_input, llm_extract, regex_extract  # noqa: E402
from app.rules import recommend  # noqa: E402

THRESHOLD = 0.9
GOLDEN = ROOT / "evals" / "golden.json"
RESULTS = ROOT / "evals" / "results.json"


def run_extraction(case: dict, use_llm: bool) -> tuple[bool, dict]:
    expected = case["expected"]
    try:
        text = clean_input(case["input"], get_settings().max_raw_text_len)
        offer = llm_extract(text, get_settings())[0] if use_llm else regex_extract(text)
        got = {"base_price": float(offer.base_price), "coupon_amount": float(offer.coupon_amount)}
    except NoPriceFound:
        got = {"unparsed": True}
    except (InputError, ValueError):
        got = {"rejected": True}
    except Exception as exc:  # unexpected failure is a test failure, not a crash
        got = {"error": type(exc).__name__}
    if expected.get("unparsed") or expected.get("rejected"):
        return got == expected, got
    if "base_price" not in got:
        return False, got
    ok = (abs(got["base_price"] - expected["base_price"]) < 0.005
          and abs(got["coupon_amount"] - expected["coupon_amount"]) < 0.005)
    return ok, got


def run_pricing(case: dict) -> tuple[bool, dict]:
    i = case["input"]
    rec = recommend(i["cost_price"], i["min_margin_percent"], i["current_price"],
                    [Decimal(str(p)) for p in i["competitor_prices"]], 10, 1)
    got = {"action": rec.action, "proposed_price": float(rec.proposed_price), "flags": list(rec.flags)}
    exp = case["expected"]
    ok = got["action"] == exp["action"] and abs(got["proposed_price"] - exp["proposed_price"]) < 0.005
    return ok, got


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true", help="use the LLM extractor (needs ANTHROPIC_API_KEY)")
    args = parser.parse_args()
    if args.llm and not get_settings().llm_enabled:
        print("--llm requested but ANTHROPIC_API_KEY is not set. Nothing was run.")
        return 2
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    results = []
    for case in golden["extraction"]:
        ok, got = run_extraction(case, args.llm)
        results.append({"id": case["id"], "type": "extraction", "category": case["category"],
                        "passed": ok, "expected": case["expected"], "got": got})
    for case in golden["pricing"]:
        ok, got = run_pricing(case)
        results.append({"id": case["id"], "type": "pricing", "category": case["category"],
                        "passed": ok, "expected": case["expected"], "got": got})

    total = len(results)
    passed = sum(r["passed"] for r in results)
    rate = passed / total
    verdict = "PASS" if rate >= THRESHOLD else "FAIL"
    by_cat = {}
    for r in results:
        c = by_cat.setdefault(r["category"], [0, 0])
        c[0] += r["passed"]
        c[1] += 1
    mode = "llm" if args.llm else "regex (offline)"
    print(f"Evaluation mode: {mode}")
    for r in results:
        print(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['id']} ({r['category']}) expected={r['expected']} got={r['got']}")
    print("By category: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in by_cat.items()))
    print(f"TOTAL: {passed}/{total} passed = {rate:.1%} (threshold {THRESHOLD:.0%}) -> {verdict}")
    RESULTS.write_text(json.dumps({
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": mode,
        "summary": {"total": total, "passed": passed, "pass_rate": round(rate, 4),
                    "threshold": THRESHOLD, "verdict": verdict, "by_category": by_cat},
        "cases": results}, indent=2), encoding="utf-8")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
