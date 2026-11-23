"""Match role briefs to recruiter profiles (recruiters/*.json)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .knowledge import RoleBrief


@dataclass
class RecruiterProfile:
    slack_user_id: str
    name: str
    title: str
    specialty: str
    alert_keywords: list[str] = field(default_factory=list)
    skip_keywords: list[str] = field(default_factory=list)
    alert_categories: list[str] = field(default_factory=list)
    skip_categories: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "RecruiterProfile":
        prefs = data.get("alert_preferences", {})
        return cls(
            slack_user_id=data.get("slack_user_id", ""),
            name=data.get("name", "there"),
            title=data.get("title", ""),
            specialty=data.get("specialty", ""),
            alert_keywords=[k.lower() for k in prefs.get("alert_role_keywords", [])],
            skip_keywords=[k.lower() for k in prefs.get("skip_role_keywords", [])],
            alert_categories=[k.lower() for k in prefs.get("alert_categories", [])],
            skip_categories=[k.lower() for k in prefs.get("skip_categories", [])],
            raw=data,
        )

    def summary(self) -> str:
        """Profile text for prompts (no Slack id)."""
        data = {k: v for k, v in self.raw.items() if k != "slack_user_id"}
        return json.dumps(data, indent=2)


def load_profiles(recruiters_dir: Path) -> dict[str, RecruiterProfile]:
    profiles: dict[str, RecruiterProfile] = {}
    for path in sorted(Path(recruiters_dir).glob("*.json")):
        profile = RecruiterProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))
        if profile.slack_user_id:
            profiles[profile.slack_user_id] = profile
    return profiles


def _has_keyword(text: str, keywords: list[str]) -> str | None:
    lowered = text.lower()
    for keyword in keywords:
        if re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", lowered):
            return keyword
    return None


def role_matches(profile: RecruiterProfile, brief: RoleBrief) -> tuple[bool, str]:
    """Return (matches, reason). Skip rules win over alert rules."""
    category = brief.category
    if category:
        if category in profile.skip_categories:
            return False, f"category '{category}' is in your skip list"
        if category in profile.alert_categories:
            return True, f"it is a {category} role"
    skipped = _has_keyword(brief.title, profile.skip_keywords)
    if skipped:
        return False, f"'{skipped}' roles are in your skip list"
    matched = _has_keyword(brief.title, profile.alert_keywords)
    if matched:
        return True, f"it is a {matched} role"
    return False, "the title is outside your specialty"


def matching_roles(profile: RecruiterProfile, briefs: list[RoleBrief]) -> list[RoleBrief]:
    return [b for b in briefs if role_matches(profile, b)[0]]
