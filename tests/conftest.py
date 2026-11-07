import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Each test gets its own database and log file, and runs offline (no API key)."""
    monkeypatch.setenv("PRICING_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("PRICING_LOG", str(tmp_path / "requests.jsonl"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from app.db import init_db
    init_db()
    yield
