"""Step 3: store brief chunks as embeddings in a local Chroma vector DB.
Chroma's built-in embedding model (all-MiniLM-L6-v2) runs locally, so no
extra API key is needed for embeddings."""
import os
import re
import uuid

import chromadb

from .brief import chunk_brief

_collection = None


def collection():
    global _collection
    if _collection is None:
        path = os.getenv("CHROMA_PATH", "./data/chroma")
        db = chromadb.PersistentClient(path=path)
        _collection = db.get_or_create_collection("role_briefs")
    return _collection


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def add_brief(title: str, markdown: str) -> str:
    """Store (or replace) a role brief. Returns the role_id."""
    role_id = slugify(title)
    col = collection()
    col.delete(where={"role_id": role_id})
    chunks = chunk_brief(markdown)
    col.add(
        ids=[f"{role_id}-{i}" for i in range(len(chunks))],
        documents=[c["text"] for c in chunks],
        metadatas=[{
            "role_id": role_id,
            "title": title,
            "section": c["section"],
            "confidential": c["confidential"],
            "source": "brief",
        } for c in chunks],
    )
    return role_id


def add_founder_answer(role_id: str, title: str, question: str, answer: str) -> None:
    """Step 5: the founder's answer becomes part of the brief."""
    collection().add(
        ids=[f"{role_id}-qa-{uuid.uuid4().hex[:8]}"],
        documents=[f"Founder Q&A:\nQ: {question}\nA: {answer}"],
        metadatas=[{
            "role_id": role_id,
            "title": title,
            "section": "Founder Q&A",
            "confidential": False,
            "source": "founder",
        }],
    )


def list_roles() -> dict[str, str]:
    """role_id -> title for every stored brief."""
    got = collection().get(include=["metadatas"])
    return {m["role_id"]: m["title"] for m in got["metadatas"]}


def search(question: str, role_id: str | None = None, k: int = 4) -> list[dict]:
    """Step 4 (retrieval): top-k non-confidential chunks for a question."""
    where = {"confidential": False}
    if role_id:
        where = {"$and": [{"confidential": False}, {"role_id": role_id}]}
    col = collection()
    if col.count() == 0:
        return []
    res = col.query(query_texts=[question], n_results=k, where=where)
    return [
        {"text": doc, **meta, "distance": dist}
        for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]
