"""Core Copilot logic, independent of Slack so it can be tested and reused."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from .candidate_search import CandidateSearch, filter_public_profiles
from .config import Settings
from .escalation import ESCALATION_LINE, Escalator
from .formatting import candidate_text, role_alert_blocks, role_alert_text, strip_emojis
from .grok_client import GrokClient
from .knowledge import REFUSAL_LINE, KnowledgeBase, RoleBrief, guard_output
from .matching import RecruiterProfile, load_profiles, matching_roles, role_matches
from .pipeline import PipelineStore
from .prompts import (
    SYSTEM_PROMPT,
    alert_why_prompt,
    answer_prompt,
    rank_candidates_prompt,
    score_own_prompt,
    search_plan_prompt,
)
from .scoring import ScoreResult, build_score_result, total_from_criteria

log = logging.getLogger(__name__)

MAX_CANDIDATES = 5


class AlertState:
    """Which roles each recruiter has already been alerted about."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8") or "{}")

    def seen(self, user_id: str) -> set[str]:
        with self._lock:
            return set(self._read().get(user_id, []))

    def mark(self, user_id: str, role_ids: list[str]) -> None:
        with self._lock:
            data = self._read()
            data[user_id] = sorted(set(data.get(user_id, [])) | set(role_ids))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class Copilot:
    def __init__(self, settings: Settings, grok: GrokClient | None = None,
                 search: CandidateSearch | None = None):
        self.settings = settings
        self.kb = KnowledgeBase(settings.briefs_dir)
        self.pipeline = PipelineStore(settings.pipeline_path)
        self.escalator = Escalator(
            log_path=settings.escalations_path,
            answers_path=settings.founder_answers_path,
            enabled=settings.enable_founder_escalation,
            founder_user_id=settings.founder_slack_user_id,
            founder_channel_id=settings.founder_channel_id,
        )
        self.search = search or CandidateSearch(settings.serper_api_key, settings.tavily_api_key)
        self.grok = grok or GrokClient(settings.xai_api_key, settings.xai_base_url, settings.grok_model)
        self.alert_state = AlertState(settings.alert_state_path)

    # --------------------------------------------------------------- helpers
    def profiles(self) -> dict[str, RecruiterProfile]:
        return load_profiles(self.settings.recruiters_dir)

    def profile_for(self, user_id: str) -> RecruiterProfile | None:
        return self.profiles().get(user_id)

    def safe(self, text: str) -> str:
        """Output guard: block INTERNAL leaks, then remove emojis."""
        guarded, leaked = guard_output(text, self.kb.fingerprints())
        if leaked:
            log.warning("Blocked a reply that repeated INTERNAL brief content")
        return strip_emojis(guarded)

    def _context(self) -> str:
        return self.kb.context_text({"Pipeline record": self.pipeline.as_knowledge_text()})

    # ------------------------------------------------- 2. role and pipeline Q&A
    def answer(self, user_id: str, question: str, history: list[dict] | None = None) -> tuple[str, bool]:
        profile = self.profile_for(user_id)
        summary = profile.summary() if profile else "Unknown recruiter (no profile on file)."
        reply = self.grok.chat(SYSTEM_PROMPT, answer_prompt(self._context(), summary, question), history=history)
        reply = self.safe(reply)
        escalated = ESCALATION_LINE.lower().rstrip(".") in reply.lower()
        if escalated:
            reply = ESCALATION_LINE
        return reply, escalated

    # ------------------------------------------------------- 1. role alerts
    def why_it_matches(self, profile: RecruiterProfile, brief: RoleBrief) -> str:
        try:
            why = self.grok.chat(SYSTEM_PROMPT, alert_why_prompt(profile.summary(), brief.public_text), temperature=0.3)
            why = self.safe(why).strip().strip('"')
            if why and why != REFUSAL_LINE:
                return why.splitlines()[0]
        except Exception:
            log.exception("Could not write the alert sentence; using the rule-based reason")
        return f"It matches your specialty because {role_matches(profile, brief)[1]}."

    def alerts_for(self, profile: RecruiterProfile, only_new: bool) -> list[tuple[RoleBrief, str, list[dict]]]:
        briefs = matching_roles(profile, self.kb.briefs())
        if only_new:
            seen = self.alert_state.seen(profile.slack_user_id)
            briefs = [b for b in briefs if b.role_id not in seen]
        alerts = []
        for brief in briefs:
            why = self.why_it_matches(profile, brief)
            alerts.append((brief, strip_emojis(role_alert_text(brief, why)), role_alert_blocks(brief, why)))
        return alerts

    def matching_briefs(self, user_id: str) -> list[RoleBrief]:
        profile = self.profile_for(user_id)
        return matching_roles(profile, self.kb.briefs()) if profile else self.kb.briefs()

    # -------------------------------------------------- 3. find candidates
    def find_candidates(self, brief: RoleBrief) -> str:
        if not self.search.enabled:
            return ("Candidate search needs a search API key (SERPER_API_KEY or TAVILY_API_KEY). "
                    "I won't list anyone I can't find on a public profile.")
        plan = self.grok.chat_json(SYSTEM_PROMPT, search_plan_prompt(brief.public_text))
        queries = [q for q in plan.get("queries", []) if isinstance(q, str) and q.strip()][:4]
        do_not_source = [c for c in plan.get("do_not_source", []) if isinstance(c, str)]
        do_not_source.append(brief.company_short)  # never source from the hiring company
        hits = []
        for query in queries:
            hits += self.search.search(query, num=10)
        profiles = filter_public_profiles(hits, do_not_source)[:25]
        if not profiles:
            return (f"I couldn't find public profiles that fit *{brief.label}* right now. "
                    "I won't make anyone up. Want me to try again later?")
        numbered = "\n".join(
            f"[{i}] {p.title}\n    {p.snippet}" for i, p in enumerate(profiles)
        )
        ranked = self.grok.chat_json(SYSTEM_PROMPT, rank_candidates_prompt(brief.public_text, numbered))
        picks = []
        used = set()
        for item in ranked.get("candidates", []):
            try:
                index = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            if index < 0 or index >= len(profiles) or index in used:
                continue
            hit = profiles[index]
            name = str(item.get("full_name", "")).strip()
            # Anti-invention guard: the name must appear in the real search result title.
            if not name or name.lower() not in hit.title.lower():
                continue
            used.add(index)
            score = total_from_criteria(item.get("criteria", []))
            bullets = [str(b) for b in item.get("bullets", [])][:3]
            picks.append((score, name, hit.link, str(item.get("why", "")), bullets))
        picks.sort(key=lambda p: p[0], reverse=True)
        picks = picks[:MAX_CANDIDATES]
        if not picks:
            return (f"I found some public profiles for *{brief.label}*, but none were a clear fit. "
                    "I won't make anyone up.")
        noun = "candidate" if len(picks) == 1 else "candidates"
        lines = [f"Here are {len(picks)} {noun} for *{brief.label}* from public profiles:"]
        for i, (score, name, link, why, bullets) in enumerate(picks, start=1):
            lines.append(candidate_text(i, name, link, why, bullets, score))
        if len(picks) < MAX_CANDIDATES:
            people = "real person" if len(picks) == 1 else "real people"
            lines.append(f"I only found {len(picks)} {people} who fit, so I stopped there instead of guessing.")
        lines.append("Please open each profile and verify before reaching out. The final call is yours.")
        return self.safe("\n\n".join(lines))

    # ---------------------------------------------- 4. submit your own
    def score_candidate(self, brief: RoleBrief, candidate_profile_text: str) -> ScoreResult:
        data = self.grok.chat_json(SYSTEM_PROMPT, score_own_prompt(brief.public_text, candidate_profile_text))
        result = build_score_result(data, brief.label)
        result.strengths = [self.safe(s) for s in result.strengths]
        result.gaps = [self.safe(g) for g in result.gaps]
        return result

    def submit(self, result: ScoreResult, brief: RoleBrief, user_id: str) -> str:
        count = self.pipeline.add(brief.role_id, brief.label, result.candidate_name,
                                  result.score, result.verdict, user_id)
        return f"{result.candidate_name} submitted for {brief.label}. The pipeline now has {count} candidates."

    # --------------------------------------------------------- /pipeline
    def pipeline_text(self, brief: RoleBrief | None = None) -> str:
        if brief:
            count = self.pipeline.count(brief.role_id)
            return f"*{brief.label}* has {count} candidates in the pipeline. (Source: Pipeline record)"
        rows = {rid: (label, n) for rid, label, n in self.pipeline.summary()}
        lines = ["*Pipeline* (Source: Pipeline record)"]
        for b in self.kb.briefs():
            lines.append(f"- {b.label}: {rows.get(b.role_id, (b.label, 0))[1]} candidates")
        return "\n".join(lines)
