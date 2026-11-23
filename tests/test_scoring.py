"""Verdict bands, dealbreakers, clamping, and reply format."""

import pytest

from copilot.scoring import (
    VERDICT_GOOD,
    VERDICT_NOT,
    VERDICT_POSSIBLE,
    build_score_result,
    format_score,
    parse_json_block,
    total_from_criteria,
    verdict_for,
)


@pytest.mark.parametrize(
    "score, expected",
    [(100, VERDICT_GOOD), (75, VERDICT_GOOD), (74, VERDICT_POSSIBLE), (55, VERDICT_POSSIBLE),
     (54, VERDICT_NOT), (0, VERDICT_NOT)],
)
def test_verdict_bands(score, expected):
    assert verdict_for(score) == expected


def test_any_dealbreaker_is_not_a_fit():
    assert verdict_for(92, ["Only React Native, no native Swift"]) == VERDICT_NOT


def test_points_are_clamped_to_weights():
    criteria = [
        {"name": "Native iOS depth", "weight": 30, "points": 45},
        {"name": "Performance / audio", "weight": 20, "points": -5},
        {"name": "Consumer product scale", "weight": 20, "points": 15},
        {"name": "Startup fit", "weight": 15, "points": "12"},
        {"name": "Proof of work", "weight": 15, "points": None},
    ]
    assert total_from_criteria(criteria) == 30 + 0 + 15 + 12 + 0


def test_parse_json_block_handles_fences_and_prose():
    assert parse_json_block('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_block('Here you go: {"a": {"b": 2}} Thanks!') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        parse_json_block("no json here")


def _model_reply(points, dealbreakers=None):
    return {
        "candidate_name": "Jordan Lee",
        "dealbreakers_hit": dealbreakers or [],
        "criteria": [
            {"name": "Native iOS depth", "weight": 30, "points": points[0]},
            {"name": "Performance / audio", "weight": 20, "points": points[1]},
            {"name": "Consumer product scale", "weight": 20, "points": points[2]},
            {"name": "Startup fit", "weight": 15, "points": points[3]},
            {"name": "Proof of work", "weight": 15, "points": points[4]},
        ],
        "strengths": ["5 years of Swift and SwiftUI", "Shipped a consumer app with 300K users"],
        "gaps": ["No keyboard extension or audio work shown"],
    }


def test_good_fit_can_be_submitted_and_formats_correctly():
    result = build_score_result(_model_reply([27, 10, 18, 12, 13]), "Wispr AI - Remote iOS Engineer")
    assert result.score == 80
    assert result.verdict == VERDICT_GOOD
    assert result.can_submit
    text = format_score(result)
    assert text.splitlines()[0] == "*Jordan Lee - Wispr AI - Remote iOS Engineer*"
    assert "Score: 80/100" in text
    assert "Verdict: Good fit, ready to submit" in text
    assert "Why:\n- 5 years of Swift and SwiftUI" in text
    assert "Gaps:\n- No keyboard extension or audio work shown" in text


def test_possible_fit_can_still_be_submitted():
    result = build_score_result(_model_reply([20, 8, 12, 10, 10]), "Role")
    assert result.score == 60
    assert result.verdict == VERDICT_POSSIBLE
    assert result.can_submit


def test_low_score_and_dealbreaker_cannot_be_submitted():
    low = build_score_result(_model_reply([10, 5, 10, 10, 10]), "Role")
    assert low.score == 45 and not low.can_submit
    blocked = build_score_result(_model_reply([30, 20, 20, 15, 15], ["Only Flutter experience"]), "Role")
    assert blocked.score == 100
    assert blocked.verdict == VERDICT_NOT
    assert not blocked.can_submit
    assert blocked.gaps[0] == "Dealbreaker: Only Flutter experience"
