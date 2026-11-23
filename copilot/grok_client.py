"""Thin wrapper around the xAI Grok API (OpenAI-compatible, https://api.x.ai/v1)."""

from __future__ import annotations

import logging

from openai import BadRequestError, OpenAI

from .scoring import parse_json_block

log = logging.getLogger(__name__)


class GrokClient:
    def __init__(self, api_key: str, base_url: str = "https://api.x.ai/v1", model: str = "grok-4.7",
                 timeout: float = 180.0):
        if not api_key:
            raise ValueError("XAI_API_KEY is not set")
        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def chat(self, system: str, user: str, history: list[dict] | None = None,
             temperature: float | None = 0.2) -> str:
        messages = [{"role": "system", "content": system}]
        messages += history or []
        messages.append({"role": "user", "content": user})
        kwargs = {"model": self.model, "messages": messages}
        if temperature is not None:
            kwargs["temperature"] = temperature
        try:
            response = self._client.chat.completions.create(**kwargs)
        except BadRequestError:
            if "temperature" not in kwargs:
                raise
            # Some models reject sampling parameters; retry with defaults.
            kwargs.pop("temperature")
            response = self._client.chat.completions.create(**kwargs)
        return (response.choices[0].message.content or "").strip()

    def chat_json(self, system: str, user: str) -> dict:
        """Ask for JSON and parse it, retrying once with a stricter reminder."""
        reply = self.chat(system, user, temperature=0.1)
        try:
            return parse_json_block(reply)
        except (ValueError, TypeError):
            log.warning("Model reply was not JSON, retrying once")
            reply = self.chat(system, user + "\n\nReturn ONLY valid JSON. No prose.", temperature=0.0)
            return parse_json_block(reply)
