from __future__ import annotations

import logging
from typing import Optional

import httpx
import re
from bs4 import BeautifulSoup

from normalize import collapse_whitespace, trim_text

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
MAIN_CONTENT_SELECTORS = (
    "article",
    "main",
    "div#articleBodyContents",
    "div#newsct_article",
    "div.article_body",
    "div#articledetail",
    "section.newsct_article",
    "div#newsEndContents",
)
NOISE_PATTERNS = (
    re.compile(r"\b본문 바로가기\b", re.IGNORECASE),
    re.compile(r"\b기사원문\b", re.IGNORECASE),
    re.compile(r"\b주요서비스 바로가기\b.*?\b전체서비스 바로가기\b", re.IGNORECASE),
    re.compile(r"\b입력\s*\d{4}\.\d{1,2}\.\d{1,2}\.\s*오[전후]\s*\d{1,2}:\d{2}", re.IGNORECASE),
    re.compile(r"\b수정\s*\d{4}\.\d{1,2}\.\d{1,2}\.\s*오[전후]\s*\d{1,2}:\d{2}", re.IGNORECASE),
    re.compile(r"\b공감 좋아요\s*\d+\s*응원해요\s*\d+\s*축하해요\s*\d+\s*기대해요\s*\d+\s*놀랐어요\s*\d+\s*슬퍼요\s*\d+", re.IGNORECASE),
    re.compile(r"텍스트 음성 변환 서비스 본문 듣기를 종료 하였습니다\.?", re.IGNORECASE),
    re.compile(r"로그인.*?회원가입", re.IGNORECASE),
)


def _extract_main_text(soup: BeautifulSoup) -> str:
    for selector in MAIN_CONTENT_SELECTORS:
        node = soup.select_one(selector)
        if not node:
            continue
        candidate = collapse_whitespace(node.get_text(" ", strip=True))
        if len(candidate) >= 120:
            return candidate
    return ""


def _clean_article_text(text: str, title: str) -> str:
    if not text:
        return ""

    cleaned = text
    for pattern in NOISE_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)

    if title:
        normalized_title = collapse_whitespace(title)
        if cleaned.startswith(normalized_title):
            cleaned = cleaned[len(normalized_title) :].lstrip(" -–|:")

    cleaned = collapse_whitespace(cleaned)
    return cleaned


async def fetch_html(client: httpx.AsyncClient, url: str, timeout: int) -> Optional[str]:
    try:
        resp = await client.get(url, timeout=timeout)
        resp.raise_for_status()
        if "text/html" not in resp.headers.get("content-type", ""):
            return None
        return resp.text
    except Exception as exc:
        logger.debug("문서 fetch 실패: url=%s error=%s", url, exc)
        return None


def extract_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    for tag in soup(["script", "style", "noscript", "header", "footer", "svg", "form", "aside", "nav", "iframe", "button"]):
        tag.decompose()

    text = _extract_main_text(soup) or soup.get_text(separator=" ")
    text = _clean_article_text(collapse_whitespace(text), title)
    return title, text


async def fetch_and_extract(
    client: httpx.AsyncClient, url: str, timeout: int, max_chars: int
) -> Optional[dict]:
    html = await fetch_html(client, url, timeout)
    if not html:
        return None
    title, text = extract_text(html)
    if not text:
        return None
    text = trim_text(text, max_chars)
    return {
        "title": title,
        "text": text,
        "url": url,
    }
