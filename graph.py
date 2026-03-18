from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from config import Config
from llm import build_llm
from models import Document, SearchResult
from normalize import dedupe_preserve, trim_text
from search import collect_documents, searxng_search

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
logger = logging.getLogger(__name__)


class SearchPlan(BaseModel):
    queries: List[str] = Field(default_factory=list)
    time_range_days: int = 30
    must_include_sites: List[str] = Field(default_factory=list)
    notes: str = ""


class VerifyResult(BaseModel):
    is_sufficient: bool = True
    gaps: List[str] = Field(default_factory=list)
    next_queries: List[str] = Field(default_factory=list)
    notes: str = ""


class GraphState(TypedDict, total=False):
    raw_query: str
    query: str
    related_queries: List[str]
    search_results: List[Dict[str, Any]]
    documents: List[Dict[str, Any]]
    attempts: int
    is_sufficient: bool
    verification_notes: str
    next_queries: List[str]
    response: str


SEARCH_PREFIX = "검색 "


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def extract_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found")
    return json.loads(text[start : end + 1])


def _doc_summary(doc: Document, max_chars: int) -> dict:
    return {
        "title": doc.title,
        "url": doc.url,
        "published": doc.published.isoformat() if doc.published else "",
        "source": doc.source,
        "text": trim_text(doc.text, max_chars),
    }


def _result_summary(result: SearchResult) -> dict:
    return {
        "title": result.title,
        "url": result.url,
        "snippet": result.snippet,
        "source": result.source,
        "engine": result.engine,
        "published": result.published.isoformat() if result.published else "",
    }


def _is_search_request(text: str) -> bool:
    return text.startswith(SEARCH_PREFIX)


def _normalize_search_query(text: str) -> str:
    if _is_search_request(text):
        stripped = text[len(SEARCH_PREFIX) :].strip()
        if stripped:
            return stripped
    return text.strip()


async def search_agent(state: GraphState, cfg: Config) -> GraphState:
    started_at = perf_counter()
    llm = build_llm(cfg, temperature=0.2)
    system_prompt = load_prompt("search_agent_system.txt")

    raw_query = state.get("raw_query") or state["query"]
    query = _normalize_search_query(raw_query)
    previous_gaps = "\n".join(state.get("next_queries", []))
    logger.info("search_agent started: query=%r attempt=%s", query, state.get("attempts", 0) + 1)

    user_prompt = (
        f"키워드: {query}\n"
        f"부족한 부분/재검색 요청: {previous_gaps}\n"
        "JSON 스키마를 따르세요."
    )

    response = await llm.ainvoke(
        [
            ("system", system_prompt),
            ("user", user_prompt),
        ]
    )

    try:
        plan = SearchPlan.model_validate(extract_json(response.content))
    except Exception:
        plan = SearchPlan(
            queries=[],
            time_range_days=cfg.max_age_days,
            must_include_sites=[
                "finance.yahoo.com",
                "investing.com",
                "twitter.com",
                "reddit.com",
            ],
            notes="fallback plan",
        )

    queries = [query] + plan.queries
    queries = dedupe_preserve(queries)

    for site in plan.must_include_sites:
        if site:
            queries.append(f"{query} site:{site}")

    queries = dedupe_preserve(queries)
    logger.info("search_agent query plan ready: query_count=%s", len(queries))

    timeout = cfg.fetch_timeout

    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True) as client:
        searx_tasks = [
            searxng_search(client, cfg.searxng_base_url, q, cfg.searxng_engines, timeout)
            for q in queries
        ]

        searx_results_lists = await asyncio.gather(*searx_tasks) if searx_tasks else []

    search_results: List[SearchResult] = []
    for batch in searx_results_lists:
        search_results.extend(batch)

    seen_urls = set()
    deduped: List[SearchResult] = []
    for result in search_results:
        if not result.url or result.url in seen_urls:
            continue
        seen_urls.add(result.url)
        deduped.append(result)

    documents = await collect_documents(
        deduped,
        timeout=cfg.fetch_timeout,
        max_chars=cfg.max_doc_chars,
        max_docs=cfg.max_docs_to_fetch,
        max_concurrency=cfg.max_fetch_concurrency,
    )

    attempts = state.get("attempts", 0) + 1
    elapsed = perf_counter() - started_at
    logger.info(
        "search_agent finished: query=%r attempt=%s queries=%s raw_results=%s deduped_results=%s documents=%s elapsed=%.2fs",
        query,
        attempts,
        len(queries),
        len(search_results),
        len(deduped),
        len(documents),
        elapsed,
    )

    return {
        "query": query,
        "related_queries": queries,
        "search_results": [_result_summary(r) for r in deduped],
        "documents": [_doc_summary(d, cfg.max_doc_chars) for d in documents],
        "attempts": attempts,
        "next_queries": [],
    }


async def verify_agent(state: GraphState, cfg: Config) -> GraphState:
    started_at = perf_counter()
    llm = build_llm(cfg, temperature=0.1)
    system_prompt = load_prompt("verify_agent_system.txt")

    documents = state.get("documents", [])
    doc_count = len(documents)
    logger.info("verify_agent started: query=%r doc_count=%s", state.get("query"), doc_count)

    now = datetime.utcnow()
    recent_cutoff = now - timedelta(days=cfg.max_age_days)

    recent_docs = 0
    docs_with_date = 0
    for doc in documents:
        published = doc.get("published")
        if not published:
            continue
        try:
            dt = datetime.fromisoformat(published)
        except ValueError:
            continue
        docs_with_date += 1
        if dt >= recent_cutoff:
            recent_docs += 1

    summary = {
        "doc_count": doc_count,
        "recent_docs": recent_docs,
        "docs_with_date": docs_with_date,
        "max_age_days": cfg.max_age_days,
        "min_docs": cfg.min_docs,
        "min_recent_docs": cfg.min_recent_docs,
        "sample_titles": [doc.get("title", "") for doc in documents[:5]],
    }

    response = await llm.ainvoke(
        [
            ("system", system_prompt),
            ("user", f"검증 요약: {json.dumps(summary, ensure_ascii=False)}"),
        ]
    )

    try:
        verdict = VerifyResult.model_validate(extract_json(response.content))
    except Exception:
        verdict = VerifyResult(
            is_sufficient=doc_count >= cfg.min_docs,
            gaps=["검증 JSON 파싱 실패"],
            next_queries=[state.get("query", "")],
            notes="fallback verdict",
        )

    if docs_with_date == 0:
        heuristics_ok = doc_count >= cfg.min_docs
    else:
        heuristics_ok = doc_count >= cfg.min_docs and recent_docs >= cfg.min_recent_docs
    is_sufficient = verdict.is_sufficient and heuristics_ok

    notes = verdict.notes
    if not heuristics_ok:
        notes = f"Heuristic 부족: doc_count={doc_count}, recent_docs={recent_docs}. {notes}"
    elapsed = perf_counter() - started_at
    logger.info(
        "verify_agent finished: query=%r sufficient=%s recent_docs=%s docs_with_date=%s elapsed=%.2fs",
        state.get("query"),
        is_sufficient,
        recent_docs,
        docs_with_date,
        elapsed,
    )

    return {
        "is_sufficient": is_sufficient,
        "verification_notes": notes,
        "next_queries": verdict.next_queries,
    }


async def direct_chat_agent(state: GraphState, cfg: Config) -> GraphState:
    started_at = perf_counter()
    llm = build_llm(cfg, temperature=0.6)
    query = state.get("raw_query") or state["query"]
    logger.info("direct_chat_agent started: query=%r", query)

    response = await llm.ainvoke([("user", query)])
    content = response.content.strip()

    elapsed = perf_counter() - started_at
    logger.info(
        "direct_chat_agent finished: query=%r response_chars=%s elapsed=%.2fs",
        query,
        len(content),
        elapsed,
    )

    return {
        "query": query,
        "response": content,
    }


async def writer_agent(state: GraphState, cfg: Config) -> GraphState:
    started_at = perf_counter()
    llm = build_llm(cfg, temperature=0.2)
    system_prompt = load_prompt("writer_agent_system.txt")

    docs = state.get("documents", [])
    logger.info("writer_agent started: query=%r doc_count=%s", state.get("query"), len(docs))
    payload = {
        "query": state.get("query"),
        "documents": docs,
    }

    response = await llm.ainvoke(
        [
            ("system", system_prompt),
            ("user", json.dumps(payload, ensure_ascii=False)),
        ]
    )
    elapsed = perf_counter() - started_at
    logger.info(
        "writer_agent finished: query=%r response_chars=%s elapsed=%.2fs",
        state.get("query"),
        len(response.content.strip()),
        elapsed,
    )

    return {
        "response": response.content.strip(),
    }


def build_graph(cfg: Config):
    graph = StateGraph(GraphState)

    async def _search(state: GraphState) -> GraphState:
        return await search_agent(state, cfg)

    async def _verify(state: GraphState) -> GraphState:
        return await verify_agent(state, cfg)

    async def _direct_chat(state: GraphState) -> GraphState:
        return await direct_chat_agent(state, cfg)

    async def _writer(state: GraphState) -> GraphState:
        return await writer_agent(state, cfg)

    graph.add_node("search_agent", _search)
    graph.add_node("verify_agent", _verify)
    graph.add_node("direct_chat_agent", _direct_chat)
    graph.add_node("writer_agent", _writer)

    def _route_start(state: GraphState) -> str:
        query = state.get("raw_query") or state.get("query", "")
        if _is_search_request(query):
            return "search_agent"
        return "direct_chat_agent"

    graph.add_conditional_edges(START, _route_start)
    graph.add_edge("search_agent", "verify_agent")
    graph.add_edge("direct_chat_agent", END)

    def _route(state: GraphState) -> str:
        if state.get("is_sufficient"):
            return "writer_agent"
        if state.get("attempts", 0) >= cfg.max_search_attempts:
            return "writer_agent"
        return "search_agent"

    graph.add_conditional_edges("verify_agent", _route)
    graph.add_edge("writer_agent", END)

    return graph.compile()
