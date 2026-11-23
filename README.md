# Role Brief Copilot: TAC 459 Capstone, Prototype 1

**A Slack bot that answers recruiters' questions about a role right away, using the founder's role brief, so busy founders stop getting the same Slack pings.**

Built for [Contrario](https://contrario.ai) (YC W25) as part of USC's TAC 459 Generative AI & NLP capstone, Team 6.

> Student prototype. Sample data is fictional. Not affiliated with or endorsed by Contrario.

🎥 **Demo video:** [add link]

---

## The problem

Recruiters on Contrario keep pinging startup founders with the same questions: *What's the comp? Is it remote? Can you sponsor visas? What's the interview process?* Founders are busy, so recruiters wait hours or days, and good candidates go cold.

## The solution

The founder describes the role **once**. After that, recruiters ask the bot in Slack, and it answers right away, **only from the founder's brief**. If the brief doesn't cover a question, the bot says it doesn't know, asks the founder, and adds the founder's answer to the brief. The brief keeps improving, and founders get fewer pings.

---

## How it works

```
1. Founder runs /rolebrief  ──►  pastes the job description + intake notes
2. Grok rewrites them as a clean brief: location, comp, must-haves, dealbreakers, interview process
                                     (private notes go in a Confidential section)
3. The brief is split into chunks ──► embedded ──► stored in Chroma (vector DB)
4. Recruiter @mentions or DMs the bot ──► RAG retrieves the relevant chunks ──► Grok answers ONLY from them
5. Not in the brief? ──► "I don't know yet" ──► question goes to #founder channel
                     ──► founder replies in the thread ──► answer goes back to the recruiter AND is added to the brief
```

### Guardrails
- **Doesn't make things up:** if the brief doesn't cover something (comp, visas, equity), the bot says it doesn't know instead of guessing.
- **Keeps secrets:** chunks in the Confidential section are never retrieved for recruiters.
- **Human in the loop:** the founder answers anything the bot can't.

---

## Tech stack

| Part | Tool |
|---|---|
| Chat interface | Slack (Bolt for Python, Socket Mode) |
| LLM | **Grok API (xAI)**, which is OpenAI-compatible |
| Embeddings + vector DB | **Chroma** with its built-in local embedding model |
| Language | Python 3.10+ |

**AI concepts used:** embeddings, vector databases, retrieval-augmented generation (RAG), decoder LLMs, prompt engineering.

---

## Repo structure

```
├── role_brief_copilot/
│   ├── bot.py        # Slack app: /rolebrief form, Q&A, founder escalation
│   ├── brief.py      # Grok turns the JD + notes into a brief; split into chunks
│   ├── store.py      # Chroma vector DB: add briefs, founder answers, search
│   ├── answer.py     # RAG: retrieve chunks, then answer only from the brief
│   ├── llm.py        # Grok API wrapper
│   └── metrics.py    # Logs every question for the success metrics
├── evaluate.py       # Offline test: accuracy, safety, speed
├── sample_data/      # Sample job description, intake notes, test questions
├── slack_manifest.yml
├── requirements.txt
└── .env.example
```

---

## Setup

**1. Install**
```bash
git clone https://github.com/sadaffsobhanii/TAC459---Prototype-1.git
cd TAC459---Prototype-1
pip install -r requirements.txt
cp .env.example .env
```

**2. Create the Slack app**
- Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest**, and paste in `slack_manifest.yml`
- Install it to your workspace
- Copy the **Bot Token** (`xoxb-…`) and an **App-Level Token** with `connections:write` (`xapp-…`) into `.env`
- Create a `#founder` channel, invite the bot, and put that channel's ID in `FOUNDER_CHANNEL_ID`

**3. Add your Grok key**
Get an API key at [console.x.ai](https://console.x.ai) and put it in `XAI_API_KEY`.

**4. Run**
```bash
python -m role_brief_copilot.bot
```

**5. Try it**
- Type `/rolebrief` and paste in `sample_data/sample_jd.txt` and `sample_intake_notes.txt`
- In any channel, ask `@Role Brief Copilot is this role remote?`
- Ask something the brief doesn't cover (*"Is there a signing bonus?"*), then answer it in `#founder`

---

## Success metrics

Run the offline test (no Slack needed):
```bash
python evaluate.py
```

| Metric | What we measure |
|---|---|
| **Accuracy** | % of questions answered correctly from the brief |
| **Speed** | Time to answer vs. waiting on the founder |
| **Safety** | % of "I don't know" cases handled correctly; confidential info never shared |
| **Usefulness** | Founder pings saved |

The live bot also logs every question to `data/question_log.csv` (time to answer, answered or escalated).

---

## Professor feedback and next steps

Our professor said this is viable. Next steps:

- [ ] **Candidate repository:** store candidates and recommend the best fits for each role
- [ ] **History and search:** look up past roles and previously recommended candidates
- [ ] **Team features:** several founders and recruiters sharing one workspace
- [ ] **Scale test:** expand to 50–100 synthetic job postings to show it working at scale
- [ ] ATS sync (Ashby, Lever) so founders never retype a JD

---

## Team 6

Sadaf · Yajan · Arsh · Nicolo · Arman · Darsh

*USC TAC 459: Generative AI & NLP, Fall 2026*
