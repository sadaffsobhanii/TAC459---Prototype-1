"""Public-profile candidate search through a web search API.

Uses SERPER_API_KEY (google.serper.dev) or TAVILY_API_KEY (api.tavily.com).
Only public LinkedIn profile URLs (linkedin.com/in/...) and GitHub profiles are
kept. The bot never logs into LinkedIn or scrapes it; it only uses the title
and snippet the search API returns. If fewer than 5 real people fit, the bot
returns fewer than 5. It never invents a person or a link.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

LINKEDIN_RE = re.compile(r"^https?://([a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+/?$")
GITHUB_RE = re.compile(r"^https?://github\.com/[A-Za-z0-9\-]+/?$")
GITHUB_RESERVED = {"topics", "orgs", "features", "about", "pricing", "search", "collections", "sponsors"}


@dataclass
class SearchHit:
    title: str
    link: str
    snippet: str


def is_public_profile(link: str) -> bool:
    link = link.split("?")[0]
    if LINKEDIN_RE.match(link):
        return True
    if GITHUB_RE.match(link):
        return link.rstrip("/").rsplit("/", 1)[-1].lower() not in GITHUB_RESERVED
    return False


def filter_public_profiles(hits: list[SearchHit], do_not_source: list[str]) -> list[SearchHit]:
    """Keep unique public profiles; drop anyone whose result names a do-not-source company."""
    blocked = [c.lower() for c in do_not_source if c and len(c) > 2]
    seen: set[str] = set()
    kept: list[SearchHit] = []
    for hit in hits:
        link = hit.link.split("?")[0].rstrip("/")
        if not is_public_profile(link) or link in seen:
            continue
        text = f"{hit.title} {hit.snippet}".lower()
        if any(company in text for company in blocked):
            continue
        seen.add(link)
        kept.append(SearchHit(title=hit.title, link=link, snippet=hit.snippet))
    return kept


class CandidateSearch:
    def __init__(self, serper_api_key: str = "", tavily_api_key: str = "", timeout: int = 20):
        self.serper_api_key = serper_api_key
        self.tavily_api_key = tavily_api_key
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.serper_api_key or self.tavily_api_key)

    def search(self, query: str, num: int = 10) -> list[SearchHit]:
        try:
            if self.serper_api_key:
                return self._serper(query, num)
            if self.tavily_api_key:
                return self._tavily(query, num)
        except requests.RequestException:
            log.exception("Search failed for %r", query)
        return []

    def _serper(self, query: str, num: int) -> list[SearchHit]:
        response = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": self.serper_api_key, "Content-Type": "application/json"},
            json={"q": query, "num": num},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return [
            SearchHit(item.get("title", ""), item.get("link", ""), item.get("snippet", ""))
            for item in response.json().get("organic", [])
        ]

    def _tavily(self, query: str, num: int) -> list[SearchHit]:
        response = requests.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {self.tavily_api_key}"},
            json={
                "query": query,
                "max_results": num,
                "include_domains": ["linkedin.com", "github.com"],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return [
            SearchHit(item.get("title", ""), item.get("url", ""), item.get("content", ""))
            for item in response.json().get("results", [])
        ]

    def lookup_profile(self, url: str) -> str:
        """Public search snippet for a LinkedIn/GitHub URL a recruiter pasted."""
        hits = self.search(f'"{url.rstrip("/")}"', num=5)
        clean = url.split("?")[0].rstrip("/").lower()
        for hit in hits:
            if hit.link.split("?")[0].rstrip("/").lower() == clean:
                return f"{hit.title}\n{hit.snippet}"
        return ""
