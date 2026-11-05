"""Lightweight web research (no API key): gathers real-time-ish snippets for an idea
from Wikipedia (extractive summaries) and DuckDuckGo HTML search. Returns short,
source-labelled text blocks that the LLM then turns into a detailed writeup.

Network failures never break the flow — callers fall back to generating from the
LLM's own knowledge.
"""

from __future__ import annotations

import html
import re

import httpx

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ReelForge/1.0"}


def _wiki_titles(query: str, limit: int = 3) -> list[str]:
    try:
        r = httpx.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "opensearch", "search": query, "limit": limit,
                "namespace": 0, "format": "json",
            },
            headers=UA, timeout=12,
        )
        r.raise_for_status()
        data = r.json()
        return [t for t in (data[1] if len(data) > 1 else [])]
    except Exception:
        return []


def _wiki_intro(title: str) -> str | None:
    try:
        r = httpx.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query", "prop": "extracts", "exintro": True,
                "explaintext": True, "redirects": True, "format": "json",
                "titles": title,
            },
            headers=UA, timeout=12,
        )
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        for pg in pages.values():
            return (pg.get("extract") or "").strip()[:1400] or None
        return None
    except Exception:
        return None


def _ddg(query: str, limit: int = 6) -> list[str]:
    """DuckDuckGo HTML results: `(title, snippet)` pairs. Fragile but keyless."""
    out: list[str] = []
    try:
        r = httpx.get("https://html.duckduckgo.com/html/", params={"q": query},
                      headers=UA, timeout=12)
        r.raise_for_status()
        text = r.text
        titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', text, re.S)
        snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', text, re.S)
        for t, s in zip(titles, snips):
            t = html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
            s = html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
            if s:
                out.append(f"{t}: {s}" if t else s)
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


def research(idea: str, max_blocks: int = 8) -> list[tuple[str, str]]:
    """Return [(source_label, text)] for the idea, mixing Wikipedia + web snippets."""
    blocks: list[tuple[str, str]] = []

    for title in _wiki_titles(idea, 3):
        intro = _wiki_intro(title)
        if intro:
            blocks.append((f"Wikipedia — {title}", intro))
            if len(blocks) >= max_blocks:
                break

    for snip in _ddg(idea, 6):
        blocks.append(("Web search", snip))
        if len(blocks) >= max_blocks:
            break

    return blocks