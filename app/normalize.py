from __future__ import annotations

import re
from typing import Iterable


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def dedupe_preserve(items: Iterable[str]) -> list[str]:
    seen = set()
    out: list[str] = []
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out
