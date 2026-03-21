from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.models import Document, SearchResult

PRICE_QUERY_KEYWORDS = (
    "주가",
    "현재가",
    "현재 가격",
    "실시간 시세",
    "시세",
    "stock price",
    "share price",
    "quote",
    "ticker",
    "market price",
)


@dataclass(frozen=True)
class QuoteRequest:
    ticker: str
    company: str
    query: str


@dataclass(frozen=True)
class QuoteSnapshot:
    ticker: str
    company: str
    market_price: float
    previous_close: Optional[float]
    currency: str
    exchange: str
    market_time: Optional[datetime]
    source_url: str

    @property
    def change(self) -> Optional[float]:
        if self.previous_close is None:
            return None
        return self.market_price - self.previous_close

    @property
    def change_percent(self) -> Optional[float]:
        if self.previous_close in (None, 0):
            return None
        return (self.market_price - self.previous_close) / self.previous_close * 100


def is_finance_quote_query(query: str) -> bool:
    lowered = query.casefold()
    return any(keyword in lowered for keyword in PRICE_QUERY_KEYWORDS)


def resolve_quote_request(query: str) -> Optional[QuoteRequest]:
    if not is_finance_quote_query(query):
        return None

    lowered = query.casefold()
    alias_map = (
        (("알파벳 c", "alphabet c", "alphabet class c", "google class c"), "GOOG", "Alphabet Inc. Class C"),
        (("알파벳 a", "alphabet a", "alphabet class a", "google class a"), "GOOGL", "Alphabet Inc. Class A"),
    )

    for patterns, ticker, company in alias_map:
        if any(pattern in lowered for pattern in patterns):
            return QuoteRequest(ticker=ticker, company=company, query=query)

    ticker_match = re.search(r"\b[A-Z]{1,5}\b", query)
    if ticker_match:
        ticker = ticker_match.group(0)
        return QuoteRequest(ticker=ticker, company=ticker, query=query)

    return None


def _parse_market_time(meta: dict) -> Optional[datetime]:
    timestamp = meta.get("regularMarketTime")
    if not timestamp:
        return None

    try:
        dt_utc = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    except Exception:
        return None

    offset_seconds = meta.get("gmtoffset")
    if offset_seconds is None:
        return dt_utc

    try:
        tzinfo = timezone(timedelta(seconds=int(offset_seconds)))
    except Exception:
        return dt_utc
    return dt_utc.astimezone(tzinfo)


async def fetch_quote_snapshot(
    client: httpx.AsyncClient,
    request: QuoteRequest,
    timeout: int,
) -> Optional[QuoteSnapshot]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{request.ticker}"

    try:
        resp = await client.get(url, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return None

    chart = payload.get("chart", {}) if isinstance(payload, dict) else {}
    result = chart.get("result", []) if isinstance(chart, dict) else []
    meta = result[0].get("meta", {}) if result and isinstance(result[0], dict) else {}
    if not isinstance(meta, dict):
        return None

    market_price = meta.get("regularMarketPrice")
    if market_price is None:
        return None

    company = meta.get("longName") or meta.get("shortName") or request.company
    exchange = meta.get("fullExchangeName") or meta.get("exchangeName") or ""
    currency = meta.get("currency") or ""

    return QuoteSnapshot(
        ticker=request.ticker,
        company=company,
        market_price=float(market_price),
        previous_close=float(meta["previousClose"]) if meta.get("previousClose") is not None else None,
        currency=currency,
        exchange=exchange,
        market_time=_parse_market_time(meta),
        source_url=url,
    )


def quote_snapshot_to_state(snapshot: QuoteSnapshot) -> dict:
    return {
        "ticker": snapshot.ticker,
        "company": snapshot.company,
        "market_price": snapshot.market_price,
        "previous_close": snapshot.previous_close,
        "change": snapshot.change,
        "change_percent": snapshot.change_percent,
        "currency": snapshot.currency,
        "exchange": snapshot.exchange,
        "market_time": snapshot.market_time.isoformat() if snapshot.market_time else "",
        "source_url": snapshot.source_url,
    }


def quote_snapshot_to_search_result(snapshot: QuoteSnapshot) -> SearchResult:
    snippet = f"{snapshot.company} ({snapshot.ticker}) {snapshot.market_price:.2f} {snapshot.currency}".strip()
    return SearchResult(
        title=f"{snapshot.company} ({snapshot.ticker}) current price",
        url=snapshot.source_url,
        snippet=snippet,
        source="finance_quote",
        engine="yahoo_chart",
        published=snapshot.market_time,
    )


def quote_snapshot_to_document(snapshot: QuoteSnapshot) -> Document:
    lines = [
        f"Company: {snapshot.company}",
        f"Ticker: {snapshot.ticker}",
        f"Current price: {snapshot.market_price:.2f} {snapshot.currency}".strip(),
    ]
    if snapshot.previous_close is not None:
        lines.append(f"Previous close: {snapshot.previous_close:.2f} {snapshot.currency}".strip())
    if snapshot.change is not None and snapshot.change_percent is not None:
        lines.append(f"Change vs previous close: {snapshot.change:+.2f} ({snapshot.change_percent:+.2f}%)")
    if snapshot.exchange:
        lines.append(f"Exchange: {snapshot.exchange}")
    if snapshot.market_time:
        lines.append(f"Market time: {snapshot.market_time.isoformat()}")
    lines.append(f"Source: {snapshot.source_url}")

    return Document(
        title=f"{snapshot.company} ({snapshot.ticker}) current price",
        url=snapshot.source_url,
        text="\n".join(lines),
        source="finance_quote",
        published=snapshot.market_time,
    )


def format_quote_response(snapshot_state: dict) -> str:
    company = snapshot_state.get("company") or snapshot_state.get("ticker") or "Unknown"
    ticker = snapshot_state.get("ticker") or ""
    price = snapshot_state.get("market_price")
    previous_close = snapshot_state.get("previous_close")
    change = snapshot_state.get("change")
    change_percent = snapshot_state.get("change_percent")
    currency = snapshot_state.get("currency") or ""
    exchange = snapshot_state.get("exchange") or ""
    market_time = snapshot_state.get("market_time") or ""
    source_url = snapshot_state.get("source_url") or ""

    lines = [f"**{company} ({ticker}) current price**"]
    if price is not None:
        lines.append(f"- Price: {price:.2f} {currency}".rstrip())
    if previous_close is not None:
        lines.append(f"- Previous close: {previous_close:.2f} {currency}".rstrip())
    if change is not None and change_percent is not None:
        lines.append(f"- Change vs previous close: {change:+.2f} ({change_percent:+.2f}%)")
    if exchange:
        lines.append(f"- Exchange: {exchange}")
    if market_time:
        lines.append(f"- Market time: {market_time}")
    lines.append("")
    lines.append("Sources")
    if source_url:
        lines.append(f"- {source_url}")
    return "\n".join(lines)
