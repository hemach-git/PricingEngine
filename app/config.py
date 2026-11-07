"""Application settings. Secrets come only from environment variables; they are never stored, logged, or displayed."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    db_path: Path
    log_path: Path
    max_change_pct: float = 10.0      # max price move per approved change (+/-)
    undercut_pct: float = 1.0         # target = lowest valid competitor price minus this %
    max_raw_text_len: int = 500       # input guardrail for scraped text
    llm_model: str = "claude-haiku-5-5"
    llm_timeout_s: float = 20.0

    @property
    def llm_enabled(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))


def get_settings() -> Settings:
    return Settings(
        db_path=Path(os.environ.get("PRICING_DB", ROOT / "data" / "pricing.db")),
        log_path=Path(os.environ.get("PRICING_LOG", ROOT / "logs" / "requests.jsonl")),
        llm_model=os.environ.get("LLM_MODEL", "claude-haiku-5-5"),
    )
