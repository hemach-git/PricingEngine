"""Business analytics computed from database rows. Nothing here is hardcoded."""
import pandas as pd

from .db import db
from .rules import margin_floor


def _q(sql: str, params=()) -> pd.DataFrame:
    with db() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def compute_kpis() -> dict:
    inv = _q("SELECT * FROM inventory")
    scr = _q("""SELECT s.id, s.scraped_at, s.is_synthetic, COUNT(o.id) AS offers
                FROM competitor_scrapes s LEFT JOIN extracted_offers o ON o.scrape_id = s.id
                GROUP BY s.id""")
    recs = _q("SELECT * FROM recommendations")
    tok = _q("SELECT COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o FROM extracted_offers").iloc[0]
    total = len(inv) + len(scr)
    if total == 0:
        return {"records": 0}
    floors = [float(margin_floor(c, m)) for c, m in zip(inv.cost_price, inv.min_margin_percent)]
    compliant = sum(1 for p, f in zip(inv.current_price, floors) if p >= f - 1e-9)
    decided = recs[recs.status.isin(["approved", "rejected"])]
    approved = recs[recs.status == "approved"]
    approval_rate = len(approved) / len(decided) if len(decided) else None
    avg_change = (((approved.proposed_price - approved.current_price) / approved.current_price) * 100).mean() \
        if len(approved) else None
    return {
        "records": total,
        "synthetic_share": float((inv.is_synthetic.sum() + scr.is_synthetic.sum()) / total),
        "skus": len(inv),
        "margin_compliance": compliant / len(inv) if len(inv) else 0.0,
        "scrapes": len(scr),
        "extraction_rate": float((scr.offers > 0).mean()) if len(scr) else 0.0,
        "pending": int((recs.status == "pending").sum()) if len(recs) else 0,
        "approval_rate": approval_rate,
        "avg_change": float(avg_change) if avg_change is not None and not pd.isna(avg_change) else None,
        "tokens_in": int(tok.i),
        "tokens_out": int(tok.o),
    }


def insights(k: dict) -> list[str]:
    if k.get("records", 0) == 0:
        return ["No data yet. Run `python -m data.seed` to create the synthetic demo."]
    lines = []
    if k["pending"]:
        lines.append(f"{k['pending']} price change(s) are waiting for a human decision.")
    gap = k["skus"] - round(k["margin_compliance"] * k["skus"])
    lines.append("Every SKU is at or above its margin floor." if gap == 0
                 else f"{gap} SKU(s) are below their margin floor. Review them first.")
    lines.append(f"{k['extraction_rate']:.0%} of competitor snippets produced a usable price; "
                 "the rest need a person to check the source text.")
    if k["approval_rate"] is not None:
        lines.append(f"Reviewers approved {k['approval_rate']:.0%} of decided recommendations.")
    lines.append(f"{k['synthetic_share']:.0%} of records are synthetic demo data; live records are labelled as measured.")
    lines.append(f"LLM tokens used: {k['tokens_in'] + k['tokens_out']:,}." if k["tokens_in"] + k["tokens_out"]
                 else "No LLM tokens used: extraction ran in offline regex mode.")
    return lines


def daily_extraction_trend() -> pd.DataFrame:
    scr = _q("""SELECT substr(s.scraped_at, 1, 10) AS day, s.id, COUNT(o.id) AS offers
                FROM competitor_scrapes s LEFT JOIN extracted_offers o ON o.scrape_id = s.id
                GROUP BY s.id""")
    if scr.empty:
        return scr
    out = scr.assign(ok=scr.offers > 0).groupby("day", as_index=False).agg(
        success_rate=("ok", "mean"), scrapes=("id", "count"))
    return out.sort_values("day")


def status_counts() -> pd.DataFrame:
    return _q("SELECT status, COUNT(*) AS count FROM recommendations GROUP BY status ORDER BY status")


def recommendations_frame() -> pd.DataFrame:
    return _q("""SELECT r.id, r.run_id, r.sku, i.title, r.current_price, r.competitor_low, r.floor_price,
                        r.proposed_price, r.action, r.status, r.reason, r.flags, r.reviewer, r.review_note,
                        r.created_at, r.decided_at
                 FROM recommendations r JOIN inventory i ON i.sku = r.sku ORDER BY r.id DESC""")


def audit_frame(limit: int = 2000) -> pd.DataFrame:
    return _q("SELECT id, ts, request_id, actor, event, sku, recommendation_id, details "
              "FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))


def sku_options() -> pd.DataFrame:
    return _q("SELECT sku, title FROM inventory ORDER BY sku")
