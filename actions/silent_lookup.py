"""Background web lookup: finds an answer without showing any window or opening a browser.

Used when the user asks for information (weather, facts, news) and does not ask to see the
workspace. Only https pages are read, and the result is plain text for ARNOX to speak or summarize.
"""

from __future__ import annotations

import re

import requests

from core.workspace_router import normalize_web_target

_TIMEOUT = 12
_MAX_CHARS = 1500
_UA = "Mozilla/5.0 (ARNOX background lookup)"


def _ddg(query: str, limit: int = 5) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=limit) or [])


def _page_text(url: str) -> str:
    url, error = normalize_web_target(url)
    if not url:
        return ""
    try:
        response = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": _UA})
        response.raise_for_status()
    except requests.RequestException:
        return ""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    return text[:_MAX_CHARS]


def silent_web_lookup(query: str) -> str:
    """Return a short answer built from search results, without showing anything."""
    query = str(query or "").strip()
    if not query:
        return "Tell me what to look up."
    try:
        results = _ddg(query)
    except Exception as exc:
        return f"The background search could not run: {exc}"
    if not results:
        return f"I found nothing for '{query}'."
    lines = []
    for item in results[:4]:
        title = str(item.get("title", "")).strip()
        body = str(item.get("body", "")).strip()
        if title or body:
            lines.append(f"- {title}: {body}")
    top_url = str(results[0].get("href", "") or results[0].get("url", "") or "")
    detail = _page_text(top_url) if top_url else ""
    answer = "\n".join(lines)
    if detail:
        answer += f"\n\nFrom {top_url}:\n{detail}"
    return answer.strip()
