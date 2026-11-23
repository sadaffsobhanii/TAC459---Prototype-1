"""Founder escalation for questions the briefs don't answer.

Every unanswered question is logged to data/escalations.json. When
ENABLE_FOUNDER_ESCALATION is true it is also posted to FOUNDER_CHANNEL_ID
(tagging FOUNDER_SLACK_USER_ID) or sent to the founder by DM. When the founder
replies in that thread, the answer is saved to briefs/founder-answers.md (so the
bot can use it next time) and sent back to the recruiter who asked.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

ESCALATION_LINE = "I don't have that info yet. I've flagged it for the founder."


class Escalator:
    def __init__(self, log_path: Path, answers_path: Path, enabled: bool,
                 founder_user_id: str = "", founder_channel_id: str = ""):
        self.log_path = Path(log_path)
        self.answers_path = Path(answers_path)
        self.enabled = enabled
        self.founder_user_id = founder_user_id
        self.founder_channel_id = founder_channel_id
        self._lock = threading.Lock()

    # ------------------------------------------------------------ storage
    def _read(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        return json.loads(self.log_path.read_text(encoding="utf-8") or "[]")

    def _write(self, records: list[dict]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")

    # ----------------------------------------------------------- escalate
    def escalate(self, client, question: str, recruiter_id: str, recruiter_name: str,
                 role_label: str | None, reply_channel: str, reply_thread_ts: str | None) -> dict:
        record = {
            "id": uuid.uuid4().hex[:8],
            "question": question,
            "role": role_label or "Not sure which role",
            "recruiter_id": recruiter_id,
            "recruiter_name": recruiter_name,
            "reply_channel": reply_channel,
            "reply_thread_ts": reply_thread_ts,
            "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "posted_channel": None,
            "posted_ts": None,
            "answer": None,
        }
        if self.enabled and client is not None and (self.founder_channel_id or self.founder_user_id):
            try:
                channel = self.founder_channel_id
                if not channel:
                    channel = client.conversations_open(users=self.founder_user_id)["channel"]["id"]
                tag = f"<@{self.founder_user_id}> " if self.founder_user_id else ""
                text = (
                    f"{tag}A recruiter asked something the role briefs don't answer.\n"
                    f"*Role:* {record['role']}\n"
                    f"*Question:* {question}\n"
                    f"*Asked by:* <@{recruiter_id}>\n"
                    "Reply in this thread and I'll add your answer to the knowledge base "
                    "and send it to the recruiter."
                )
                response = client.chat_postMessage(channel=channel, text=text)
                record["posted_channel"] = response["channel"]
                record["posted_ts"] = response["ts"]
            except Exception:  # never break the recruiter reply over this
                log.exception("Could not post founder escalation")
        else:
            log.info("Founder escalation is off; question logged only: %s", question)
        with self._lock:
            records = self._read()
            records.append(record)
            self._write(records)
        return record

    # ------------------------------------------------------ founder reply
    def find_by_thread(self, channel: str, thread_ts: str | None) -> dict | None:
        if not thread_ts:
            return None
        with self._lock:
            for record in self._read():
                if record.get("posted_channel") == channel and record.get("posted_ts") == thread_ts:
                    return record
        return None

    def record_answer(self, record: dict, answer: str) -> None:
        with self._lock:
            records = self._read()
            for item in records:
                if item["id"] == record["id"]:
                    item["answer"] = answer
                    item["answered_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._write(records)
            self.answers_path.parent.mkdir(parents=True, exist_ok=True)
            is_new = not self.answers_path.exists()
            with self.answers_path.open("a", encoding="utf-8") as handle:
                if is_new:
                    handle.write("# Founder answers\n\nAnswers founders gave to recruiter questions the briefs did not cover.\n")
                handle.write(f"\n## {record['role']}\nQ: {record['question']}\nA: {answer}\n")
