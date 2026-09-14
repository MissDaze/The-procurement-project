from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'nixsec.db'}")
    session_secret: str = os.getenv("SESSION_SECRET", "dev-only-change-me")
    admin_email: str = os.getenv("ADMIN_EMAIL", "")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1-mini")
    ai_enabled: bool = _bool("AI_ENABLED")
    nixsec_website: str = os.getenv("NIXSEC_WEBSITE", "https://nixsec.com.au")
    abr_guid: str = os.getenv("ABR_GUID", "")
    live_tender_url: str = os.getenv("LIVE_TENDER_URL", "https://www.tenders.vic.gov.au/tenders/open")
    report_dir: Path = ROOT / "generated_reports"
    upload_dir: Path = ROOT / "uploads"


settings = Settings()

