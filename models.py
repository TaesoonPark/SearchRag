from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str
    engine: str
    published: Optional[datetime] = None


@dataclass
class Document:
    title: str
    url: str
    text: str
    source: str
    published: Optional[datetime] = None
