"""Logs every question so you can report the roadmap metrics:
speed (latency), safety ("I don't know" rate), usefulness (founder pings saved)."""
import csv
import os
from datetime import datetime, timezone

LOG_PATH = os.getenv("METRICS_LOG", "./data/question_log.csv")
FIELDS = ["timestamp", "question", "role_id", "known", "latency_sec", "escalated"]


def log(question: str, role_id, known: bool, latency: float, escalated: bool) -> None:
    os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
    new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "question": question,
            "role_id": role_id or "",
            "known": known,
            "latency_sec": round(latency, 2),
            "escalated": escalated,
        })
