from __future__ import annotations

import httpx

from fetch import USER_AGENT
from models import SearchResult
from search import parse_datetime


def _brave_result_to_search_result(item: dict) -> SearchResult:
    title = item.get("title") or ""
    url = item.get("url") or ""
    snippet = item.get("description") or ""
    published = parse_datetime(item.get("page_age")) or parse_datetime(item.get("age"))

    return SearchResult(
        title=title,
        url=url,
        snippet=snippet,
        source="brave",
        engine="brave",
        published=published,
    )


async def brave_search(
    client: httpx.AsyncClient,
    query: str,
    api_key: str,
    country: str,
    lang: str,
    timeout: int,
) -> list[SearchResult]:
    if not api_key:
        return []

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
        "User-Agent": USER_AGENT,
    }
    params = {
        "q": query,
        "country": country,
        "search_lang": lang,
        "count": 10,
    }

    try:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers=headers,
            params=params,
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return []

    web = payload.get("web", {}) if isinstance(payload, dict) else {}
    results = web.get("results", []) if isinstance(web, dict) else []
    parsed: list[SearchResult] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        parsed.append(_brave_result_to_search_result(item))
    return parsed
