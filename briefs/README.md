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
