# Dynamic Competitive Pricing & Repricing Engine

A local Streamlit app that turns messy competitor listings into structured prices, proposes bounded price changes, checks margin floors, and requires human approval before any change. **All data is synthetic.**

## Architecture
```
Scraped text ─► clean & validate ─► extract (LLM optional, regex fallback) ─► offers table
                                                                                   │
inventory (cost, margin, price) ─────────────────────────────► rules.py (pure math) ◄┘
                                                                  │
                                       recommendation (pending) ◄─┘
                                                │
                              Reviewer approves / rejects (Streamlit)
                                                │
                    inventory price updated only on approval ─► audit_log
```
- **LLM role:** extract `base_price` and `coupon_amount` only. Margin and price math is deterministic code.
- **Stack:** Python, Streamlit (UI), SQLite (storage), Pydantic (validation), httpx (LLM call), pytest.
- **Layout:**
  - `app/`: config, db, rules, extract, service, audit, observability, analytics
  - `ui/streamlit_app.py`: Dashboard, Ingest & Extract, Review Queue, Audit Trail, Diagnostics
  - `data/seed.py`: synthetic demo data generator
  - `evals/`: golden dataset and evaluation runner
  - `tests/`: unit, workflow, and UI smoke tests
  - `docs/GOVERNANCE.md`: assumptions, guardrails, limitations

## Setup
Requires Python 3.10+.
```bash
pip install -r requirements.txt
python -m data.seed --reset        # create synthetic demo database
streamlit run ui/streamlit_app.py  # open http://localhost:8501
```
No API key is needed. Without one, extraction runs offline with regex.

To use the LLM extractor, set the key as an environment variable (never in code or git):
```bash
set ANTHROPIC_API_KEY=your-key-here      # Windows cmd
export ANTHROPIC_API_KEY=your-key-here   # bash
```
Optional: `LLM_MODEL` (default `claude-haiku-5-5`), `PRICING_DB`, `PRICING_LOG`. See `.env.example`.

## Tests and evaluations
```bash
python -m pytest -q                # 34 automated tests (rules, extraction, workflow, UI smoke)
python -m evals.run_evals          # golden set, offline regex mode; writes evals/results.json
python -m evals.run_evals --llm    # LLM mode (needs ANTHROPIC_API_KEY)
```
Latest results (offline regex mode):
- Tests: 34 of 34 passed.
- Golden set: 26 of 27 passed (96.3%, threshold 90%). The failing case (E20) is an injected "$0.01" that the regex reads as a price. The pricing guardrail still blocks it. Details in `docs/GOVERNANCE.md`.

## Using the app
1. **Dashboard:** KPIs, plain-English insights, extraction trend, drill-down table with CSV export.
2. **Ingest & Extract:** choose a SKU, paste scraped text, and see the extracted price with evidence. Then run pricing.
3. **Review Queue:** read each proposal (current price, competitor low, floor, reason, flags) and approve or reject with a reviewer name.
4. **Audit Trail:** every change, filterable, with CSV export.
5. **Diagnostics:** request logs (duration, errors, tokens), active configuration, last evaluation.

## Key rules
- Target = lowest valid competitor price − 1%.
- Proposed price is capped at ±10% of current price and never below the margin floor (cost ÷ (1 − margin)).
- If the floor is above the highest allowed price, the SKU is held for manual review.
- Competitor prices outside 0.5x–2x of our price are ignored.
- No price changes without human approval.

## Limitations
See `docs/GOVERNANCE.md`. Summary: synthetic data only; regex extraction is basic; single-user app with no authentication; SQLite storage.
