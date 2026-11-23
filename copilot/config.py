"""Settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _list(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def _path(name: str, default: Path) -> Path:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


@dataclass(frozen=True)
class Settings:
    slack_bot_token: str
    slack_app_token: str
    xai_api_key: str
    xai_base_url: str
    grok_model: str
    briefs_dir: Path
    recruiters_dir: Path
    data_dir: Path
    enable_founder_escalation: bool
    founder_slack_user_id: str
    founder_channel_id: str
    admin_user_ids: list[str] = field(default_factory=list)
    serper_api_key: str = ""
    tavily_api_key: str = ""
    alert_timezone: str = "America/Los_Angeles"
    alert_hour: int = 9
    alert_minute: int = 0
    brief_scan_minutes: int = 0
    enable_scheduler: bool = True

    @property
    def pipeline_path(self) -> Path:
        return self.data_dir / "pipeline.json"

    @property
    def alert_state_path(self) -> Path:
        return self.data_dir / "alert_state.json"

    @property
    def escalations_path(self) -> Path:
        return self.data_dir / "escalations.json"

    @property
    def founder_answers_path(self) -> Path:
        return self.briefs_dir / "founder-answers.md"

    def is_admin(self, user_id: str) -> bool:
        """Founders/admins may add roles and answer escalations.

        If no admins and no founder are configured, everyone is allowed
        (handy for a local demo workspace).
        """
        allowed = set(self.admin_user_ids)
        if self.founder_slack_user_id:
            allowed.add(self.founder_slack_user_id)
        return not allowed or user_id in allowed


def load_settings() -> Settings:
    return Settings(
        slack_bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
        slack_app_token=os.getenv("SLACK_APP_TOKEN", ""),
        xai_api_key=os.getenv("XAI_API_KEY", ""),
        xai_base_url=os.getenv("XAI_BASE_URL", "https://api.x.ai/v1"),
        grok_model=os.getenv("GROK_MODEL", "grok-4.7"),
        briefs_dir=_path("BRIEFS_DIR", ROOT / "briefs"),
        recruiters_dir=_path("RECRUITERS_DIR", ROOT / "recruiters"),
        data_dir=_path("DATA_DIR", ROOT / "data"),
        enable_founder_escalation=_bool("ENABLE_FOUNDER_ESCALATION", True),
        founder_slack_user_id=os.getenv("FOUNDER_SLACK_USER_ID", "").strip(),
        founder_channel_id=os.getenv("FOUNDER_CHANNEL_ID", "").strip(),
        admin_user_ids=_list("ADMIN_SLACK_USER_IDS"),
        serper_api_key=os.getenv("SERPER_API_KEY", "").strip(),
        tavily_api_key=os.getenv("TAVILY_API_KEY", "").strip(),
        alert_timezone=os.getenv("ALERT_TIMEZONE", "America/Los_Angeles"),
        alert_hour=_int("ALERT_HOUR", 9),
        alert_minute=_int("ALERT_MINUTE", 0),
        brief_scan_minutes=_int("BRIEF_SCAN_MINUTES", 0),
        enable_scheduler=_bool("ENABLE_SCHEDULER", True),
    )
