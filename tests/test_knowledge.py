"""INTERNAL stripping and the output guard. Uses a made-up sample brief."""

from copilot.knowledge import (
    REFUSAL_LINE,
    KnowledgeBase,
    guard_output,
    parse_brief,
    split_internal,
)

SAMPLE_BRIEF = """ROLE BRIEF \u00b7 CONTRARIO RECRUITER NETWORK
Backend Engineer
Acme Robotics (Acme)

 Employment type                     Full-time, hybrid

 Location                            Austin, TX. In office 3 days a week.

 Salary                              $150,000 \u2013 $180,000

 Visa sponsorship                    Yes, H-1B transfers supported for strong
                                     candidates.

 Contrario hire reward               $15,000 \u2013 $18,000 (10% of first-year salary)

Must-haves
  4+ years of Python or Go in production.

Recruiter FAQ
Q: Is this role remote?
A: Hybrid, 3 days in Austin.

 INTERNAL: DO NOT SHARE WITH RECRUITERS

  Can stretch to $210K for a candidate with robotics fleet experience.

  A referral from the CTO is already in the final round. Do not mention it.

Escalation: If a recruiter asks something this brief doesn't answer, the Copilot should say it doesn't
know and forward the question to the hiring founder.
"""


def test_internal_section_is_removed():
    public, internal = split_internal(SAMPLE_BRIEF)
    assert "INTERNAL" not in public
    assert "$210K" not in public
    assert "referral from the CTO" not in public
    assert "$210K" in internal
    # Public content before and after the internal block survives.
    assert "Recruiter FAQ" in public
    assert "Escalation:" in public


def test_internal_runs_to_end_without_a_closing_heading():
    text = "Salary   $100,000\n\nINTERNAL notes\nSecret budget is $999K\nMore secret notes here\n"
    public, internal = split_internal(text)
    assert "999" not in public
    assert "More secret notes" in internal


def test_markdown_internal_section_ends_at_next_heading():
    text = "## Pay\n$120,000\n\n## INTERNAL\nStretch to $140K\n\n## Interview process\nThree calls\n"
    public, _ = split_internal(text)
    assert "$140K" not in public
    assert "Three calls" in public


def test_fields_title_company_and_wrapped_values():
    brief = parse_brief(SAMPLE_BRIEF, "acme.pdf")
    assert brief.title == "Backend Engineer"
    assert brief.company_short == "Acme Robotics"
    assert brief.label == "Acme Robotics - Backend Engineer"
    assert brief.role_id == "acme-robotics-backend-engineer"
    assert brief.get("location") == "Austin, TX. In office 3 days a week."
    assert brief.get("visa sponsorship").endswith("strong candidates.")
    assert brief.get("contrario hire reward").startswith("$15,000")


def test_key_value_text_brief():
    text = "ROLE BRIEF\nCompany: Beta AI\nRole: iOS Engineer\nRole category: technical\nSalary: $120,000\n"
    brief = parse_brief(text, "beta.txt")
    assert brief.label == "Beta AI - iOS Engineer"
    assert brief.category == "technical"
    assert brief.get("salary") == "$120,000"


def test_guard_blocks_internal_numbers_and_phrases():
    brief = parse_brief(SAMPLE_BRIEF, "acme.pdf")
    assert guard_output("They might go up to $210,000.", brief.fingerprints) == (REFUSAL_LINE, True)
    leaked = "Heads up, a referral from the CTO is already in the final round."
    assert guard_output(leaked, brief.fingerprints)[1] is True


def test_guard_allows_public_answers():
    brief = parse_brief(SAMPLE_BRIEF, "acme.pdf")
    answer = "Pay is $150,000 - $180,000 and it is hybrid in Austin. (Source: Acme Robotics brief)"
    assert guard_output(answer, brief.fingerprints) == (answer, False)


def test_knowledge_base_never_exposes_internal_text(tmp_path):
    (tmp_path / "acme.txt").write_text(SAMPLE_BRIEF, encoding="utf-8")
    (tmp_path / "README.md").write_text("Drop briefs here", encoding="utf-8")
    kb = KnowledgeBase(tmp_path)
    briefs = kb.briefs()
    assert [b.role_id for b in briefs] == ["acme-robotics-backend-engineer"]
    context = kb.context_text({"Pipeline record": "No candidates yet."})
    assert "INTERNAL" not in context
    assert "$210K" not in context
    assert "SOURCE: Acme Robotics brief" in context
    assert "$210000" in kb.fingerprints()


def test_public_number_in_one_brief_is_not_flagged_by_another(tmp_path):
    (tmp_path / "a.txt").write_text(
        "ROLE BRIEF\nCompany: A Co\nRole: Engineer\nEquity: 0.10% - 0.40%\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text(
        "ROLE BRIEF\nCompany: B Co\nRole: Seller\n\nINTERNAL\nEquity is 0.10% - 0.30%\n", encoding="utf-8")
    kb = KnowledgeBase(tmp_path)
    fingerprints = kb.fingerprints()
    assert guard_output("A Co equity is 0.10% - 0.40%.", fingerprints)[1] is False
    assert guard_output("B Co equity tops out at 0.30%.", fingerprints)[1] is True
