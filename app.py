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
