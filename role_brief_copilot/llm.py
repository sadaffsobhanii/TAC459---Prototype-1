"""Thin wrapper around the Grok API (xAI). xAI's API is OpenAI-compatible,
so we use the official `openai` client pointed at xAI's base URL."""
import json
import os

from openai import OpenAI

_client = None


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=os.environ["XAI_API_KEY"],
            base_url=os.getenv("XAI_BASE_URL", "https://api.x.ai/v1"),
        )
    return _client


def model() -> str:
    return os.getenv("GROK_MODEL", "grok-4")


def chat(system: str, user: str, temperature: float = 0.2) -> str:
    resp = client().chat.completions.create(
        model=model(),
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content.strip()


def chat_json(system: str, user: str) -> dict:
    """Ask for a JSON object and parse it (tolerates ```json fences)."""
    raw = chat(system + "\nRespond with a single JSON object only.", user, temperature=0)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):]
    return json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
