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
