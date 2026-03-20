from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime
from typing import Any, Iterable, Optional

import httpx
from dateutil import parser as date_parser

from fetch import USER_AGENT, fetch_and_extract
from models import Document, SearchResult
from normalize import dedupe_preserve

logger = logging.getLogger(__name__)


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


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(value or "")).strip()


def _naver_result_to_search_result(item: dict) -> SearchResult:
    title = _strip_tags(item.get("title") or "")
    link = item.get("link") or ""
    snippet = _strip_tags(item.get("description") or "")
    published = (
        parse_datetime(item.get("postdate"))
        or parse_datetime(item.get("pubDate"))
        or parse_datetime(item.get("pubDateTime"))
    )

    return SearchResult(
        title=title,
        url=link,
        snippet=snippet,
        source="naver",
        engine="naver",
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
    except Exception as exc:
        logger.warning("SearXNG 검색 실패: query=%r error=%s", query, exc)
        return []

    results = payload.get("results", []) if isinstance(payload, dict) else []
    parsed: list[SearchResult] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        parsed.append(_searxng_result_to_search_result(item, source="searxng"))
    return parsed


async def naver_search(
    client: httpx.AsyncClient,
    query: str,
    client_id: str,
    client_secret: str,
    search_type: str,
    sort: str,
    display: int,
    start: int,
    timeout: int,
) -> list[SearchResult]:
    if not client_id or not client_secret:
        return []

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {
        "query": query,
        "display": max(1, min(display, 100)),
        "sort": sort,
        "start": max(1, start),
    }

    try:
        resp = await client.get(
            f"https://openapi.naver.com/v1/search/{search_type}.json",
            headers=headers,
            params=params,
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logger.warning("Naver 검색 실패: query=%r type=%s error=%s", query, search_type, exc)
        return []

    items = payload.get("items", []) if isinstance(payload, dict) else []
    parsed: list[SearchResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        parsed.append(_naver_result_to_search_result(item))
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

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        async def _fetch(url: str) -> Optional[Document]:
            async with semaphore:
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
