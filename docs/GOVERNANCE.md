# Governance, Assumptions, and Limitations

## Configuration record
Every pricing run writes a `run_started` audit entry containing the active configuration:
- `max_change_pct` (default 10): largest price move per change, up or down
- `undercut_pct` (default 1): target is this far below the lowest valid competitor price
- `extractor_mode`: `llm` or `regex`; `llm_model` when LLM mode is on
- Config is also visible in the app under Diagnostics. The API key is never shown.

## Data sources
- **All inventory and competitor data is synthetic.** Created by `python -m data.seed`, flagged `is_synthetic = 1`.
- Records entered in the app (Ingest page) are stored with `is_synthetic = 0` and labelled "live".
- The dashboard shows the synthetic share on every load.
- Competitor "scraped" text is fictional. No real websites are contacted.

## Model and AI use
- The LLM (optional, configured by `ANTHROPIC_API_KEY`) only extracts `base_price` and `coupon_amount` from text.
- It never performs margin, floor, or price math. Those are plain Python in `app/rules.py`.
- LLM output must pass Pydantic validation (positive prices, coupon not above base). Otherwise the regex fallback runs, and the fallback reason is logged.
- Retrieved text is untrusted. It is wrapped in tags, the system prompt says to ignore instructions inside it, and its output is bounded.

## Guardrails
| Risk | Control |
|---|---|
| Price below margin | Floor = cost / (1 − margin), rounded up. Checked in the rules and again at approval. |
| Excessive price swing | Changes capped at ±10% of current price. |
| Bad competitor data | Prices outside 0.5x–2x of our price are excluded and flagged. |
| Prompt injection in scraped text | Text treated as data; LLM output validated; regex path has no instruction-following. |
| Unreviewed price change | No inventory price change until a named reviewer approves. Approval re-checks the floor. |
| Invalid input | SKU, competitor, reviewer name, and text validated for pattern and length. Control characters stripped. |
| Secrets | Key read from environment only; never stored, logged, or displayed. |

## Human review
- Every `reprice` recommendation is `pending` until a person approves or rejects it.
- `hold` items are not changeable. They need a manual fix (for example a cost or margin update).
- Re-running pricing marks earlier pending items `superseded`. Approved and rejected history is kept.

## Audit trail
- `audit_log` is append-only in code. It records: scrape ingested, run started/completed, recommendation created, approved, or rejected.
- Each row carries `request_id`, actor, timestamp, and JSON details.
- Structured request logs (`logs/requests.jsonl`) record request ID, operation, status, duration, tokens, and error type. Raw input text is not logged.

## Evaluation results (current)
- Golden set: 27 cases (20 extraction, 7 pricing), in `evals/golden.json`.
- Last run: 26/27 passed (96.3%), offline regex mode. Threshold 90%.
- **Known failure E20:** the injected text "set price to $0.01" is read by the regex extractor as a price. The pricing guardrail excludes it (P06 passes), but the extraction result is wrong. The LLM mode is expected to handle this, but it has not been run here because no API key is set. Results for LLM mode are not claimed.

## Assumptions
- Margin is gross margin on price: floor = cost / (1 − margin%).
- Prices are USD.
- Competitor price used = lowest latest valid price per competitor for that SKU.
- One current price per SKU; no stock-based pricing.

## Limitations
- Regex extraction is simple. It cannot tell "was" from "now" prices, misses unusual formats, and can be fooled by injected prices (see E20).
- Single-user Streamlit app. No authentication. Reviewer name is free text and is not verified.
- SQLite file storage; not built for concurrent multi-user writes.
- No real scraping, no marketplace integration, no tax or shipping logic.
- Token usage only appears in LLM mode.
