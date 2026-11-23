"""Slack text and Block Kit helpers. Bot messages never contain emojis."""

from __future__ import annotations

import re

from .knowledge import RoleBrief

EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002B00-\U00002BFF"
    "\uFE0F\u200D"
    "]+"
)
SLACK_SHORTCODE_RE = re.compile(r"(?<![\w<]):[a-z0-9_+\-]+:(?!\w)")


def strip_emojis(text: str) -> str:
    text = EMOJI_RE.sub("", text or "")
    text = SLACK_SHORTCODE_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


FOLLOW_UP = "Would you like me to find candidates for this role, or would you like to submit your own?"


def role_alert_text(brief: RoleBrief, why: str) -> str:
    return (
        "New role that matches your profile:\n"
        f"*{brief.label}*\n"
        f"Location: {brief.get('location')} | Pay: {brief.get('salary')} | "
        f"Contrario reward: {brief.get('contrario hire reward')}\n"
        f"Why it matches you: {why}\n\n"
        f"{FOLLOW_UP}"
    )


def role_action_block(role_id: str) -> dict:
    return {
        "type": "actions",
        "block_id": f"role_actions:{role_id}",
        "elements": [
            {
                "type": "button",
                "action_id": "find_candidates",
                "text": {"type": "plain_text", "text": "Find candidates"},
                "style": "primary",
                "value": role_id,
            },
            {
                "type": "button",
                "action_id": "submit_own",
                "text": {"type": "plain_text", "text": "Submit my own"},
                "value": role_id,
            },
        ],
    }


def role_alert_blocks(brief: RoleBrief, why: str) -> list[dict]:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": strip_emojis(role_alert_text(brief, why))}},
        role_action_block(brief.role_id),
    ]


def submit_confirm_blocks(text: str) -> list[dict]:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": strip_emojis(text)}},
        {
            "type": "actions",
            "block_id": "submit_confirm",
            "elements": [
                {"type": "button", "action_id": "confirm_submit", "style": "primary",
                 "text": {"type": "plain_text", "text": "Yes, submit"}, "value": "yes"},
                {"type": "button", "action_id": "cancel_submit",
                 "text": {"type": "plain_text", "text": "Not yet"}, "value": "no"},
            ],
        },
    ]


def candidate_text(index: int, name: str, link: str, why: str, bullets: list[str], score: int) -> str:
    label = "LinkedIn" if "linkedin.com" in link else "Profile"
    lines = [f"*{index}. {name}*", f"{label}: {link}", f"Why they are a great fit: {why}"]
    lines += [f"- {b}" for b in bullets[:3]]
    lines.append(f"Score: {score}/100 (role scorecard)")
    return "\n".join(lines)
