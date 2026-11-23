# Role Brief Copilot

Role Brief Copilot is a Slack bot for Contrario, the YC-backed recruiting marketplace where independent recruiters submit candidates to startup roles. It gives recruiters a daily DM of new roles that match their profile, answers role and pipeline questions straight from the founder's role brief (with the source named), finds real candidates from public profiles, and scores a recruiter's own candidate before they submit. Recruiters only earn a reward for good submissions, and founders lose time answering the same questions, so the bot gets recruiters accurate answers in seconds, keeps confidential (INTERNAL) brief details private, and sends anything the brief doesn't cover to the founder.

Built by Group 8 in TAC 459 (USC Generative AI capstone) for Contrario. The live prototype was set up in a no-code assistant platform. This repo is the same behavior written as working code: Python, Slack Bolt in Socket Mode, and the Grok API.

## How It Works

1. **A founder adds a role.** They drop the role brief PDF into `briefs/` or run `/add-role` in Slack and paste the brief, which saves it to `briefs/` as a `.txt` file. Each brief has the role facts (location, salary, visa, Contrario reward), must-haves, dealbreakers, candidate targeting, a 100-point scorecard, an FAQ, and an optional INTERNAL section.
2. **The bot loads the brief.** `copilot/knowledge.py` extracts the text with pypdf and cuts out every INTERNAL section before anything reaches the model. It keeps only fingerprints of the internal content (dollar amounts, percentages, 5-word phrases) for the output guard. Briefs reload automatically when a file changes.
3. **It matches roles to recruiters.** Each recruiter has a JSON profile in `recruiters/`. `copilot/matching.py` checks the role title (or its `Role category`) against the recruiter's alert and skip lists. Saf is a technical recruiter, so she gets engineering roles like the Wispr AI Remote iOS Engineer, and GTM, sales, and operations roles are skipped.
4. **Daily role alerts.** On weekdays at 9 AM PT the scheduler DMs each recruiter the new matching roles: company and role, location, pay, Contrario reward, and one sentence on why it matches. Each alert has two buttons, **Find candidates** and **Submit my own**. Recruiters can also ask "What new roles were posted?" or run `/roles` at any time. `/add-role` sends instant alerts too.
5. **A recruiter asks a question in Slack.** They DM the bot (App Home, Messages tab) or @mention it in a channel, where it replies in the thread. Examples: "Does Wispr sponsor visas?" or "How many candidates are in the pipeline?" The model gets only the public brief text, the pipeline record (`data/pipeline.json`), and any founder answers.
6. **The bot answers from the brief.** It answers in 1-3 sentences and names the source, for example "(Source: Wispr AI brief)". Every reply passes the output guard. If a reply would repeat an INTERNAL number or phrase, it becomes "That's not something I can share." Emojis are stripped.
7. **Or it escalates to the founder.** If the brief doesn't cover the question, the bot replies "I don't have that info yet. I've flagged it for the founder." It logs the question to `data/escalations.json`. With `ENABLE_FOUNDER_ESCALATION=true` it also posts the question to `FOUNDER_CHANNEL_ID`, tagging `FOUNDER_SLACK_USER_ID` (or DMs the founder if no channel is set). When the founder replies in that thread, the answer is saved to `briefs/founder-answers.md` and sent back to the recruiter. The live prototype ran with escalation off: Saf asked that nobody be tagged yet, so the bot still said the line but no one was tagged.
8. **Find candidates.** The bot asks Grok for public-profile search queries based on the role's targeting section. It runs them through Serper or Tavily, keeping only public `linkedin.com/in/` and GitHub profile URLs and dropping anyone at a "Do not source from" company. Grok ranks only those real results with the role scorecard. The bot returns up to 5 people. Names must match the search result and links come from the search API, so if fewer than 5 real people fit, it returns fewer and says so.
9. **Submit your own.** The bot asks for a resume (PDF upload) or LinkedIn profile. It checks dealbreakers first, then scores the candidate with the role scorecard and gives a verdict: 75+ Good fit, 55-74 Possible fit, below 55 or any dealbreaker Not a fit. At 55+ it asks "Would you like me to submit [name] for this role?" On yes, it records the candidate in `data/pipeline.json` and confirms "[Name] submitted for [Company] - [Role]. The pipeline now has [X] candidates." In this prototype, submissions are recorded in the bot's pipeline file, not on Contrario's site.
10. **The recruiter decides.** The bot never judges protected traits, uses only public professional information, and leaves the final submission decision to the recruiter.

## File Structure

```text
role-brief-copilot/
├── briefs/
│   └── README.md
├── copilot/
│   ├── __init__.py
│   ├── candidate_search.py
│   ├── config.py
│   ├── escalation.py
│   ├── formatting.py
│   ├── grok_client.py
│   ├── knowledge.py
│   ├── matching.py
│   ├── pipeline.py
│   ├── prompts.py
│   ├── scheduler.py
│   ├── scoring.py
│   └── service.py
├── data/
│   └── pipeline.json
├── recruiters/
│   └── saf.json
├── tests/
│   ├── test_knowledge.py
│   ├── test_matching_and_pipeline.py
│   └── test_scoring.py
├── .env.example
├── .gitignore
├── app.py
├── manifest.yml
├── pytest.ini
├── requirements.txt
└── ROLE_BRIEF_COPILOT.md
```

Notes:
- The real brief PDFs (Wispr AI, Listen Labs, AfterQuery) are not committed, because they contain INTERNAL sections. `.gitignore` keeps everything in `briefs/` except its README. Copy the PDFs into `briefs/` locally.
- `data/alert_state.json`, `data/escalations.json`, and `briefs/founder-answers.md` are created at runtime.

## Code

### app.py

```python
"""Role Brief Copilot: Slack Bolt app (Socket Mode) powered by the Grok API.

Recruiters DM the bot or @mention it. It sends daily role alerts that match
their profile, answers role and pipeline questions from the briefs, finds real
candidates from public profiles, and scores and submits the recruiter's own
candidate. Built by Group 8 in TAC 459 (USC) for Contrario.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import defaultdict, deque

import requests
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from copilot.config import load_settings
from copilot.formatting import strip_emojis, submit_confirm_blocks
from copilot.knowledge import RoleBrief, extract_pdf_text, slugify
from copilot.scheduler import send_alerts, start_scheduler
from copilot.scoring import format_score
from copilot.service import Copilot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("role-brief-copilot")

settings = load_settings()

_missing = [name for name, value in [
    ("SLACK_BOT_TOKEN", settings.slack_bot_token),
    ("SLACK_APP_TOKEN", settings.slack_app_token),
    ("XAI_API_KEY", settings.xai_api_key),
] if not value]
if _missing:
    raise SystemExit(f"Missing environment variables: {', '.join(_missing)}. Copy .env.example to .env and fill it in.")

app = App(token=settings.slack_bot_token)
copilot = Copilot(settings)

ASK_FOR_CANDIDATE = "Please share the candidate's resume or LinkedIn profile."
NEW_ROLES_RE = re.compile(r"\b(new roles?|roles? (were )?posted|what roles|open roles|any roles)\b", re.I)
FIND_RE = re.compile(r"\b(find|source|recommend)\b.*\bcandidates?\b", re.I)
OWN_RE = re.compile(r"\b(submit (my|a|an) (own )?candidate|submit my own|my own candidate|check (my|a) candidate|score (my|a) candidate)\b", re.I)
YES_RE = re.compile(r"^\s*(yes|yep|yeah|sure|y|submit|go ahead|please do|do it)\b", re.I)
NO_RE = re.compile(r"^\s*(no|nope|not yet|n|cancel|don't|do not)\b", re.I)
URL_RE = re.compile(r"https?://[^\s>|]+")
MENTION_RE = re.compile(r"<@[A-Z0-9]+>")

# Per-user conversation state (in memory; fine for a prototype).
STATE: dict[str, dict] = {}
LAST_ROLE: dict[str, str] = {}
HISTORY: dict[str, deque] = defaultdict(lambda: deque(maxlen=6))


# ------------------------------------------------------------------ helpers

def post(client, channel: str, text: str, thread_ts: str | None = None, blocks: list | None = None) -> None:
    client.chat_postMessage(channel=channel, text=strip_emojis(text), thread_ts=thread_ts, blocks=blocks)


def run_in_background(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


def resolve_role(user_id: str, text: str) -> RoleBrief | None:
    brief = copilot.kb.resolve(text)
    if brief:
        return brief
    if user_id in LAST_ROLE:
        return copilot.kb.get(LAST_ROLE[user_id])
    matches = copilot.matching_briefs(user_id)
    return matches[0] if len(matches) == 1 else None


def which_role_text(user_id: str) -> str:
    names = "\n".join(f"- {b.label}" for b in copilot.matching_briefs(user_id)) or "- No roles are posted yet"
    return f"Which role is this for?\n{names}"


def file_text(files: list[dict]) -> str:
    """Text from files shared in Slack (PDF resumes or plain text)."""
    parts = []
    for item in files or []:
        url = item.get("url_private_download") or item.get("url_private")
        if not url:
            continue
        try:
            response = requests.get(url, headers={"Authorization": f"Bearer {settings.slack_bot_token}"}, timeout=30)
            response.raise_for_status()
        except requests.RequestException:
            log.exception("Could not download shared file")
            continue
        name = (item.get("name") or "").lower()
        if item.get("mimetype") == "application/pdf" or name.endswith(".pdf"):
            parts.append(extract_pdf_text(response.content))
        elif (item.get("mimetype") or "").startswith("text/"):
            parts.append(response.text)
    return "\n\n".join(p for p in parts if p.strip())


def send_role_alerts_to_user(client, user_id: str, channel: str, thread_ts: str | None) -> None:
    profile = copilot.profile_for(user_id)
    if not profile:
        post(client, channel, "I don't have a recruiter profile for you yet, so I can't match roles. "
             "Ask the team to add one in recruiters/.", thread_ts)
        return
    alerts = copilot.alerts_for(profile, only_new=False)
    if not alerts:
        post(client, channel, "No new roles match your profile right now.", thread_ts)
        return
    for brief, text, blocks in alerts:
        post(client, channel, text, thread_ts, blocks)
        LAST_ROLE[user_id] = brief.role_id
    copilot.alert_state.mark(user_id, [b.role_id for b, _, _ in alerts])


def start_find(client, user_id: str, brief: RoleBrief, channel: str, thread_ts: str | None) -> None:
    LAST_ROLE[user_id] = brief.role_id
    post(client, channel, f"Searching public profiles for *{brief.label}*. This can take a minute.", thread_ts)

    def work():
        try:
            post(client, channel, copilot.find_candidates(brief), thread_ts)
        except Exception:
            log.exception("Candidate search failed")
            post(client, channel, "Something went wrong while searching. Please try again in a few minutes.", thread_ts)

    run_in_background(work)


def start_submit_own(client, user_id: str, brief: RoleBrief, channel: str, thread_ts: str | None) -> None:
    LAST_ROLE[user_id] = brief.role_id
    STATE[user_id] = {"mode": "await_candidate", "role_id": brief.role_id}
    post(client, channel, f"{ASK_FOR_CANDIDATE} (Role: {brief.label})", thread_ts)


def score_and_reply(client, user_id: str, brief: RoleBrief, candidate_text: str, channel: str,
                    thread_ts: str | None) -> None:
    try:
        result = copilot.score_candidate(brief, candidate_text)
    except Exception:
        log.exception("Scoring failed")
        post(client, channel, "I couldn't score this candidate just now. Please try again in a minute.", thread_ts)
        return
    text = format_score(result)
    if result.can_submit:
        STATE[user_id] = {"mode": "await_confirm", "role_id": brief.role_id, "result": result}
        question = f"Would you like me to submit {result.candidate_name} for this role?"
        post(client, channel, f"{text}\n\n{question}", thread_ts, submit_confirm_blocks(f"{text}\n\n{question}"))
    else:
        STATE.pop(user_id, None)
        post(client, channel, f"{text}\n\nI'd hold off on submitting this one. The final call is yours.", thread_ts)


def do_submit(client, user_id: str, channel: str, thread_ts: str | None) -> None:
    state = STATE.pop(user_id, None)
    if not state or state.get("mode") != "await_confirm":
        post(client, channel, "There's no candidate waiting to be submitted.", thread_ts)
        return
    brief = copilot.kb.get(state["role_id"])
    if not brief:
        post(client, channel, "I can't find that role anymore. It may have been closed.", thread_ts)
        return
    post(client, channel, copilot.submit(state["result"], brief, user_id), thread_ts)


def recruiter_name(client, user_id: str) -> str:
    profile = copilot.profile_for(user_id)
    if profile:
        return profile.name
    try:
        user = client.users_info(user=user_id)["user"]
        return user.get("profile", {}).get("display_name") or user.get("real_name") or user_id
    except Exception:
        return user_id


# ------------------------------------------------------------------ router

def handle_text(client, user_id: str, text: str, channel: str, thread_ts: str | None,
                files: list[dict] | None = None) -> None:
    text = MENTION_RE.sub("", text or "").strip()
    state = STATE.get(user_id)

    # Waiting for a yes/no on submitting a scored candidate.
    if state and state["mode"] == "await_confirm":
        if YES_RE.match(text):
            do_submit(client, user_id, channel, thread_ts)
            return
        if NO_RE.match(text):
            STATE.pop(user_id, None)
            post(client, channel, "No problem. I won't submit them. The final call is yours.", thread_ts)
            return
        STATE.pop(user_id, None)

    # Waiting for a resume or LinkedIn profile.
    if state and state["mode"] == "await_candidate":
        brief = copilot.kb.get(state["role_id"])
        candidate = "\n\n".join(t for t in [text, file_text(files or [])] if t.strip())
        urls = URL_RE.findall(text)
        if urls and len(URL_RE.sub("", text).split()) < 5 and copilot.search.enabled:
            snippet = copilot.search.lookup_profile(urls[0])
            if snippet:
                candidate += f"\n\nPublic profile summary from search:\n{snippet}"
        if not brief or len(candidate) < 40:
            post(client, channel, "I need a bit more to go on. Please upload the resume (PDF) or paste "
                 "the candidate's LinkedIn profile text.", thread_ts)
            return
        post(client, channel, f"Checking dealbreakers and scoring against the *{brief.label}* scorecard.", thread_ts)
        run_in_background(score_and_reply, client, user_id, brief, candidate, channel, thread_ts)
        return

    if NEW_ROLES_RE.search(text):
        run_in_background(send_role_alerts_to_user, client, user_id, channel, thread_ts)
        return

    if FIND_RE.search(text) or OWN_RE.search(text):
        brief = resolve_role(user_id, text)
        if not brief:
            post(client, channel, which_role_text(user_id), thread_ts)
            return
        if OWN_RE.search(text):
            start_submit_own(client, user_id, brief, channel, thread_ts)
        else:
            start_find(client, user_id, brief, channel, thread_ts)
        return

    if not text:
        post(client, channel, "Hi! Ask me about a role, say \"What new roles were posted?\", "
             "or ask me to find candidates.", thread_ts)
        return

    def answer():
        brief = copilot.kb.resolve(text)
        if brief:
            LAST_ROLE[user_id] = brief.role_id
        history = list(HISTORY[user_id])
        try:
            reply, escalated = copilot.answer(user_id, text, history=history)
        except Exception:
            log.exception("Grok call failed")
            post(client, channel, "I couldn't reach my answer engine just now. Please try again in a minute.", thread_ts)
            return
        HISTORY[user_id].extend([{"role": "user", "content": text}, {"role": "assistant", "content": reply}])
        post(client, channel, reply, thread_ts)
        if escalated:
            role = brief or (copilot.kb.get(LAST_ROLE[user_id]) if user_id in LAST_ROLE else None)
            copilot.escalator.escalate(client, text, user_id, recruiter_name(client, user_id),
                                       role.label if role else None, channel, thread_ts)

    run_in_background(answer)


def handle_founder_reply(client, event: dict) -> bool:
    """A founder answering an escalation in its thread. Returns True if handled."""
    record = copilot.escalator.find_by_thread(event.get("channel"), event.get("thread_ts"))
    if not record or not settings.is_admin(event.get("user", "")):
        return False
    answer = MENTION_RE.sub("", event.get("text", "")).strip()
    if not answer:
        return True
    copilot.escalator.record_answer(record, answer)
    post(client, event["channel"], "Thanks. I added this to the knowledge base and sent it to the recruiter.",
         event.get("thread_ts"))
    post(client, record["reply_channel"],
         f"Update on your question \"{record['question']}\": {copilot.safe(answer)} (Source: Founder answer)",
         record.get("reply_thread_ts"))
    return True


# ------------------------------------------------------------------ events

@app.event("app_mention")
def on_mention(event, client):
    thread_ts = event.get("thread_ts") or event.get("ts")
    if event.get("thread_ts") and handle_founder_reply(client, event):
        return
    handle_text(client, event["user"], event.get("text", ""), event["channel"], thread_ts, event.get("files"))


@app.event("message")
def on_message(event, client, context):
    if event.get("bot_id") or event.get("subtype") not in (None, "file_share"):
        return
    bot_mention = f"<@{context.get('bot_user_id')}>"
    if event.get("channel_type") != "im" and bot_mention in event.get("text", ""):
        return  # app_mention handles it, avoid double replies
    if event.get("thread_ts") and handle_founder_reply(client, event):
        return
    if event.get("channel_type") != "im":
        return  # channel messages are handled through app_mention
    handle_text(client, event["user"], event.get("text", ""), event["channel"], event.get("thread_ts"),
                event.get("files"))


# ------------------------------------------------------------------ buttons

def _button_context(body: dict) -> tuple[str, str, str | None, str]:
    user_id = body["user"]["id"]
    channel = body["channel"]["id"]
    message = body.get("message", {})
    thread_ts = message.get("thread_ts")
    value = body["actions"][0].get("value", "")
    return user_id, channel, thread_ts, value


@app.action("find_candidates")
def on_find_candidates(ack, body, client):
    ack()
    user_id, channel, thread_ts, role_id = _button_context(body)
    brief = copilot.kb.get(role_id)
    if not brief:
        post(client, channel, "That role is no longer in the briefs.", thread_ts)
        return
    start_find(client, user_id, brief, channel, thread_ts)


@app.action("submit_own")
def on_submit_own(ack, body, client):
    ack()
    user_id, channel, thread_ts, role_id = _button_context(body)
    brief = copilot.kb.get(role_id)
    if not brief:
        post(client, channel, "That role is no longer in the briefs.", thread_ts)
        return
    start_submit_own(client, user_id, brief, channel, thread_ts)


@app.action("confirm_submit")
def on_confirm_submit(ack, body, client):
    ack()
    user_id, channel, thread_ts, _ = _button_context(body)
    do_submit(client, user_id, channel, thread_ts)


@app.action("cancel_submit")
def on_cancel_submit(ack, body, client):
    ack()
    user_id, channel, thread_ts, _ = _button_context(body)
    STATE.pop(user_id, None)
    post(client, channel, "No problem. I won't submit them. The final call is yours.", thread_ts)


# ----------------------------------------------------------- slash commands

@app.command("/roles")
def on_roles(ack, command, client, respond):
    ack()
    user_id = command["user_id"]
    if command.get("text", "").strip().lower() == "all":
        briefs = copilot.kb.briefs()
        lines = ["*All open roles*"] + [
            f"- *{b.label}* | {b.get('location')} | Pay: {b.get('salary')} | Reward: {b.get('contrario hire reward')}"
            for b in briefs
        ]
        respond(strip_emojis("\n".join(lines) if briefs else "No roles are posted yet."))
        return
    channel = client.conversations_open(users=user_id)["channel"]["id"]
    respond("Sending the roles that match your profile to our DM.")
    run_in_background(send_role_alerts_to_user, client, user_id, channel, None)


@app.command("/pipeline")
def on_pipeline(ack, command, respond):
    ack()
    text = command.get("text", "").strip()
    brief = copilot.kb.resolve(text) if text else None
    respond(strip_emojis(copilot.pipeline_text(brief)))


ADD_ROLE_CALLBACK = "add_role_modal"


@app.command("/add-role")
def on_add_role(ack, command, client, respond):
    ack()
    if not settings.is_admin(command["user_id"]):
        respond("Only founders can add roles. Recruiters can ask me about any posted role.")
        return
    client.views_open(
        trigger_id=command["trigger_id"],
        view={
            "type": "modal",
            "callback_id": ADD_ROLE_CALLBACK,
            "title": {"type": "plain_text", "text": "Add a role"},
            "submit": {"type": "plain_text", "text": "Add role"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {"type": "input", "block_id": "company", "label": {"type": "plain_text", "text": "Company"},
                 "element": {"type": "plain_text_input", "action_id": "value"}},
                {"type": "input", "block_id": "title", "label": {"type": "plain_text", "text": "Role title"},
                 "element": {"type": "plain_text_input", "action_id": "value"}},
                {"type": "input", "block_id": "category", "label": {"type": "plain_text", "text": "Role category"},
                 "element": {"type": "static_select", "action_id": "value", "options": [
                     {"text": {"type": "plain_text", "text": "Technical"}, "value": "technical"},
                     {"text": {"type": "plain_text", "text": "Non-technical"}, "value": "non-technical"},
                 ]}},
                {"type": "input", "block_id": "brief", "label": {"type": "plain_text", "text": "Role brief"},
                 "hint": {"type": "plain_text", "text": "Paste the full brief. Put confidential notes under a line "
                          "that starts with INTERNAL; the bot never shares them."},
                 "element": {"type": "plain_text_input", "action_id": "value", "multiline": True}},
            ],
        },
    )


@app.view(ADD_ROLE_CALLBACK)
def on_add_role_submit(ack, body, view, client):
    values = view["state"]["values"]
    company = values["company"]["value"]["value"].strip()
    title = values["title"]["value"]["value"].strip()
    category = values["category"]["value"]["selected_option"]["value"]
    brief_text = values["brief"]["value"]["value"]
    if not company or not title:
        ack(response_action="errors", errors={"company": "Company and title are required."})
        return
    ack()
    settings.briefs_dir.mkdir(parents=True, exist_ok=True)
    path = settings.briefs_dir / f"{slugify(company + '-' + title)}.txt"
    path.write_text(
        f"ROLE BRIEF\nCompany: {company}\nRole: {title}\nRole category: {category}\n\n{brief_text}\n",
        encoding="utf-8",
    )
    user_id = body["user"]["id"]
    dm = client.conversations_open(users=user_id)["channel"]["id"]
    post(client, dm, f"Added *{company} - {title}*. Matching recruiters will get an alert now.")
    run_in_background(send_alerts, client, copilot, True, False)


# --------------------------------------------------------------------- main

def main() -> None:
    if settings.enable_scheduler:
        start_scheduler(app.client, copilot)
    log.info("Role Brief Copilot is running in Socket Mode with model %s", settings.grok_model)
    SocketModeHandler(app, settings.slack_app_token).start()


if __name__ == "__main__":
    main()
```

### copilot/__init__.py

```python
"""Role Brief Copilot: a Slack assistant for Contrario recruiters.

Built by Group 8 in TAC 459 (USC) as a capstone prototype for Contrario.
"""

__version__ = "0.1.0"
```

### copilot/config.py

```python
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
```

### copilot/knowledge.py

```python
"""Load role briefs, strip INTERNAL sections, and guard model output.

Founders drop role brief PDFs (or .txt/.md files) into briefs/. Every brief is
split into a public part and an INTERNAL part the moment it is loaded. Only the
public part is ever sent to the model. The INTERNAL part is kept only as a set
of fingerprints (dollar amounts, percentages, and 5-word phrases) so the output
guard can block a reply that somehow repeats internal details.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

REFUSAL_LINE = "That's not something I can share."

BRIEF_SUFFIXES = {".pdf", ".txt", ".md"}
SKIP_FILES = {"readme.md", "founder-answers.md"}

# A line containing the uppercase word INTERNAL starts an internal block.
INTERNAL_START_RE = re.compile(r"\bINTERNAL\b")

# Public headings that end an internal block. If none follows, the internal
# block runs to the end of the document (the safe default).
PUBLIC_HEADINGS = (
    "escalation",
    "about the company",
    "how the company works",
    "why we're hiring",
    "the team you'd join",
    "what you'll do",
    "first 30 / 60 / 90 days",
    "must-haves",
    "nice-to-haves",
    "dealbreakers",
    "ideal candidate profile",
    "candidate targeting",
    "candidate match scorecard",
    "sourcing keywords",
    "interview process",
    "why candidates say yes",
    "recruiter faq",
)
MD_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")

FIELD_KEYS = (
    "Company",
    "Role",
    "Role category",
    "Employment type",
    "Location",
    "Salary",
    "Equity",
    "Visa sponsorship",
    "Contrario hire reward",
    "Bonuses",
    "Openings",
    "Hiring timeline",
)

MONEY_RE = re.compile(r"\$\s?(\d+(?:[.,]\d+)*)\s?([kKmMbB])?(?![a-zA-Z])")
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")
WORD_RE = re.compile(r"[a-z0-9$%.']+")
SHINGLE_SIZE = 5


# ---------------------------------------------------------------- extraction

def extract_pdf_text(source: Path | bytes) -> str:
    """Return the text of a PDF, keeping its layout where pypdf supports it."""
    data = source.read_bytes() if isinstance(source, Path) else source
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        try:
            text = page.extract_text(extraction_mode="layout")
        except TypeError:  # older pypdf without layout mode
            text = page.extract_text()
        pages.append(text or "")
    return "\n".join(pages)


def read_document(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return extract_pdf_text(path)
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------- INTERNAL removal

def _is_public_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or INTERNAL_START_RE.search(stripped):
        return False
    md = MD_HEADING_RE.match(stripped)
    if md:
        return True
    lowered = stripped.lower()
    if lowered.strip(" :.-=[]") in {"end internal", "public", "end of internal"}:
        return True
    if line[:1].isspace() or lowered.startswith(("-", "*", "\u2022")):
        return False
    return any(lowered.startswith(heading) for heading in PUBLIC_HEADINGS)


def split_internal(text: str) -> tuple[str, str]:
    """Split a brief into (public_text, internal_text)."""
    public_lines: list[str] = []
    internal_lines: list[str] = []
    in_internal = False
    for line in text.splitlines():
        if INTERNAL_START_RE.search(line):
            in_internal = True
            internal_lines.append(line)
            continue
        if in_internal and _is_public_heading(line):
            in_internal = False
        (internal_lines if in_internal else public_lines).append(line)
    public = "\n".join(public_lines).strip() + "\n"
    internal = "\n".join(internal_lines).strip()
    return public, internal


def strip_internal(text: str) -> str:
    return split_internal(text)[0]


# ------------------------------------------------------------- fingerprints

def _money_tokens(text: str) -> set[str]:
    tokens = set()
    for number, suffix in MONEY_RE.findall(text):
        try:
            value = float(number.replace(",", ""))
        except ValueError:
            continue
        multiplier = {"k": 1e3, "m": 1e6, "b": 1e9}.get((suffix or "").lower(), 1)
        tokens.add(f"${int(round(value * multiplier))}")
    return tokens


def _percent_tokens(text: str) -> set[str]:
    return {f"{float(n):g}%" for n in PERCENT_RE.findall(text)}


def _shingles(text: str) -> set[str]:
    words = WORD_RE.findall(text.lower().replace("\u2019", "'"))
    return {
        " ".join(words[i : i + SHINGLE_SIZE])
        for i in range(len(words) - SHINGLE_SIZE + 1)
    }


def text_tokens(text: str) -> set[str]:
    return _money_tokens(text) | _percent_tokens(text) | _shingles(text)


def internal_fingerprints(internal_text: str) -> set[str]:
    """Tokens from INTERNAL lines, minus the header line itself."""
    body = "\n".join(
        line for line in internal_text.splitlines()
        if "DO NOT SHARE" not in line.upper()
    )
    body = INTERNAL_START_RE.sub(" ", body)
    return text_tokens(body)


def guard_output(text: str, fingerprints: set[str]) -> tuple[str, bool]:
    """Return (safe_text, leaked). A leak replaces the reply with the refusal."""
    if not fingerprints or not text:
        return text, False
    if text_tokens(text) & fingerprints:
        return REFUSAL_LINE, True
    return text, False


# -------------------------------------------------------------- brief model

def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "role"


def parse_fields(text: str) -> dict[str, str]:
    """Read 'Key    value' or 'Key: value' rows, including wrapped values."""
    fields: dict[str, str] = {}
    lines = text.splitlines()
    for key in FIELD_KEYS:
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*(?::\s*|\s{{2,}})(\S.*?)\s*$", re.I)
        for i, line in enumerate(lines):
            match = pattern.match(line)
            if not match:
                continue
            value = match.group(1)
            # Wrapped values continue on deeply indented lines.
            for nxt in lines[i + 1 : i + 4]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) >= 20:
                    value += " " + nxt.strip()
                else:
                    break
            fields[key.lower()] = re.sub(r"\s+", " ", value).strip()
            break
    return fields


def _title_and_company(text: str, fields: dict[str, str], fallback: str) -> tuple[str, str]:
    if fields.get("role") and fields.get("company"):
        return fields["role"], fields["company"]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if line.upper().startswith("ROLE BRIEF") and i + 2 < len(lines):
            return lines[i + 1], lines[i + 2]
    return fields.get("role", fallback), fields.get("company", "Unknown company")


@dataclass
class RoleBrief:
    role_id: str
    company: str
    title: str
    source_file: str
    public_text: str
    fields: dict[str, str]
    fingerprints: set[str] = field(default_factory=set)

    @property
    def company_short(self) -> str:
        return re.sub(r"\s*\(.*?\)", "", self.company).strip()

    @property
    def label(self) -> str:
        return f"{self.company_short} - {self.title}"

    @property
    def source_name(self) -> str:
        return f"{self.company_short} brief"

    @property
    def category(self) -> str:
        return self.fields.get("role category", "").lower()

    def get(self, key: str, default: str = "not in the brief") -> str:
        return self.fields.get(key.lower(), default)


def parse_brief(raw_text: str, source_file: str) -> RoleBrief:
    public, internal = split_internal(raw_text)
    fields = parse_fields(public)
    title, company = _title_and_company(public, fields, Path(source_file).stem)
    short_company = re.sub(r"\s*\(.*?\)", "", company).strip()
    return RoleBrief(
        role_id=slugify(f"{short_company}-{title}"),
        company=company,
        title=title,
        source_file=source_file,
        public_text=public,
        fields=fields,
        fingerprints=internal_fingerprints(internal) if internal else set(),
    )


class KnowledgeBase:
    """All role briefs in a folder, reloaded when files change."""

    def __init__(self, briefs_dir: Path):
        self.briefs_dir = Path(briefs_dir)
        self._cache: dict[Path, tuple[float, RoleBrief]] = {}

    def _brief_paths(self) -> list[Path]:
        if not self.briefs_dir.exists():
            return []
        return sorted(
            p for p in self.briefs_dir.iterdir()
            if p.is_file()
            and p.suffix.lower() in BRIEF_SUFFIXES
            and p.name.lower() not in SKIP_FILES
            and not p.name.startswith(".")
        )

    def briefs(self) -> list[RoleBrief]:
        result = []
        seen = set()
        for path in self._brief_paths():
            mtime = path.stat().st_mtime
            cached = self._cache.get(path)
            if not cached or cached[0] != mtime:
                brief = parse_brief(read_document(path), path.name)
                self._cache[path] = (mtime, brief)
            brief = self._cache[path][1]
            if brief.role_id not in seen:
                seen.add(brief.role_id)
                result.append(brief)
        return result

    def get(self, role_id: str) -> RoleBrief | None:
        return next((b for b in self.briefs() if b.role_id == role_id), None)

    def resolve(self, text: str) -> RoleBrief | None:
        """Best guess at which role a message is about, or None."""
        lowered = text.lower()
        best, best_score = None, 0
        for brief in self.briefs():
            score = 0
            company_words = [w for w in re.findall(r"[a-z0-9]+", brief.company_short.lower()) if len(w) > 2]
            if company_words and company_words[0] in lowered:
                score += 3
            if brief.title.lower() in lowered:
                score += 3
            title_words = [w for w in re.findall(r"[a-z0-9]+", brief.title.lower()) if len(w) > 2]
            score += sum(1 for w in title_words if re.search(rf"\b{re.escape(w)}\b", lowered))
            if score > best_score:
                best, best_score = brief, score
        return best if best_score >= 2 else None

    def fingerprints(self) -> set[str]:
        """Internal tokens from every brief that never appear in public text."""
        briefs = self.briefs()
        internal: set[str] = set()
        public: set[str] = set()
        for brief in briefs:
            internal |= brief.fingerprints
            public |= text_tokens(brief.public_text)
        if self.founder_answers_text():
            public |= text_tokens(self.founder_answers_text())
        return internal - public

    def founder_answers_text(self) -> str:
        path = self.briefs_dir / "founder-answers.md"
        if not path.exists():
            return ""
        return strip_internal(path.read_text(encoding="utf-8", errors="replace"))

    def context_text(self, extra_docs: dict[str, str] | None = None) -> str:
        """Public knowledge for the model, each block labeled with its source."""
        blocks = []
        for brief in self.briefs():
            blocks.append(f"=== SOURCE: {brief.source_name} ({brief.label}) ===\n{brief.public_text}")
        answers = self.founder_answers_text()
        if answers.strip():
            blocks.append(f"=== SOURCE: Founder answers ===\n{answers}")
        for name, text in (extra_docs or {}).items():
            blocks.append(f"=== SOURCE: {name} ===\n{strip_internal(text)}")
        return "\n\n".join(blocks)
```

### copilot/prompts.py

```python
"""Every prompt the Role Brief Copilot sends to Grok."""

from __future__ import annotations

from .escalation import ESCALATION_LINE
from .knowledge import REFUSAL_LINE

SYSTEM_PROMPT = f"""You are Role Brief Copilot, a Slack assistant for Contrario, a YC-backed recruiting marketplace where independent recruiters submit candidates to startup roles. A recruiter only earns the Contrario reward when they submit a good candidate, so your job is to raise each recruiter's odds of closing: help them understand roles, find strong candidates, and check their own candidates before they submit.

Knowledge:
- Answer only from the SOURCE blocks you are given: role briefs, the pipeline record, and founder answers. Never guess. Never make up numbers, people, companies, or links.
- Anything marked INTERNAL has already been removed. If a recruiter asks about confidential details (stretch budgets, other candidates in process, confidential customers, anything a brief says not to share), reply exactly: "{REFUSAL_LINE}"
- If the answer is not in the sources, reply exactly: "{ESCALATION_LINE}" Do not add a guess after it.

Style:
- Friendly, clear, brief. Simple Slack formatting: *bold*, short lines, "- " bullets. No headers, no tables.
- Never use emojis.

Rules:
- Never judge candidates on age, gender, race, religion, nationality, disability, or any other protected trait. Schools are a plus only, never a requirement.
- Use public, professional information only.
- The final submission decision is always the recruiter's. You recommend; they decide.
"""

ANSWER_INSTRUCTIONS = f"""Answer the recruiter's question about a role or the pipeline.
- Use 1-3 sentences.
- End with the source in parentheses, using the SOURCE name, for example "(Source: Wispr AI brief)" or "(Source: Pipeline record)".
- Pipeline questions (how many candidates, stages, who is in process) come only from the Pipeline record source. Never use a role brief to answer a pipeline question.
- If the recruiter does not say which role and it is unclear, ask which role they mean and list the role names.
- If the answer is not in the sources, reply with exactly: "{ESCALATION_LINE}"
- If they ask for confidential or internal details, reply with exactly: "{REFUSAL_LINE}"
"""


def answer_prompt(context: str, recruiter_summary: str, question: str) -> str:
    return (
        f"{ANSWER_INSTRUCTIONS}\n"
        f"Recruiter profile:\n{recruiter_summary}\n\n"
        f"SOURCES:\n{context}\n\n"
        f"Recruiter question: {question}"
    )


def alert_why_prompt(recruiter_summary: str, brief_text: str) -> str:
    return (
        "Write ONE short sentence (max 25 words) telling this recruiter why the role below matches their profile. "
        "Base it only on the recruiter profile and the role brief. Speak to the recruiter as 'you'. "
        "No emojis, no quotes, no preamble.\n\n"
        f"Recruiter profile:\n{recruiter_summary}\n\n"
        f"Role brief:\n{brief_text}"
    )


def search_plan_prompt(brief_text: str) -> str:
    return (
        "Plan a public web search for candidates for the role below, using its CANDIDATE TARGETING section, "
        "must-haves, and sourcing keywords.\n"
        "Return ONLY JSON in this shape:\n"
        '{"queries": ["...", "...", "..."], "do_not_source": ["company names from the Do not source from row"]}\n'
        "Rules:\n"
        "- 3 or 4 queries. Each query must start with site:linkedin.com/in and combine a target title with "
        "1-3 target past companies and a must-have skill, using quotes and OR, for example: "
        'site:linkedin.com/in "iOS Engineer" (Superhuman OR Notion) Swift\n'
        "- Never include age, gender, race, religion, nationality, or other protected traits in a query.\n"
        "- do_not_source lists company names only.\n\n"
        f"Role brief:\n{brief_text}"
    )


def rank_candidates_prompt(brief_text: str, numbered_profiles: str) -> str:
    return (
        "Below are public search results (title and snippet only) for possible candidates, and a role brief. "
        "Pick up to 5 people who best fit the role's CANDIDATE TARGETING section and scorecard.\n"
        "Rules:\n"
        "- Use ONLY the people listed below, referenced by their index. Never add anyone. Never write a link.\n"
        "- full_name must be copied exactly as it appears in that result's title.\n"
        "- Use only facts in that result's title and snippet. If a fact is unknown, say it is not shown.\n"
        "- Skip anyone who currently works at a Do not source from company, and anyone with an obvious dealbreaker.\n"
        "- Score each scorecard criterion from 0 to its weight. Unknown evidence earns partial or zero points, never full.\n"
        "- Ignore protected traits entirely. Schools are a plus only.\n"
        "- If fewer than 5 fit, return fewer.\n"
        "Return ONLY JSON:\n"
        '{"candidates": [{"index": 0, "full_name": "...", "why": "one sentence", '
        '"bullets": ["Past companies and current role", "Years of experience and key skills", '
        '"How they match the must-haves"], '
        '"criteria": [{"name": "criterion", "weight": 30, "points": 24}]}]}\n\n'
        f"Role brief:\n{brief_text}\n\n"
        f"Search results:\n{numbered_profiles}"
    )


def score_own_prompt(brief_text: str, candidate_text: str) -> str:
    return (
        "A recruiter wants to submit their own candidate for the role below. Check the role's Dealbreakers first, "
        "then score the candidate with the role's Candidate match scorecard.\n"
        "Rules:\n"
        "- Use only what the resume or profile says. Do not assume skills that are not shown; list them as gaps.\n"
        "- Score each scorecard criterion from 0 to its weight.\n"
        "- dealbreakers_hit lists only dealbreakers the candidate clearly hits. Missing info is a gap, not a dealbreaker.\n"
        "- Never consider age, gender, race, religion, nationality, disability, or other protected traits. "
        "Schools are a plus only.\n"
        "- strengths: 2 or 3 short bullets. gaps: 1 to 3 short bullets, including missing info.\n"
        "Return ONLY JSON:\n"
        '{"candidate_name": "...", "dealbreakers_hit": [], '
        '"criteria": [{"name": "criterion", "weight": 30, "points": 25, "reason": "..."}], '
        '"strengths": ["..."], "gaps": ["..."]}\n\n'
        f"Role brief:\n{brief_text}\n\n"
        f"Candidate resume or profile:\n{candidate_text}"
    )
```

### copilot/grok_client.py

```python
"""Thin wrapper around the xAI Grok API (OpenAI-compatible, https://api.x.ai/v1)."""

from __future__ import annotations

import logging

from openai import BadRequestError, OpenAI

from .scoring import parse_json_block

log = logging.getLogger(__name__)


class GrokClient:
    def __init__(self, api_key: str, base_url: str = "https://api.x.ai/v1", model: str = "grok-4.7",
                 timeout: float = 180.0):
        if not api_key:
            raise ValueError("XAI_API_KEY is not set")
        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def chat(self, system: str, user: str, history: list[dict] | None = None,
             temperature: float | None = 0.2) -> str:
        messages = [{"role": "system", "content": system}]
        messages += history or []
        messages.append({"role": "user", "content": user})
        kwargs = {"model": self.model, "messages": messages}
        if temperature is not None:
            kwargs["temperature"] = temperature
        try:
            response = self._client.chat.completions.create(**kwargs)
        except BadRequestError:
            if "temperature" not in kwargs:
                raise
            # Some models reject sampling parameters; retry with defaults.
            kwargs.pop("temperature")
            response = self._client.chat.completions.create(**kwargs)
        return (response.choices[0].message.content or "").strip()

    def chat_json(self, system: str, user: str) -> dict:
        """Ask for JSON and parse it, retrying once with a stricter reminder."""
        reply = self.chat(system, user, temperature=0.1)
        try:
            return parse_json_block(reply)
        except (ValueError, TypeError):
            log.warning("Model reply was not JSON, retrying once")
            reply = self.chat(system, user + "\n\nReturn ONLY valid JSON. No prose.", temperature=0.0)
            return parse_json_block(reply)
```

### copilot/matching.py

```python
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
```

### copilot/scoring.py

````python
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
````

### copilot/pipeline.py

```python
"""A tiny JSON pipeline store (data/pipeline.json).

In the prototype a submission is recorded here, not on Contrario's site.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path


class PipelineStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {"roles": {}}
        data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        data.setdefault("roles", {})
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, self.path)

    def count(self, role_id: str) -> int:
        with self._lock:
            return len(self._read()["roles"].get(role_id, {}).get("candidates", []))

    def add(self, role_id: str, role_label: str, candidate_name: str, score: int,
            verdict: str, submitted_by: str) -> int:
        """Record a submission and return the new candidate count for the role."""
        with self._lock:
            data = self._read()
            role = data["roles"].setdefault(role_id, {"role": role_label, "candidates": []})
            role["role"] = role_label
            role["candidates"].append({
                "name": candidate_name,
                "score": score,
                "verdict": verdict,
                "submitted_by": submitted_by,
                "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "stage": "Submitted",
            })
            self._write(data)
            return len(role["candidates"])

    def summary(self) -> list[tuple[str, str, int]]:
        with self._lock:
            roles = self._read()["roles"]
        return [(rid, r.get("role", rid), len(r.get("candidates", []))) for rid, r in roles.items()]

    def as_knowledge_text(self) -> str:
        """Pipeline facts for the model: counts and stages only, no names."""
        rows = []
        with self._lock:
            roles = self._read()["roles"]
        for role_id, role in roles.items():
            stages: dict[str, int] = {}
            for cand in role.get("candidates", []):
                stages[cand.get("stage", "Submitted")] = stages.get(cand.get("stage", "Submitted"), 0) + 1
            stage_text = ", ".join(f"{k}: {v}" for k, v in stages.items()) or "none"
            rows.append(f"{role.get('role', role_id)}: {len(role.get('candidates', []))} candidates submitted through the Copilot ({stage_text})")
        if not rows:
            return "No candidates have been submitted through the Copilot yet for any role."
        return "\n".join(rows)
```

### copilot/escalation.py

```python
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
```

### copilot/candidate_search.py

```python
"""Public-profile candidate search through a web search API.

Uses SERPER_API_KEY (google.serper.dev) or TAVILY_API_KEY (api.tavily.com).
Only public LinkedIn profile URLs (linkedin.com/in/...) and GitHub profiles are
kept. The bot never logs into LinkedIn or scrapes it; it only uses the title
and snippet the search API returns. If fewer than 5 real people fit, the bot
returns fewer than 5. It never invents a person or a link.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

LINKEDIN_RE = re.compile(r"^https?://([a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+/?$")
GITHUB_RE = re.compile(r"^https?://github\.com/[A-Za-z0-9\-]+/?$")
GITHUB_RESERVED = {"topics", "orgs", "features", "about", "pricing", "search", "collections", "sponsors"}


@dataclass
class SearchHit:
    title: str
    link: str
    snippet: str


def is_public_profile(link: str) -> bool:
    link = link.split("?")[0]
    if LINKEDIN_RE.match(link):
        return True
    if GITHUB_RE.match(link):
        return link.rstrip("/").rsplit("/", 1)[-1].lower() not in GITHUB_RESERVED
    return False


def filter_public_profiles(hits: list[SearchHit], do_not_source: list[str]) -> list[SearchHit]:
    """Keep unique public profiles; drop anyone whose result names a do-not-source company."""
    blocked = [c.lower() for c in do_not_source if c and len(c) > 2]
    seen: set[str] = set()
    kept: list[SearchHit] = []
    for hit in hits:
        link = hit.link.split("?")[0].rstrip("/")
        if not is_public_profile(link) or link in seen:
            continue
        text = f"{hit.title} {hit.snippet}".lower()
        if any(company in text for company in blocked):
            continue
        seen.add(link)
        kept.append(SearchHit(title=hit.title, link=link, snippet=hit.snippet))
    return kept


class CandidateSearch:
    def __init__(self, serper_api_key: str = "", tavily_api_key: str = "", timeout: int = 20):
        self.serper_api_key = serper_api_key
        self.tavily_api_key = tavily_api_key
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.serper_api_key or self.tavily_api_key)

    def search(self, query: str, num: int = 10) -> list[SearchHit]:
        try:
            if self.serper_api_key:
                return self._serper(query, num)
            if self.tavily_api_key:
                return self._tavily(query, num)
        except requests.RequestException:
            log.exception("Search failed for %r", query)
        return []

    def _serper(self, query: str, num: int) -> list[SearchHit]:
        response = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": self.serper_api_key, "Content-Type": "application/json"},
            json={"q": query, "num": num},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return [
            SearchHit(item.get("title", ""), item.get("link", ""), item.get("snippet", ""))
            for item in response.json().get("organic", [])
        ]

    def _tavily(self, query: str, num: int) -> list[SearchHit]:
        response = requests.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {self.tavily_api_key}"},
            json={
                "query": query,
                "max_results": num,
                "include_domains": ["linkedin.com", "github.com"],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return [
            SearchHit(item.get("title", ""), item.get("url", ""), item.get("content", ""))
            for item in response.json().get("results", [])
        ]

    def lookup_profile(self, url: str) -> str:
        """Public search snippet for a LinkedIn/GitHub URL a recruiter pasted."""
        hits = self.search(f'"{url.rstrip("/")}"', num=5)
        clean = url.split("?")[0].rstrip("/").lower()
        for hit in hits:
            if hit.link.split("?")[0].rstrip("/").lower() == clean:
                return f"{hit.title}\n{hit.snippet}"
        return ""
```

### copilot/formatting.py

```python
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
```

### copilot/service.py

```python
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
```

### copilot/scheduler.py

```python
"""Daily role alerts (weekdays 9 AM PT) and instant alerts for new briefs."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .service import Copilot

log = logging.getLogger(__name__)

NO_MATCHES = "Good morning. No new roles match your profile right now."


def dm_channel(client, user_id: str) -> str:
    return client.conversations_open(users=user_id)["channel"]["id"]


def send_alerts(client, copilot: Copilot, only_new: bool, say_when_empty: bool) -> None:
    """DM each recruiter the roles that match their profile."""
    for user_id, profile in copilot.profiles().items():
        try:
            alerts = copilot.alerts_for(profile, only_new=only_new)
            if not alerts and not say_when_empty:
                continue
            channel = dm_channel(client, user_id)
            if not alerts:
                client.chat_postMessage(channel=channel, text=NO_MATCHES)
                continue
            for brief, text, blocks in alerts:
                client.chat_postMessage(channel=channel, text=text, blocks=blocks)
            copilot.alert_state.mark(user_id, [brief.role_id for brief, _, _ in alerts])
        except Exception:
            log.exception("Could not send role alerts to %s", user_id)


def start_scheduler(client, copilot: Copilot) -> BackgroundScheduler:
    settings = copilot.settings
    tz = ZoneInfo(settings.alert_timezone)
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        send_alerts,
        CronTrigger(day_of_week="mon-fri", hour=settings.alert_hour, minute=settings.alert_minute, timezone=tz),
        kwargs={"client": client, "copilot": copilot, "only_new": True, "say_when_empty": True},
        id="daily_role_alerts",
        replace_existing=True,
    )
    if settings.brief_scan_minutes > 0:
        scheduler.add_job(
            send_alerts,
            IntervalTrigger(minutes=settings.brief_scan_minutes, timezone=tz),
            kwargs={"client": client, "copilot": copilot, "only_new": True, "say_when_empty": False},
            id="instant_role_alerts",
            replace_existing=True,
        )
    scheduler.start()
    log.info("Scheduler started: daily alerts weekdays %02d:%02d %s",
             settings.alert_hour, settings.alert_minute, settings.alert_timezone)
    return scheduler
```

### recruiters/saf.json

```json
{
  "slack_user_id": "U0YOURSLACKID",
  "name": "Saf",
  "title": "Technical Recruiter",
  "specialty": "Technical recruiting: engineering and technical roles",
  "based_in": "Los Angeles, CA (recruits nationwide and remote)",
  "company_stages": "Big tech and startups up to Series B",
  "role_types": "Software engineers (iOS, mobile, backend, full-stack), ML/AI engineers, technical product roles",
  "seniority": "Mid-level to senior (3-10 years of experience)",
  "industries": ["AI", "consumer apps", "developer tools", "B2B SaaS"],
  "background": [
    "Technical recruiter with experience hiring for both big tech companies and fast-growing Series B startups.",
    "Knows how to move engineers from big tech into early-stage roles: what motivates them, and which startups fit their goals.",
    "Comfortable screening for technical depth (stack, shipped products, scale of systems)."
  ],
  "candidate_network": [
    "Engineers from big tech companies looking for more ownership at a startup.",
    "Engineers at seed to Series B startups looking for their next high-growth role.",
    "Strongest in mobile and full-stack engineering talent."
  ],
  "alert_preferences": {
    "alert_when": "A new role is posted that matches my specialty: engineering or technical roles at big tech or seed to Series B companies",
    "dont_alert_for": "Sales, GTM, operations, or non-technical roles",
    "alert_categories": ["technical"],
    "skip_categories": ["non-technical"],
    "alert_role_keywords": [
      "engineer", "engineering", "developer", "software", "ios", "android", "mobile",
      "backend", "back-end", "frontend", "front-end", "full-stack", "full stack",
      "ml", "machine learning", "ai engineer", "data engineer", "technical product", "product engineer"
    ],
    "skip_role_keywords": [
      "sales", "gtm", "go-to-market", "account executive", "operations", "bizops",
      "strategic projects", "strategy", "marketing", "customer success", "chief of staff", "recruiter"
    ],
    "frequency": "Daily digest at 9 AM PT on weekdays",
    "where": "Slack direct message"
  }
}
```

### briefs/README.md

````markdown
# Role briefs

Drop each role brief here. The bot reads every `.pdf`, `.txt`, and `.md` file in
this folder (except this README) and reloads a file when it changes.

How founders add a role:

1. Drop the brief PDF into this folder, or
2. Run `/add-role` in Slack and paste the brief. It is saved here as a `.txt` file.

What a brief should contain (the TAC 459 sample briefs follow this layout):

- A first line `ROLE BRIEF`, then the role title, then the company name
  (or `Company:` and `Role:` lines).
- Key rows such as `Location`, `Salary`, `Equity`, `Visa sponsorship`,
  `Contrario hire reward`, `Bonuses`, `Openings`, and `Hiring timeline`.
- Must-haves, Dealbreakers, Candidate targeting (including "Do not source from"),
  a Candidate match scorecard with weights that add up to 100, the interview
  process, and a Recruiter FAQ.
- Optional `Role category: technical` or `Role category: non-technical`, which
  overrides keyword matching for recruiter alerts.

Confidential notes: put them under a line containing the uppercase word
`INTERNAL` (for example `INTERNAL: DO NOT SHARE WITH RECRUITERS`). Everything
from that line to the next public heading (such as `Escalation:`) or the end of
the file is removed before any text reaches the model, and replies are checked
so internal numbers and phrases can't slip out.

`founder-answers.md` is created automatically when a founder answers an
escalated question in Slack. The bot uses it as an extra source.

Real brief PDFs are not committed to the repo (see `.gitignore`). Keep them
local or in a private drive.
````

### data/pipeline.json

```json
{
  "roles": {}
}
```

### tests/test_knowledge.py

```python
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
```

### tests/test_scoring.py

````python
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
````

### tests/test_matching_and_pipeline.py

```python
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
```

### requirements.txt

```text
slack_bolt>=1.21,<2
slack_sdk>=3.33,<4
openai>=1.50
pypdf>=4.0
python-dotenv>=1.0
APScheduler>=3.10,<4
requests>=2.31
tzdata>=2024.1
pytest>=8.0
```

### .env.example

```bash
# Slack (Socket Mode)
SLACK_BOT_TOKEN=xoxb-YOUR-BOT-TOKEN
SLACK_APP_TOKEN=xapp-YOUR-APP-LEVEL-TOKEN

# Grok API (xAI, OpenAI-compatible)
XAI_API_KEY=xai-YOUR-API-KEY
XAI_BASE_URL=https://api.x.ai/v1
GROK_MODEL=grok-4.7

# Founder escalation (the live prototype ran with this off: nobody was tagged)
ENABLE_FOUNDER_ESCALATION=true
FOUNDER_SLACK_USER_ID=U0FOUNDERID
FOUNDER_CHANNEL_ID=C0FOUNDERCHANNEL
ADMIN_SLACK_USER_IDS=U0FOUNDERID

# Candidate search (optional; set one). Public profiles only.
SERPER_API_KEY=YOUR-SERPER-KEY
TAVILY_API_KEY=

# Daily role alerts (weekdays)
ALERT_TIMEZONE=America/Los_Angeles
ALERT_HOUR=9
ALERT_MINUTE=0
# Minutes between scans of briefs/ for instant new-role alerts (0 = off)
BRIEF_SCAN_MINUTES=0
ENABLE_SCHEDULER=true

# Folders (relative to the repo root)
BRIEFS_DIR=briefs
RECRUITERS_DIR=recruiters
DATA_DIR=data
```

### manifest.yml

```yaml
display_information:
  name: Role Brief Copilot
  description: Role alerts, role answers, and candidate checks for Contrario recruiters.
  background_color: "#1a1a1a"
  long_description: Role Brief Copilot helps Contrario recruiters understand startup roles, get daily alerts for roles that match their profile, find real candidates from public profiles, and score their own candidates before submitting. Built by Group 8 in TAC 459 at USC.
features:
  app_home:
    home_tab_enabled: false
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
  bot_user:
    display_name: Role Brief Copilot
    always_online: true
  slash_commands:
    - command: /roles
      description: Show roles that match your recruiter profile
      usage_hint: "[all]"
      should_escape: false
    - command: /add-role
      description: Founders - add a new role brief
      should_escape: false
    - command: /pipeline
      description: Show pipeline counts for a role
      usage_hint: "[company or role]"
      should_escape: false
oauth_config:
  scopes:
    bot:
      - app_mentions:read
      - channels:history
      - groups:history
      - chat:write
      - commands
      - files:read
      - im:history
      - im:read
      - im:write
      - users:read
settings:
  event_subscriptions:
    bot_events:
      - app_mention
      - message.channels
      - message.groups
      - message.im
  interactivity:
    is_enabled: true
  org_deploy_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
```

### pytest.ini

```ini
[pytest]
testpaths = tests
pythonpath = .
```

### .gitignore

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/

# Secrets
.env

# Real role briefs stay local (they can contain INTERNAL notes)
briefs/*
!briefs/README.md

# Runtime state
data/alert_state.json
data/escalations.json
data/*.tmp

# OS
.DS_Store
```

## System Prompt

All prompts live in `copilot/prompts.py`. Every call sends the shared system prompt plus one task prompt. Text in braces is filled in at runtime. The role brief text is always the public part: INTERNAL sections are removed before any prompt is built.

### 1. System prompt (sent with every call)

```text
You are Role Brief Copilot, a Slack assistant for Contrario, a YC-backed recruiting marketplace where independent recruiters submit candidates to startup roles. A recruiter only earns the Contrario reward when they submit a good candidate, so your job is to raise each recruiter's odds of closing: help them understand roles, find strong candidates, and check their own candidates before they submit.

Knowledge:
- Answer only from the SOURCE blocks you are given: role briefs, the pipeline record, and founder answers. Never guess. Never make up numbers, people, companies, or links.
- Anything marked INTERNAL has already been removed. If a recruiter asks about confidential details (stretch budgets, other candidates in process, confidential customers, anything a brief says not to share), reply exactly: "That's not something I can share."
- If the answer is not in the sources, reply exactly: "I don't have that info yet. I've flagged it for the founder." Do not add a guess after it.

Style:
- Friendly, clear, brief. Simple Slack formatting: *bold*, short lines, "- " bullets. No headers, no tables.
- Never use emojis.

Rules:
- Never judge candidates on age, gender, race, religion, nationality, disability, or any other protected trait. Schools are a plus only, never a requirement.
- Use public, professional information only.
- The final submission decision is always the recruiter's. You recommend; they decide.
```

### 2. Role and pipeline answers

```text
Answer the recruiter's question about a role or the pipeline.
- Use 1-3 sentences.
- End with the source in parentheses, using the SOURCE name, for example "(Source: Wispr AI brief)" or "(Source: Pipeline record)".
- Pipeline questions (how many candidates, stages, who is in process) come only from the Pipeline record source. Never use a role brief to answer a pipeline question.
- If the recruiter does not say which role and it is unclear, ask which role they mean and list the role names.
- If the answer is not in the sources, reply with exactly: "I don't have that info yet. I've flagged it for the founder."
- If they ask for confidential or internal details, reply with exactly: "That's not something I can share."

Recruiter profile:
{recruiter profile JSON}

SOURCES:
{SOURCE blocks: public role briefs, Pipeline record, Founder answers}

Recruiter question: {recruiter question}
```

### 3. Daily role alerts ("Why it matches you" sentence)

The alert itself is a fixed template filled from the brief, so pay, location, and reward are always copied word for word from the brief:

```text
New role that matches your profile:
*[Company] - [Role]*
Location: [location] | Pay: [salary] | Contrario reward: [amount]
Why it matches you: [one sentence]

Would you like me to find candidates for this role, or would you like to submit your own?
```

The model writes only the one sentence:

```text
Write ONE short sentence (max 25 words) telling this recruiter why the role below matches their profile. Base it only on the recruiter profile and the role brief. Speak to the recruiter as 'you'. No emojis, no quotes, no preamble.

Recruiter profile:
{recruiter profile JSON}

Role brief:
{public role brief}
```

If nothing matches, the bot says: "No new roles match your profile right now."

### 4. Find candidates (two calls)

Search plan:

```text
Plan a public web search for candidates for the role below, using its CANDIDATE TARGETING section, must-haves, and sourcing keywords.
Return ONLY JSON in this shape:
{"queries": ["...", "...", "..."], "do_not_source": ["company names from the Do not source from row"]}
Rules:
- 3 or 4 queries. Each query must start with site:linkedin.com/in and combine a target title with 1-3 target past companies and a must-have skill, using quotes and OR, for example: site:linkedin.com/in "iOS Engineer" (Superhuman OR Notion) Swift
- Never include age, gender, race, religion, nationality, or other protected traits in a query.
- do_not_source lists company names only.

Role brief:
{public role brief}
```

Ranking the real search results:

```text
Below are public search results (title and snippet only) for possible candidates, and a role brief. Pick up to 5 people who best fit the role's CANDIDATE TARGETING section and scorecard.
Rules:
- Use ONLY the people listed below, referenced by their index. Never add anyone. Never write a link.
- full_name must be copied exactly as it appears in that result's title.
- Use only facts in that result's title and snippet. If a fact is unknown, say it is not shown.
- Skip anyone who currently works at a Do not source from company, and anyone with an obvious dealbreaker.
- Score each scorecard criterion from 0 to its weight. Unknown evidence earns partial or zero points, never full.
- Ignore protected traits entirely. Schools are a plus only.
- If fewer than 5 fit, return fewer.
Return ONLY JSON:
{"candidates": [{"index": 0, "full_name": "...", "why": "one sentence", "bullets": ["Past companies and current role", "Years of experience and key skills", "How they match the must-haves"], "criteria": [{"name": "criterion", "weight": 30, "points": 24}]}]}

Role brief:
{public role brief}

Search results:
{numbered search results: [index] title / snippet}
```

The code then drops any pick whose name is not in that result's title, takes the link from the search result (never from the model), totals the scorecard points, and formats each person as:

```text
*[Full name]*
LinkedIn: [link]
Why they are a great fit: [one sentence]
- [Past companies and current role]
- [Years of experience and key skills]
- [How they match the role's must-haves]
Score: [X]/100 (role scorecard)
```

### 5. Submit your own candidate

The bot first asks: "Please share the candidate's resume or LinkedIn profile." Then:

```text
A recruiter wants to submit their own candidate for the role below. Check the role's Dealbreakers first, then score the candidate with the role's Candidate match scorecard.
Rules:
- Use only what the resume or profile says. Do not assume skills that are not shown; list them as gaps.
- Score each scorecard criterion from 0 to its weight.
- dealbreakers_hit lists only dealbreakers the candidate clearly hits. Missing info is a gap, not a dealbreaker.
- Never consider age, gender, race, religion, nationality, disability, or other protected traits. Schools are a plus only.
- strengths: 2 or 3 short bullets. gaps: 1 to 3 short bullets, including missing info.
Return ONLY JSON:
{"candidate_name": "...", "dealbreakers_hit": [], "criteria": [{"name": "criterion", "weight": 30, "points": 25, "reason": "..."}], "strengths": ["..."], "gaps": ["..."]}

Role brief:
{public role brief}

Candidate resume or profile:
{resume PDF text or pasted LinkedIn profile}
```

The code computes the total and the verdict: 75+ "Good fit, ready to submit", 55-74 "Possible fit, review first", below 55 or any dealbreaker "Not a fit". The reply:

```text
*[Candidate name] - [Role]*
Score: [X]/100
Verdict: [verdict]
Why:
- [Strength 1]
- [Strength 2]
Gaps:
- [Gap or missing info]
```

At 55 or higher (and no dealbreaker) it asks "Would you like me to submit [name] for this role?" On yes: "[Name] submitted for [Company] - [Role]. The pipeline now has [X] candidates."

### Fixed lines

- Not in the files: "I don't have that info yet. I've flagged it for the founder."
- INTERNAL or confidential: "That's not something I can share."

## Slack App Setup

The fastest way is the manifest: https://api.slack.com/apps, then **Create New App**, **From an app manifest**, pick the workspace, and paste `manifest.yml`. Every setting it applies is listed below.

**Basic information**
- App name and bot display name: Role Brief Copilot
- Always show the bot as online: on

**Socket Mode: ON**
- Settings, Socket Mode, enable.
- Create an app-level token (Basic Information, App-Level Tokens) with the scope `connections:write`. That is `SLACK_APP_TOKEN` (`xapp-...`).
- No public Request URL is needed for events, interactivity, or slash commands.

**OAuth and Permissions: bot token scopes**

| Scope | Why |
| --- | --- |
| `app_mentions:read` | Receive @mentions in channels |
| `chat:write` | Send replies, alerts, and escalations |
| `commands` | `/roles`, `/add-role`, `/pipeline` |
| `im:history` | Read DMs sent to the bot |
| `im:read` | See DM channel info |
| `im:write` | Open a DM for daily alerts and founder DMs |
| `channels:history` | Read founder replies in escalation threads (public channel) |
| `groups:history` | Same, for a private founder channel |
| `files:read` | Read resume PDFs shared with the bot |
| `users:read` | Look up the recruiter's name for escalations |

Install the app to the workspace and copy the **Bot User OAuth Token**. That is `SLACK_BOT_TOKEN` (`xoxb-...`).

**Event Subscriptions: on**, with bot events:
- `app_mention`
- `message.im`
- `message.channels`
- `message.groups`

**Interactivity and Shortcuts: on.** No Request URL is needed in Socket Mode. Used by the **Find candidates**, **Submit my own**, **Yes, submit**, and **Not yet** buttons, and the `/add-role` modal.

**Slash commands** (no Request URL in Socket Mode)

| Command | What it does | Usage hint |
| --- | --- | --- |
| `/roles` | DMs you the roles that match your profile. `/roles all` lists every open role. | `[all]` |
| `/add-role` | Founders only: opens a modal to paste a new role brief. Saves it to `briefs/` and alerts matching recruiters. | |
| `/pipeline` | Pipeline counts per role, or for one role. | `[company or role]` |

**App Home**
- Messages tab: on
- "Allow users to send Slash commands and messages from the messages tab": on (that is `messages_tab_read_only_enabled: false`)
- Home tab: off

**Other**
- Org-wide deploy: off
- Token rotation: off

After installing, invite the bot to the founder channel (`/invite @Role Brief Copilot`) so it can post escalations and read the founder's thread replies. Put each recruiter's Slack member ID (Profile, three dots, **Copy member ID**) in their file in `recruiters/`.

## Environment Variables

Copy `.env.example` to `.env` and fill it in. Never commit `.env`.

| Name | Example placeholder | Required | Purpose |
| --- | --- | --- | --- |
| `SLACK_BOT_TOKEN` | `xoxb-YOUR-BOT-TOKEN` | Yes | Bot User OAuth Token |
| `SLACK_APP_TOKEN` | `xapp-YOUR-APP-LEVEL-TOKEN` | Yes | App-level token with `connections:write` (Socket Mode) |
| `XAI_API_KEY` | `xai-YOUR-API-KEY` | Yes | Grok API key from console.x.ai |
| `XAI_BASE_URL` | `https://api.x.ai/v1` | No | OpenAI-compatible xAI endpoint |
| `GROK_MODEL` | `grok-4.7` | No | Any chat model your key can use (for example `grok-4.7` or `grok-4`) |
| `ENABLE_FOUNDER_ESCALATION` | `true` | No | Post unanswered questions to the founder. The live prototype ran with this off (`false`). |
| `FOUNDER_SLACK_USER_ID` | `U0FOUNDERID` | For escalation | Founder to tag (and DM if no channel is set) |
| `FOUNDER_CHANNEL_ID` | `C0FOUNDERCHANNEL` | No | Channel for escalations |
| `ADMIN_SLACK_USER_IDS` | `U0FOUNDERID,U0COFOUNDERID` | No | Who may use `/add-role` and answer escalations. Empty with no founder set means everyone. |
| `SERPER_API_KEY` | `YOUR-SERPER-KEY` | For candidate search | Serper.dev Google search key |
| `TAVILY_API_KEY` | `tvly-YOUR-KEY` | Alternative | Used only if no Serper key is set |
| `ALERT_TIMEZONE` | `America/Los_Angeles` | No | Time zone for daily alerts |
| `ALERT_HOUR` | `9` | No | Hour of the weekday alert |
| `ALERT_MINUTE` | `0` | No | Minute of the weekday alert |
| `BRIEF_SCAN_MINUTES` | `0` | No | Scan `briefs/` every N minutes for instant new-role alerts (0 = off) |
| `ENABLE_SCHEDULER` | `true` | No | Turn the alert scheduler on or off |
| `BRIEFS_DIR` | `briefs` | No | Folder for role briefs |
| `RECRUITERS_DIR` | `recruiters` | No | Folder for recruiter profiles |
| `DATA_DIR` | `data` | No | Folder for pipeline and runtime state |

## How to Run

Requires Python 3.10 or newer.

```bash
# 1. Get the code
git clone https://github.com/sadaffsobhanii/TAC459---Prototype-1.git
cd TAC459---Prototype-1

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Configure
cp .env.example .env               # then edit .env with your tokens and keys
# Put your Slack member ID in recruiters/saf.json ("slack_user_id")
cp /path/to/your/role-briefs/*.pdf briefs/

# 5. Run the tests
python -m pytest -q

# 6. Start the bot (Socket Mode, no public URL needed)
python app.py
```

You should see `Role Brief Copilot is running in Socket Mode`. Then DM the bot in Slack: "What new roles were posted?"

Optional: check which Grok models your key can use.

```bash
curl https://api.x.ai/v1/models -H "Authorization: Bearer $XAI_API_KEY"
```

## Example Conversation

These use the public facts in the TAC 459 sample briefs (Wispr AI, Listen Labs, AfterQuery). INTERNAL details are never shown.

**1. Daily role alert (DM, weekdays 9 AM PT, or on request)**

> *Saf:* What new roles were posted?
>
> *Role Brief Copilot:*
> New role that matches your profile:
> *Wispr AI - Remote iOS Engineer*
> Location: Worldwide. Must overlap at least 4 hours/day with Pacific Time. | Pay: $84,000 – $120,000 (varies by location and level) | Contrario reward: $8,400 – $12,000 (10% of first-year salary)
> Why it matches you: You recruit mobile and iOS engineers for startups up to Series B, and this is a native Swift role at a Series B AI company.
>
> Would you like me to find candidates for this role, or would you like to submit your own?
> `[Find candidates]` `[Submit my own]`

The Listen Labs Technical GTM Lead and the AfterQuery Strategic Projects Lead are not sent to Saf, because they are outside her technical specialty.

**2. Role question (DM)**

> *Saf:* Can the Wispr role hire someone outside the US? Do they sponsor visas?
>
> *Role Brief Copilot:* Yes. It's fully remote worldwide as long as the person overlaps at least 4 hours a day with Pacific Time, and no visa is needed: international hires join as contractors or through an employer-of-record. (Source: Wispr AI brief)

**3. Pay and reward question (@mention in a channel, answered in the thread)**

> *Saf:* @Role Brief Copilot what's the reward on the Listen Labs GTM role, and are bonuses on?
>
> *Role Brief Copilot:* The Contrario reward is $22,500 – $27,000+ (15% of first-year salary), and bonuses are enabled for this role. Pay is $150,000 – $300,000 OTE, about 60/40 base to variable, in person in San Francisco 5 days a week. (Source: Listen Labs brief)

**4. Confidential question**

> *Saf:* Could Wispr go above the posted salary range for a really strong candidate?
>
> *Role Brief Copilot:* That's not something I can share.

**5. Not in the brief: the bot doesn't know and flags the founder**

> *Saf:* Does AfterQuery offer a relocation package for someone moving to SF?
>
> *Role Brief Copilot:* I don't have that info yet. I've flagged it for the founder.

With `ENABLE_FOUNDER_ESCALATION=true`, the bot posts this in the founder channel:

> *Role Brief Copilot:* @Founder A recruiter asked something the role briefs don't answer.
> *Role:* AfterQuery - Strategic Projects Lead
> *Question:* Does AfterQuery offer a relocation package for someone moving to SF?
> *Asked by:* @Saf
> Reply in this thread and I'll add your answer to the knowledge base and send it to the recruiter.

When the founder replies in the thread, Saf gets the answer in her original conversation, tagged "(Source: Founder answer)". The bot can use that answer for later questions. In the live prototype escalation was off, so the bot still said the line but no one was tagged.
