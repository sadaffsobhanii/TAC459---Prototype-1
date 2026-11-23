"""Role matching against Saf's profile, the pipeline store, and emoji removal."""

import json
from pathlib import Path

from copilot.formatting import strip_emojis
from copilot.knowledge import parse_brief
from copilot.matching import RecruiterProfile, matching_roles, role_matches
from copilot.pipeline import PipelineStore

ROOT = Path(__file__).resolve().parent.parent


def saf() -> RecruiterProfile:
    data = json.loads((ROOT / "recruiters" / "saf.json").read_text(encoding="utf-8"))
    return RecruiterProfile.from_dict(data)


def brief(company: str, title: str, extra: str = ""):
    return parse_brief(f"ROLE BRIEF\nCompany: {company}\nRole: {title}\n{extra}", f"{company}.txt")


def test_technical_roles_match_and_gtm_or_ops_roles_are_skipped():
    profile = saf()
    assert role_matches(profile, brief("Wispr AI", "Remote iOS Engineer"))[0] is True
    assert role_matches(profile, brief("Listen Labs", "Technical GTM Lead"))[0] is False
    assert role_matches(profile, brief("AfterQuery", "Strategic Projects Lead"))[0] is False
    assert role_matches(profile, brief("Acme", "ML Engineer"))[0] is True


def test_role_category_overrides_keywords():
    profile = saf()
    assert role_matches(profile, brief("Acme", "Founding Lead", "Role category: technical\n"))[0] is True
    assert role_matches(profile, brief("Acme", "Mobile Lead", "Role category: non-technical\n"))[0] is False


def test_matching_roles_filters_list():
    briefs = [brief("Wispr AI", "Remote iOS Engineer"), brief("Listen Labs", "Technical GTM Lead")]
    assert [b.label for b in matching_roles(saf(), briefs)] == ["Wispr AI - Remote iOS Engineer"]


def test_pipeline_counts_and_persists(tmp_path):
    path = tmp_path / "pipeline.json"
    store = PipelineStore(path)
    assert store.count("wispr-ai-remote-ios-engineer") == 0
    assert store.add("wispr-ai-remote-ios-engineer", "Wispr AI - Remote iOS Engineer", "Jordan Lee", 80,
                     "Good fit, ready to submit", "U123") == 1
    assert store.add("wispr-ai-remote-ios-engineer", "Wispr AI - Remote iOS Engineer", "Sam Park", 62,
                     "Possible fit, review first", "U123") == 2
    assert PipelineStore(path).count("wispr-ai-remote-ios-engineer") == 2
    assert "2 candidates" in store.as_knowledge_text()
    assert "Jordan Lee" not in store.as_knowledge_text()


def test_strip_emojis():
    assert strip_emojis("Great fit \U0001F680 :tada: ready") == "Great fit ready"
    assert strip_emojis("Pay: $84,000 - $120,000") == "Pay: $84,000 - $120,000"
