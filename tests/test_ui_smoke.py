from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent


def test_every_page_renders_without_exception():
    at = AppTest.from_file(str(ROOT / "ui" / "streamlit_app.py"), default_timeout=30)
    at.run()
    assert not at.exception
    for label in ["Ingest & Extract", "Review Queue", "Audit Trail", "Diagnostics", "Dashboard"]:
        at.sidebar.radio[0].set_value(label).run()
        assert not at.exception, f"page {label} raised"
