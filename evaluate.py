"""Offline test for the Phase 3 metrics — no Slack needed.

1. Loads the sample brief into the vector DB.
2. Asks every question in sample_data/test_questions.csv.
3. Reports accuracy, safety ("I don't know" handled correctly), and speed.

Run:  python evaluate.py
"""
import csv
import time

from dotenv import load_dotenv

load_dotenv()

from role_brief_copilot import brief, store  # noqa: E402
from role_brief_copilot.answer import answer_question  # noqa: E402

TITLE = "Founding Account Executive"

with open("sample_data/sample_jd.txt") as f:
    jd = f.read()
with open("sample_data/sample_intake_notes.txt") as f:
    notes = f.read()

print("Building brief...")
store.add_brief(TITLE, brief.build_brief(TITLE, jd, notes))

rows, results = list(csv.DictReader(open("sample_data/test_questions.csv"))), []
for r in rows:
    t = time.time()
    out = answer_question(r["question"])
    latency = time.time() - t
    should_know = r["in_brief"].strip().lower() == "yes"
    correct = out["known"] == should_know
    results.append((r, out, latency, correct))
    flag = "OK " if correct else "MISS"
    print(f"[{flag}] {r['question']}\n      -> known={out['known']} | {out['answer'][:120]}\n")

known_qs = [x for x in results if x[0]["in_brief"].lower() == "yes"]
unknown_qs = [x for x in results if x[0]["in_brief"].lower() != "yes"]
print("=" * 60)
print(f"Answered when it should   : {sum(x[1]['known'] for x in known_qs)}/{len(known_qs)}")
print(f"Said 'I don't know' safely: {sum(not x[1]['known'] for x in unknown_qs)}/{len(unknown_qs)}")
print(f"Overall routing accuracy  : {sum(x[3] for x in results)}/{len(results)}")
print(f"Avg time to answer        : {sum(x[2] for x in results)/len(results):.1f}s")
print("Tip: also check each answer's text by hand against the expected_answer column.")
