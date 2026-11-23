"""Slack app: the full Role Brief Copilot flow.

  /rolebrief            -> founder opens a form, pastes JD + intake notes (Steps 1-3)
  @Role Brief Copilot   -> recruiter asks a question in a channel or DM (Step 4)
  unknown answer        -> question is forwarded to the founder channel;
                           founder replies in the thread -> answer is sent back
                           to the recruiter AND saved into the brief (Step 5)
"""
import json
import logging
import os
import re
import time

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

from . import brief, metrics, store  # noqa: E402
from .answer import answer_question  # noqa: E402

logging.basicConfig(level=logging.INFO)
app = App(token=os.environ["SLACK_BOT_TOKEN"])
FOUNDER_CHANNEL = os.environ["FOUNDER_CHANNEL_ID"]
PENDING_PATH = os.getenv("PENDING_PATH", "./data/pending.json")


# ---------- pending escalations (founder thread ts -> recruiter thread) ----------
def _load_pending() -> dict:
    try:
        with open(PENDING_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_pending(p: dict) -> None:
    os.makedirs(os.path.dirname(PENDING_PATH) or ".", exist_ok=True)
    with open(PENDING_PATH, "w") as f:
        json.dump(p, f, indent=2)


# ---------- Steps 1-3: founder creates a brief ----------
@app.command("/rolebrief")
def open_brief_form(ack, body, client):
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "brief_submit",
            "title": {"type": "plain_text", "text": "New Role Brief"},
            "submit": {"type": "plain_text", "text": "Create brief"},
            "blocks": [
                {"type": "input", "block_id": "title",
                 "label": {"type": "plain_text", "text": "Role title"},
                 "element": {"type": "plain_text_input", "action_id": "v"}},
                {"type": "input", "block_id": "jd",
                 "label": {"type": "plain_text", "text": "Job description"},
                 "element": {"type": "plain_text_input", "action_id": "v", "multiline": True}},
                {"type": "input", "block_id": "notes", "optional": True,
                 "label": {"type": "plain_text", "text": "Intake notes (comp, dealbreakers, process...)"},
                 "element": {"type": "plain_text_input", "action_id": "v", "multiline": True}},
            ],
        },
    )


@app.view("brief_submit")
def create_brief(ack, body, client):
    ack()
    vals = body["view"]["state"]["values"]
    title = vals["title"]["v"]["value"].strip()
    jd = vals["jd"]["v"]["value"]
    notes = vals["notes"]["v"].get("value") or ""
    user = body["user"]["id"]

    markdown = brief.build_brief(title, jd, notes)
    role_id = store.add_brief(title, markdown)

    # Show the founder the brief (minus the confidential section) so they can check it.
    public = markdown.split("## Confidential")[0].strip()
    client.chat_postMessage(
        channel=user,
        text=f":white_check_mark: Brief for *{title}* is live (id `{role_id}`). "
             f"Recruiters can now ask about it.\n\n{public[:2800]}",
    )


# ---------- Step 4: recruiter asks a question ----------
def handle_question(question: str, channel: str, thread_ts: str, say, client):
    start = time.time()
    result = answer_question(question)
    latency = time.time() - start

    if result["known"]:
        src = ", ".join(result["sources"])
        say(text=f"{result['answer']}\n_Source: {result['title']} brief ({src})_", thread_ts=thread_ts)
        metrics.log(question, result["role_id"], True, latency, escalated=False)
        return

    # Step 5: unknown -> say so honestly and forward to the founder.
    say(text="I don't have that in the role brief yet, so I won't guess. "
             "I've asked the founder and will reply here as soon as they answer. :hourglass_flowing_sand:",
        thread_ts=thread_ts)
    role = result["title"] or "an open role"
    msg = client.chat_postMessage(
        channel=FOUNDER_CHANNEL,
        text=f":question: A recruiter asked about *{role}*:\n> {question}\n"
             f"Reply *in this thread* and I'll send your answer back and add it to the brief.",
    )
    pending = _load_pending()
    pending[msg["ts"]] = {
        "channel": channel, "thread_ts": thread_ts, "question": question,
        "role_id": result["role_id"], "title": result["title"],
    }
    _save_pending(pending)
    metrics.log(question, result["role_id"], False, latency, escalated=True)


@app.event("app_mention")
def on_mention(event, say, client):
    question = re.sub(r"<@[^>]+>", "", event["text"]).strip()
    if question:
        handle_question(question, event["channel"], event.get("thread_ts") or event["ts"], say, client)


@app.event("message")
def on_message(event, say, client):
    if event.get("bot_id") or event.get("subtype"):
        return

    # Founder answering an escalated question (thread reply in the founder channel).
    if event["channel"] == FOUNDER_CHANNEL and event.get("thread_ts"):
        pending = _load_pending()
        item = pending.pop(event["thread_ts"], None)
        if not item:
            return
        answer = event["text"].strip()
        if item["role_id"]:
            store.add_founder_answer(item["role_id"], item["title"], item["question"], answer)
        client.chat_postMessage(
            channel=item["channel"], thread_ts=item["thread_ts"],
            text=f":speech_balloon: The founder says: {answer}",
        )
        client.reactions_add(channel=event["channel"], timestamp=event["ts"], name="white_check_mark")
        _save_pending(pending)
        return

    # Direct messages to the bot count as questions too.
    if event.get("channel_type") == "im":
        handle_question(event["text"], event["channel"], event["ts"], say, client)


def main():
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    main()
