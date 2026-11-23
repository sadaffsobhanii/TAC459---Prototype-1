# Role Brief Copilot: TAC 459 Capstone, Prototype 1

**A Slack bot that helps Contrario recruiters close more placements.** It sends a daily DM of new roles that match each recruiter's profile, answers role questions straight from the founder's brief, finds real candidates from public profiles, and scores a recruiter's own candidate before they submit.

Built for [Contrario](https://contrario.ai) (YC W25) as part of USC's TAC 459 Generative AI capstone. Powered by the **Grok API**, **Slack Bolt** (Socket Mode) and Python.

> Student prototype. Not affiliated with or endorsed by Contrario.

🎥 **Demo video:** [add link]
📄 **Full documentation:** [ROLE_BRIEF_COPILOT.md](ROLE_BRIEF_COPILOT.md), which covers the flow, every prompt, Slack setup, environment variables and example conversations

---

## What it does

| # | Feature | How |
|---|---|---|
| 1 | **Daily role alerts** | Weekdays at 9 AM PT, each recruiter gets a DM with the new roles that match their profile, with **Find candidates** and **Submit my own** buttons |
| 2 | **Answers role questions** | Answers in 1–3 sentences, only from the founder's brief, and names its source. Never guesses. |
| 3 | **Keeps secrets** | INTERNAL brief sections are removed before anything reaches the model, and an output guard blocks any reply that repeats an internal number or phrase |
| 4 | **Escalates to the founder** | If the brief doesn't cover a question, it says so and flags the founder. The founder's reply is added to the knowledge base. |
| 5 | **Finds candidates** | Searches public LinkedIn and GitHub profiles, then ranks up to 5 against the role scorecard. It never invents a person or a link. |
| 6 | **Scores your own candidate** | Checks dealbreakers, then scores a resume or LinkedIn profile out of 100: **Good fit (75+) / Possible fit (55–74) / Not a fit** |
| 7 | **Submits candidates** | At 55+, it asks to submit and records the candidate in the pipeline. The recruiter makes the final call. |

## Quick start

```bash
git clone https://github.com/sadaffsobhanii/TAC459---Prototype-1.git
cd TAC459---Prototype-1
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add your Slack and Grok keys
python -m pytest -q         # 26 tests
python app.py               # start the bot
```

Create the Slack app from `manifest.yml` (see [Slack App Setup](ROLE_BRIEF_COPILOT.md#slack-app-setup)). Role brief PDFs go in `briefs/` and aren't committed, because they contain INTERNAL notes.

## Repo structure

```
app.py              Slack app: events, buttons, slash commands
copilot/            Core logic (knowledge, matching, scoring, search, pipeline, prompts)
recruiters/         Recruiter profiles used for role matching
briefs/             Role briefs (local only)
data/               Pipeline record
tests/              26 unit tests: INTERNAL stripping, output guard, scoring, matching
manifest.yml        One-paste Slack app setup
```

## Professor feedback and next steps

- [ ] Candidate repository, with history and search for past candidates and roles
- [ ] Team features for several recruiters and founders
- [ ] Scale test with 50–100 synthetic job postings
- [ ] A web version of the agent for Contrario's website
