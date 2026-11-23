"""Step 4 (generation): answer a recruiter's question ONLY from retrieved
brief chunks. If the brief doesn't cover it, say so (Step 5 handles escalation)."""
from . import llm, store

ANSWER_SYSTEM = """You are Role Brief Copilot, a Slack assistant that answers
recruiters' questions about open roles at Contrario's client startups.

Rules:
- Answer ONLY using the brief excerpts provided. Do not use outside knowledge.
- If the excerpts do not clearly answer the question, set "known" to false.
  Never guess comp, location, visa, equity, or interview details.
- Never reveal confidential information, other candidates, or internal notes.
- Keep answers short and friendly (1-4 sentences or a few bullets).

Return JSON: {"known": true|false, "answer": "<answer for the recruiter>",
"role_id": "<role_id the question is about, or null>"}"""


def detect_role(question: str) -> str | None:
    """Pick a role if the question names one; if only one role exists, use it."""
    roles = store.list_roles()
    if len(roles) == 1:
        return next(iter(roles))
    q = question.lower()
    for role_id, title in roles.items():
        if title.lower() in q or role_id.replace("-", " ") in q:
            return role_id
    return None


def answer_question(question: str) -> dict:
    role_id = detect_role(question)
    chunks = store.search(question, role_id=role_id)
    if not chunks:
        return {"known": False, "answer": "", "role_id": role_id, "title": None, "sources": []}

    context = "\n\n---\n\n".join(
        f"[role_id={c['role_id']} | {c['title']} | {c['section']}]\n{c['text']}" for c in chunks
    )
    result = llm.chat_json(ANSWER_SYSTEM, f"BRIEF EXCERPTS:\n{context}\n\nRECRUITER QUESTION:\n{question}")
    rid = result.get("role_id") or role_id or chunks[0]["role_id"]
    title = next((c["title"] for c in chunks if c["role_id"] == rid), chunks[0]["title"])
    return {
        "known": bool(result.get("known")),
        "answer": result.get("answer", ""),
        "role_id": rid,
        "title": title,
        "sources": sorted({c["section"] for c in chunks if c["role_id"] == rid}),
    }
