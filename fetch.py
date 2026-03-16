from __future__ import annotations

from typing import Optional

import httpx
from bs4 import BeautifulSoup

from normalize import collapse_whitespace, trim_text

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"


async def fetch_html(client: httpx.AsyncClient, url: str, timeout: int) -> Optional[str]:
    try:
        resp = await client.get(url, timeout=timeout)
        resp.raise_for_status()
        if "text/html" not in resp.headers.get("content-type", ""):
            return None
        return resp.text
    except Exception:
        return None


def extract_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    for tag in soup(["script", "style", "noscript", "header", "footer", "svg", "form"]):
        tag.decompose()

    text = soup.get_text(separator=" ")
    text = collapse_whitespace(text)
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
