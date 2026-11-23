"""Turn the model's scorecard JSON into a score, a verdict, and a Slack reply.

The model only fills in points per scorecard criterion. The total, the verdict
band, and the dealbreaker rule are computed here so they are consistent and
testable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

GOOD_FIT_MIN = 75
POSSIBLE_FIT_MIN = 55

VERDICT_GOOD = "Good fit, ready to submit"
VERDICT_POSSIBLE = "Possible fit, review first"
VERDICT_NOT = "Not a fit"


def verdict_for(score: int, dealbreakers: list[str] | None = None) -> str:
    if dealbreakers:
        return VERDICT_NOT
    if score >= GOOD_FIT_MIN:
        return VERDICT_GOOD
    if score >= POSSIBLE_FIT_MIN:
        return VERDICT_POSSIBLE
    return VERDICT_NOT


def parse_json_block(text: str) -> dict:
    """Parse JSON from a model reply, tolerating code fences or extra prose."""
    if not text:
        raise ValueError("empty model reply")
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in model reply")
        return json.loads(candidate[start : end + 1])


def _as_int(value, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def total_from_criteria(criteria: list[dict]) -> int:
    total = 0
    for item in criteria or []:
        weight = max(0, _as_int(item.get("weight"), 0))
        points = _as_int(item.get("points"), 0)
        total += min(max(points, 0), weight)
    return min(max(total, 0), 100)


@dataclass
class ScoreResult:
    candidate_name: str
    role_label: str
    score: int
    verdict: str
    strengths: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    dealbreakers: list[str] = field(default_factory=list)
    criteria: list[dict] = field(default_factory=list)

    @property
    def can_submit(self) -> bool:
        return self.score >= POSSIBLE_FIT_MIN and not self.dealbreakers


def build_score_result(data: dict, role_label: str) -> ScoreResult:
    criteria = data.get("criteria") or []
    if criteria:
        score = total_from_criteria(criteria)
    else:
        score = min(max(_as_int(data.get("score"), 0), 0), 100)
    dealbreakers = [d for d in (data.get("dealbreakers_hit") or []) if str(d).strip()]
    gaps = [str(g) for g in (data.get("gaps") or []) if str(g).strip()]
    for dealbreaker in dealbreakers:
        note = f"Dealbreaker: {dealbreaker}"
        if note not in gaps:
            gaps.insert(0, note)
    return ScoreResult(
        candidate_name=str(data.get("candidate_name") or "Candidate").strip(),
        role_label=role_label,
        score=score,
        verdict=verdict_for(score, dealbreakers),
        strengths=[str(s) for s in (data.get("strengths") or []) if str(s).strip()][:3],
        gaps=gaps[:4] or ["None noted from the information shared"],
        dealbreakers=[str(d) for d in dealbreakers],
        criteria=criteria,
    )


def format_score(result: ScoreResult) -> str:
    lines = [
        f"*{result.candidate_name} - {result.role_label}*",
        f"Score: {result.score}/100",
        f"Verdict: {result.verdict}",
        "Why:",
    ]
    lines += [f"- {s}" for s in (result.strengths or ["Not enough information to list strengths"])]
    lines.append("Gaps:")
    lines += [f"- {g}" for g in result.gaps]
    return "\n".join(lines)
