"""Web Search MCP Server.

Provides web search and page fetch tools to LangGraph agents via stdio MCP.
Uses DuckDuckGo Lite (no API key) by default.
When TAVILY_API_KEY is set in environment, Tavily is used instead.
"""

import asyncio
import logging
import os
import urllib.parse
from typing import Any

import httpx
from fastmcp import FastMCP
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    tavily_api_key: str | None = None
    search_max_results: int = 5
    fetch_max_chars: int = 8000
    request_timeout: float = 20.0


settings = Settings()

# ---------------------------------------------------------------------------
# Search backends
# ---------------------------------------------------------------------------


async def _search_duckduckgo(query: str, max_results: int) -> list[dict[str, str]]:
    """Search via DuckDuckGo Lite HTML endpoint (no API key required)."""
    url = "https://lite.duckduckgo.com/lite/"
    params = {"q": query}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; BrowserAgentBot/1.0; +https://github.com/spica)"
        )
    }
    results: list[dict[str, str]] = []

    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        response = await client.post(url, data=params, headers=headers)
        response.raise_for_status()
        html = response.text

    # Parse results from DuckDuckGo Lite HTML
    # Results follow pattern: <a class="result-link" href="...">title</a>
    import re

    link_pattern = re.compile(
        r'<a[^>]+class="result-link"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<td class="result-snippet">(.*?)</td>',
        re.DOTALL,
    )

    links = link_pattern.findall(html)
    snippets = [re.sub(r"<[^>]+>", "", s).strip() for s in snippet_pattern.findall(html)]

    for i, (href, title) in enumerate(links[:max_results]):
        title_clean = re.sub(r"<[^>]+>", "", title).strip()
        snippet = snippets[i] if i < len(snippets) else ""
        results.append(
            {
                "title": title_clean,
                "url": href,
                "snippet": snippet,
            }
        )

    return results


async def _search_tavily(
    query: str, max_results: int, api_key: str
) -> list[dict[str, str]]:
    """Search via Tavily API."""
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": False,
    }

    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()

    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
        }
        for item in data.get("results", [])
    ]


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "web-search",
    instructions=(
        "Provides web search and page fetch capabilities. "
        "Use web_search to find information on the web, "
        "then fetch_webpage to read the full content of a specific page."
    ),
)


@mcp.tool()
async def web_search(
    query: str,
    max_results: int = 5,
) -> list[dict[str, str]]:
    """Search the web and return a list of results.

    Args:
        query: Search query string.
        max_results: Maximum number of results to return (1-10).

    Returns:
        List of dicts with 'title', 'url', and 'snippet' keys.
    """
    max_results = min(max(1, max_results), 10)

    try:
        if settings.tavily_api_key:
            results = await _search_tavily(query, max_results, settings.tavily_api_key)
        else:
            results = await _search_duckduckgo(query, max_results)
    except httpx.HTTPError as exc:
        logger.error("Search failed: %s", exc)
        return [{"title": "Search error", "url": "", "snippet": str(exc)}]

    return results


@mcp.tool()
async def fetch_webpage(
    url: str,
    max_chars: int = 8000,
) -> dict[str, Any]:
    """Fetch the text content of a webpage.

    Strips HTML tags and returns plain text, truncated to max_chars.

    Args:
        url: Full URL of the page to fetch.
        max_chars: Maximum characters of content to return.

    Returns:
        Dict with 'url', 'title', and 'content' keys.
    """
    import re

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; BrowserAgentBot/1.0)"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        async with httpx.AsyncClient(
            timeout=settings.request_timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
    except httpx.HTTPError as exc:
        logger.error("Fetch failed for %s: %s", url, exc)
        return {"url": url, "title": "", "content": f"Fetch error: {exc}"}

    # Extract title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else ""

    # Remove script and style blocks
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)

    # Strip remaining HTML tags
    text = re.sub(r"<[^>]+>", " ", html)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    # Truncate
    if len(text) > max_chars:
        text = text[:max_chars] + "... [truncated]"

    return {"url": url, "title": title, "content": text}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # stdio transport — spawned as subprocess by Chat Agent
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
