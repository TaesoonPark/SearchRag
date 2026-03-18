from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Iterable, Optional

import httpx
from dateutil import parser as date_parser

from fetch import USER_AGENT, fetch_and_extract
from models import Document, SearchResult
from normalize import dedupe_preserve


def parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return date_parser.parse(str(value))
    except Exception:
        return None


def _searxng_result_to_search_result(item: dict, source: str) -> SearchResult:
    title = item.get("title") or ""
    url = item.get("url") or ""
    snippet = item.get("content") or item.get("snippet") or ""
    engines = item.get("engines") or []
    engine = engines[0] if engines else "searxng"

    published = (
        parse_datetime(item.get("publishedDate"))
        or parse_datetime(item.get("published_date"))
        or parse_datetime(item.get("published"))
        or parse_datetime(item.get("pubdate"))
    )

    return SearchResult(
        title=title,
        url=url,
        snippet=snippet,
        source=source,
        engine=engine,
        published=published,
    )


async def searxng_search(
    client: httpx.AsyncClient,
    base_url: str,
    query: str,
    engines: Iterable[str],
    timeout: int,
) -> list[SearchResult]:
    params = {
        "q": query,
        "format": "json",
    }
    if engines:
        params["engines"] = ",".join(engines)

    try:
        resp = await client.get(f"{base_url.rstrip('/')}/search", params=params, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return []

    results = payload.get("results", []) if isinstance(payload, dict) else []
    parsed: list[SearchResult] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        parsed.append(_searxng_result_to_search_result(item, source="searxng"))
    return parsed


async def collect_documents(
    results: list[SearchResult],
    timeout: int,
    max_chars: int,
    max_docs: int,
    max_concurrency: int,
) -> list[Document]:
    urls = dedupe_preserve([r.url for r in results if r.url])
    urls = urls[:max_docs]

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _fetch(url: str) -> Optional[Document]:
        async with semaphore:
            async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
                data = await fetch_and_extract(client, url, timeout, max_chars)
                if not data:
                    return None
                result = next((r for r in results if r.url == url), None)
                return Document(
                    title=data.get("title") or (result.title if result else ""),
                    url=url,
                    text=data.get("text") or "",
                    source=(result.source if result else "unknown"),
                    published=(result.published if result else None),
                )

    tasks = [_fetch(url) for url in urls]
    docs_raw = await asyncio.gather(*tasks)
    return [doc for doc in docs_raw if doc]
