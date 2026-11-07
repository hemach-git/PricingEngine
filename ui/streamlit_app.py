"""Repricing Engine UI. Synthetic demo data is labelled wherever it appears."""
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app import analytics  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import db, init_db  # noqa: E402
from app.extract import InputError  # noqa: E402
from app.observability import read_recent  # noqa: E402
from app.service import ingest_scrape, review_recommendation, run_pricing  # noqa: E402

st.set_page_config(page_title="Repricing Engine", page_icon="📊", layout="wide")
st.markdown("""<style>
.block-container {padding-top: 1.5rem;}
div[data-testid="stMetricValue"] {font-size: 1.6rem;}
</style>""", unsafe_allow_html=True)

init_db()
settings = get_settings()
PAGES = ["Dashboard", "Ingest & Extract", "Review Queue", "Audit Trail", "Diagnostics"]


def money(v) -> str:
    return "—" if v is None or pd.isna(v) else f"${float(v):,.2f}"


def download(df: pd.DataFrame, name: str, key: str) -> None:
    st.download_button("Export CSV", df.to_csv(index=False), file_name=name, mime="text/csv", key=key)


def page_dashboard() -> None:
    st.header("Dashboard")
    k = analytics.compute_kpis()
    if k.get("records", 0) == 0:
        st.info("No data yet. Run `python -m data.seed` to create the synthetic demo.")
        return
    if k["synthetic_share"] > 0:
        st.info(f"{k['synthetic_share']:.0%} of records are synthetic demo data. "
                "Records entered in this app are labelled as live measurements.")
    c = st.columns(5)
    c[0].metric("SKUs at/above margin floor", f"{k['margin_compliance']:.0%}")
    c[1].metric("Extraction success", f"{k['extraction_rate']:.0%}")
    c[2].metric("Pending reviews", k["pending"])
    c[3].metric("Approval rate", "n/a" if k["approval_rate"] is None else f"{k['approval_rate']:.0%}")
    c[4].metric("Avg approved change", "n/a" if k["avg_change"] is None else f"{k['avg_change']:+.1f}%")

    st.subheader("What this means")
    for line in analytics.insights(k):
        st.markdown(f"- {line}")

    left, right = st.columns(2)
    with left:
        st.subheader("Extraction success by day")
        trend = analytics.daily_extraction_trend()
        if not trend.empty:
            st.line_chart(trend.set_index("day")["success_rate"])
    with right:
        st.subheader("Recommendations by status")
        counts = analytics.status_counts()
        if not counts.empty:
            st.bar_chart(counts.set_index("status")["count"])

    st.subheader("Recommendations (drill-down)")
    recs = analytics.recommendations_frame()
    if recs.empty:
        st.caption("No recommendations yet. Run pricing from the Ingest page.")
        return
    f1, f2 = st.columns(2)
    sku = f1.selectbox("SKU", ["All"] + sorted(recs.sku.unique()), key="dash_sku")
    statuses = f2.multiselect("Status", sorted(recs.status.unique()), default=sorted(recs.status.unique()),
                              key="dash_status")
    view = recs[recs.status.isin(statuses)]
    if sku != "All":
        view = view[view.sku == sku]
    st.dataframe(view, hide_index=True)
    download(view, "recommendations.csv", "dl_recs")


def page_ingest() -> None:
    st.header("Ingest & Extract")
    st.caption("Paste scraped competitor text. It is treated as untrusted data and checked before any price is stored.")
    skus = analytics.sku_options()
    if skus.empty:
        st.warning("No inventory loaded. Run `python -m data.seed`.")
        return
    labels = {r.sku: f"{r.sku} · {r.title}" for r in skus.itertuples()}
    with st.form("ingest_form"):
        sku = st.selectbox("SKU", list(labels), format_func=labels.get)
        competitor = st.text_input("Competitor", value="ShopNorth", max_chars=60)
        raw = st.text_area("Raw scraped text", max_chars=settings.max_raw_text_len, height=110,
                           placeholder="e.g. Item X on sale for $42.99 with $5 coupon")
        submitted = st.form_submit_button("Extract price", type="primary")
    if submitted:
        rid = uuid.uuid4().hex[:12]
        with st.spinner("Extracting..."):
            try:
                res = ingest_scrape(sku, competitor, raw, actor="analyst-ui", request_id=rid, is_synthetic=0)
            except InputError as exc:
                st.error(f"Input rejected: {exc}")
            except Exception:
                st.error(f"Extraction failed. Request ID {rid}. Check Diagnostics.")
            else:
                if res["status"] == "extracted":
                    st.success("Price extracted and stored as a live record.")
                    o = res["offer"]
                    st.table(pd.DataFrame([{"Base price": money(o["base_price"]), "Coupon": money(o["coupon_amount"]),
                                            "Final price": money(o["final_price"]),
                                            "Confidence": f"{o['confidence']:.0%}", "Extractor": res["extractor"]}]))
                elif res["status"] == "unparsed":
                    st.warning("No usable price found. Nothing was priced from this text.")
                else:
                    st.error("Extracted values failed validation. Nothing was priced.")
                st.caption(f"Request ID: {res['request_id']} · Evidence: the text you submitted is stored with scrape #{res['scrape_id']}.")

    st.divider()
    st.subheader("Run pricing for all SKUs")
    st.caption("Creates recommendations only. Prices change only after a reviewer approves them.")
    if st.button("Run pricing"):
        try:
            out = run_pricing(actor="analyst-ui", request_id=uuid.uuid4().hex[:12])
            st.success(f"Run {out['run_id']} complete: {out['counts']['reprice']} to reprice, "
                       f"{out['counts']['hold']} on hold. Review them in the Review Queue.")
        except Exception:
            st.error("Pricing run failed. Check Diagnostics.")


def page_review() -> None:
    st.header("Review Queue")
    st.caption("Every price change needs a named human approval before it reaches inventory.")
    if st.session_state.get("flash"):
        st.success(st.session_state.pop("flash"))
    recs = analytics.recommendations_frame()
    if recs.empty:
        st.info("No recommendations yet. Run pricing from the Ingest page.")
        return
    status = st.selectbox("Show", ["pending", "approved", "rejected", "superseded", "hold", "all"], key="rev_status")
    view = recs if status == "all" else recs[recs.status == status]
    cols = ["id", "sku", "title", "current_price", "competitor_low", "floor_price", "proposed_price", "action", "status", "reason"]
    st.dataframe(view[cols], hide_index=True)
    download(view[cols], "review_queue.csv", "dl_review")
    if view.empty:
        return

    labels = {int(r.id): f"#{int(r.id)} · {r.sku} → {money(r.proposed_price)}" for r in view.itertuples()}
    rec_id = st.selectbox("Select recommendation", list(labels), format_func=labels.get, key="rev_pick")
    r = view.set_index("id").loc[rec_id]
    st.markdown(f"""
- **Current price:** {money(r.current_price)}
- **Lowest valid competitor price:** {money(r.competitor_low)}
- **Margin floor (never go below):** {money(r.floor_price)}
- **Proposed price:** {money(r.proposed_price)}
- **Why:** {r.reason}
- **Flags:** {r.flags}
""")
    if r.status != "pending":
        st.info(f"Already {r.status}. Read-only.")
        return
    with st.form("review_form"):
        reviewer = st.text_input("Reviewer name", max_chars=60)
        note = st.text_input("Note (optional)", max_chars=300)
        c1, c2 = st.columns(2)
        approve = c1.form_submit_button("Approve price change", type="primary")
        reject = c2.form_submit_button("Reject")
    if approve or reject:
        decision = "approve" if approve else "reject"
        try:
            res = review_recommendation(int(rec_id), decision, reviewer, note, request_id=uuid.uuid4().hex[:12])
            st.session_state["flash"] = f"Recommendation #{rec_id} {res['status']}. Audit entry written."
            st.rerun()
        except InputError as exc:
            st.error(f"Review rejected: {exc}")
        except Exception:
            st.error("Review failed. Check Diagnostics.")


def page_audit() -> None:
    st.header("Audit Trail")
    log = analytics.audit_frame()
    if log.empty:
        st.info("No audit entries yet.")
        return
    f1, f2 = st.columns(2)
    event = f1.selectbox("Event", ["All"] + sorted(log.event.unique()), key="aud_event")
    sku = f2.selectbox("SKU", ["All"] + sorted(log.sku.dropna().unique()), key="aud_sku")
    view = log
    if event != "All":
        view = view[view.event == event]
    if sku != "All":
        view = view[view.sku == sku]
    st.dataframe(view, hide_index=True)
    download(view, "audit_trail.csv", "dl_audit")


def page_diagnostics() -> None:
    st.header("Diagnostics")
    logs = pd.DataFrame(read_recent(500))
    if logs.empty:
        st.info("No request logs yet.")
    else:
        errors = (logs.status == "error").sum()
        c = st.columns(5)
        c[0].metric("Requests logged", len(logs))
        c[1].metric("Error rate", f"{errors / len(logs):.1%}")
        c[2].metric("Avg duration", f"{logs.duration_ms.mean():.1f} ms")
        c[3].metric("p95 duration", f"{logs.duration_ms.quantile(0.95):.1f} ms")
        tokens = int(logs.input_tokens.fillna(0).sum() + logs.output_tokens.fillna(0).sum())
        c[4].metric("LLM tokens", f"{tokens:,}")
        cols = [c_ for c_ in ["ts", "request_id", "operation", "status", "outcome", "duration_ms", "error"] if c_ in logs]
        st.subheader("Recent requests")
        st.dataframe(logs[cols].tail(25).iloc[::-1], hide_index=True)

    st.subheader("Active configuration")
    st.json({
        "extraction_mode": "llm" if settings.llm_enabled else "regex (offline)",
        "llm_model": settings.llm_model if settings.llm_enabled else None,
        "api_key": "configured (value hidden)" if settings.llm_enabled else "not configured",
        "max_change_pct": settings.max_change_pct,
        "undercut_pct": settings.undercut_pct,
        "max_raw_text_len": settings.max_raw_text_len,
        "db_path": str(settings.db_path),
    })
    results = ROOT / "evals" / "results.json"
    st.subheader("Last evaluation run")
    if results.exists():
        data = json.loads(results.read_text(encoding="utf-8"))
        st.write(f"**{data['summary']['verdict']}** · {data['summary']['passed']}/{data['summary']['total']} passed "
                 f"· ran {data['run_at']} · mode `{data['mode']}`")
    else:
        st.caption("No results yet. Run `python -m evals.run_evals`.")


with st.sidebar:
    st.title("Repricing Engine")
    page = st.radio("Navigate", PAGES, label_visibility="collapsed")
    st.caption("Extraction: " + (f"LLM ({settings.llm_model})" if settings.llm_enabled
                                 else "offline regex (no API key)"))
    st.caption("Margin and price math: deterministic code, not the LLM")
    st.caption("Human approval required for every price change")

if page == "Dashboard":
    page_dashboard()
elif page == "Ingest & Extract":
    page_ingest()
elif page == "Review Queue":
    page_review()
elif page == "Audit Trail":
    page_audit()
else:
    page_diagnostics()
