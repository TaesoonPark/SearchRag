from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, TypedDict, Optional

import httpx
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.config import Config
from app.llm import build_llm
from app.models import Document, SearchResult
from app.normalize import dedupe_preserve, trim_text
from app.search import collect_documents, naver_search, searxng_search

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
    seen_urls: List[str]
    search_results: List[Dict[str, Any]]
    documents: List[Dict[str, Any]]
    attempts: int
    is_sufficient: bool
    verification_notes: str
    next_queries: List[str]
    response: str


SEARCH_PREFIX = "검색 "


async def _invoke_with_timeout(llm, messages: list[tuple[str, str]], timeout: int):
    return await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout)


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


def _merge_items_by_url(existing: List[Dict[str, Any]], new_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for item in existing + new_items:
        url = str(item.get("url", "")).strip()
        if not url or url in seen:
            continue
        seen.add(url)
        merged.append(item)

    return merged


def _serialize_for_log(value: Any, max_len: int = 120) -> str:
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "..."


def _serialize_query_list(values: Any, max_items: int = 8, item_len: int = 100) -> str:
    if not isinstance(values, (list, tuple)):
        return _serialize_for_log(values)
    normalized: list[str] = []
    for v in values:
        item = str(v).strip()
        if not item:
            continue
        normalized.append(_serialize_for_log(item, item_len))
        if len(normalized) >= max_items:
            break
    if not normalized:
        return "[]"
    if len(values) > max_items:
        return f"[{', '.join(normalized)}, ...(+{len(values)-len(normalized)}개)]"
    return f"[{', '.join(normalized)}]"


def _normalize_query(query: str) -> str:
    normalized = re.sub(r"\s+", " ", query.strip().lower())
    return re.sub(r"[^0-9a-z가-힣\s]", "", normalized)


def _is_similar_query(query: str, candidates: set[str]) -> bool:
    current = _normalize_query(query)
    if not current:
        return True
    current_tokens = set(current.split())
    if not current_tokens:
        return True

    for raw in candidates:
        prev = _normalize_query(raw)
        if not prev:
            continue
        prev_tokens = set(prev.split())
        if not prev_tokens:
            continue

        if prev == current:
            return True
        if current_tokens == prev_tokens:
            return True
        if len(current_tokens) >= 2 and (current_tokens.issubset(prev_tokens) or prev_tokens.issubset(current_tokens)):
            return True

        if max(len(current_tokens), len(prev_tokens)) >= 4:
            intersection = current_tokens & prev_tokens
            union_size = len(current_tokens | prev_tokens)
            if union_size and len(intersection) >= int(max(len(current_tokens), len(prev_tokens)) * 0.7):
                return True

    return False


def _to_utc_datetime(value: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _log_writer_inputs(state: GraphState) -> None:
    docs = state.get("documents", [])
    logger.info(
        "writer_agent input: query=%r doc_count=%s",
        state.get("query"),
        len(docs),
    )
    for idx, doc in enumerate(docs[:8], start=1):
        if not isinstance(doc, dict):
            continue
        logger.info(
            "writer_agent document[%s]: title=%s url=%s source=%s published=%s",
            idx,
            _serialize_for_log(doc.get("title", ""), 80),
            _serialize_for_log(doc.get("url", ""), 120),
            _serialize_for_log(doc.get("source", ""), 40),
            _serialize_for_log(doc.get("published", ""), 40),
        )
        logger.debug(
            "writer_agent document[%s] text=%s",
            idx,
            _serialize_for_log(doc.get("text", ""), 200),
        )


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
    retry_queries = state.get("next_queries", [])
    previous_queries = state.get("related_queries", [])
    logger.info("search_agent started: query=%r attempt=%s", query, state.get("attempts", 0) + 1)

    prompt_payload = {
        "original_query": raw_query,
        "normalized_query": query,
        "attempt": state.get("attempts", 0) + 1,
        "previous_queries": previous_queries[-12:],
        "retry_queries": retry_queries[:6],
    }

    try:
        response = await _invoke_with_timeout(
            llm,
            [
                ("system", system_prompt),
                ("user", json.dumps(prompt_payload, ensure_ascii=False)),
            ],
            cfg.llm_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("search_agent LLM timeout: query=%r timeout=%s", query, cfg.llm_timeout)
        response = None
    except Exception as exc:
        logger.warning("search_agent LLM 호출 실패: query=%r error=%s", query, exc)
        response = None

    try:
        content = response.content if response is not None else ""
        plan = SearchPlan.model_validate(extract_json(content))
    except Exception as exc:
        logger.warning("search_agent 계획 파싱 실패: query=%r error=%s", query, exc)
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

    queries = [query] + retry_queries + plan.queries
    queries = dedupe_preserve(queries)

    site_queries: list[str] = []
    for site in plan.must_include_sites:
        if site:
            site_queries.append(f"{query} site:{site}")
    queries.extend(site_queries)

    queries = dedupe_preserve(queries)
    previous_query_set = {item.strip() for item in previous_queries if item.strip()}
    retry_query_set = {item.strip() for item in retry_queries if item.strip()}
    reused_from_previous = [item for item in queries if item.strip() in previous_query_set]
    fresh_queries = []
    for item in queries:
        candidate = item.strip()
        if not candidate:
            continue
        if _is_similar_query(candidate, previous_query_set):
            reused_from_previous.append(candidate)
            continue
        fresh_queries.append(candidate)
    if not fresh_queries:
        fresh_queries = queries
        if retry_query_set:
            logger.warning(
                "search_agent retry_queries_all_duplicated: no fresh query found, falling back to all queries",
            )
        logger.warning(
            "search_agent no_fresh_queries_after_dedupe: fallback=%s",
            _serialize_query_list(queries),
        )

    suppressed = [item for item in queries if item.strip() and item not in fresh_queries]

    logger.info(
        "search_agent retry source: incoming_next_queries=%s",
        _serialize_query_list(retry_queries),
    )
    logger.info(
        "search_agent candidate queries (plan/site/retry): plan=%s, site=%s, retry=%s",
        _serialize_query_list(plan.queries),
        _serialize_query_list(site_queries),
        _serialize_query_list(retry_queries),
    )
    logger.info(
        "search_agent llm plan queries=%s",
        _serialize_query_list(plan.queries),
    )
    logger.info(
        "search_agent query overlap: reused_from_previous=%s",
        _serialize_query_list(reused_from_previous),
    )
    logger.info(
        "search_agent query suppressed=%s",
        _serialize_query_list(suppressed),
    )

    logger.info(
        "search_agent query plan ready: query_count=%s fresh_query_count=%s prior_query_count=%s used_queries=%s",
        len(queries),
        len(fresh_queries),
        len(previous_query_set),
        _serialize_query_list(fresh_queries),
    )

    timeout = cfg.fetch_timeout

    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True) as client:
        searx_tasks = (
            [
                searxng_search(client, cfg.searxng_base_url, q, cfg.searxng_engines, timeout)
                for q in fresh_queries
            ]
            if cfg.searxng_enabled
            else []
        )
        naver_sort = "sim" if cfg.naver_search_sort not in {"sim", "date"} else cfg.naver_search_sort
        naver_type = cfg.naver_search_type if cfg.naver_search_type in {"news", "webkr"} else "webkr"
        naver_tasks = [
            naver_search(
                client,
                q,
                cfg.naver_client_id,
                cfg.naver_client_secret,
                naver_type,
                naver_sort,
                cfg.naver_search_display,
                cfg.naver_search_start,
                timeout,
            )
            for q in fresh_queries
        ]

        searx_results_lists = await asyncio.gather(*searx_tasks) if searx_tasks else []
        naver_results_lists = await asyncio.gather(*naver_tasks) if naver_tasks else []

    search_results: List[SearchResult] = []
    for batch in searx_results_lists:
        search_results.extend(batch)
    for batch in naver_results_lists:
        search_results.extend(batch)

    prior_seen_urls = {item.strip() for item in state.get("seen_urls", []) if item.strip()}
    batch_seen_urls = set()
    deduped: List[SearchResult] = []
    for result in search_results:
        if not result.url or result.url in prior_seen_urls or result.url in batch_seen_urls:
            continue
        batch_seen_urls.add(result.url)
        deduped.append(result)

    documents = await collect_documents(
        deduped,
        timeout=cfg.fetch_timeout,
        max_chars=cfg.max_doc_chars,
        max_docs=cfg.max_docs_to_fetch,
        max_concurrency=cfg.max_fetch_concurrency,
    )

    attempts = state.get("attempts", 0) + 1
    merged_search_results = _merge_items_by_url(state.get("search_results", []), [_result_summary(r) for r in deduped])
    merged_documents = _merge_items_by_url(state.get("documents", []), [_doc_summary(d, cfg.max_doc_chars) for d in documents])
    seen_urls_all = dedupe_preserve([*state.get("seen_urls", []), *[r.url for r in deduped if r.url]])
    elapsed = perf_counter() - started_at
    logger.info(
        "search_agent finished: query=%r attempt=%s fresh_queries=%s raw_results=%s new_results=%s total_results=%s new_documents=%s total_documents=%s elapsed=%.2fs",
        query,
        attempts,
        len(fresh_queries),
        len(search_results),
        len(deduped),
        len(merged_search_results),
        len(documents),
        len(merged_documents),
        elapsed,
    )

    return {
        "query": query,
        "related_queries": dedupe_preserve([*previous_queries, *queries]),
        "seen_urls": seen_urls_all,
        "search_results": merged_search_results,
        "documents": merged_documents,
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

    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=cfg.max_age_days)

    recent_docs = 0
    docs_with_date = 0
    for doc in documents:
        published = doc.get("published")
        if not published:
            continue
        dt = _to_utc_datetime(published)
        if dt is None:
            continue
        docs_with_date += 1
        if dt >= recent_cutoff:
            recent_docs += 1

    summary = {
        "original_query": state.get("raw_query") or state.get("query"),
        "active_query": state.get("query"),
        "related_queries": state.get("related_queries", [])[-12:],
        "doc_count": doc_count,
        "recent_docs": recent_docs,
        "docs_with_date": docs_with_date,
        "max_age_days": cfg.max_age_days,
        "min_docs": cfg.min_docs,
        "min_recent_docs": cfg.min_recent_docs,
        "search_results_sample": state.get("search_results", [])[:8],
        "documents_sample": documents[:6],
    }

    try:
        response = await _invoke_with_timeout(
            llm,
            [
                ("system", system_prompt),
                ("user", json.dumps(summary, ensure_ascii=False)),
            ],
            cfg.llm_timeout,
        )
    except Exception as exc:
        logger.warning("verify_agent LLM 호출 실패: query=%r error=%s", state.get("query"), exc)
        response = None

    try:
        content = response.content if response is not None else ""
        verdict = VerifyResult.model_validate(extract_json(content))
    except Exception as exc:
        logger.warning("verify_agent 검증 파싱 실패: query=%r error=%s", state.get("query"), exc)
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
    logger.info(
        "verify_agent recommended_queries=%s",
        _serialize_query_list(verdict.next_queries),
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

    try:
        response = await _invoke_with_timeout(llm, [("user", query)], cfg.llm_timeout)
    except Exception as exc:
        logger.warning("direct_chat_agent LLM 호출 실패: query=%r error=%s", query, exc)
        response = None
    if response is None:
        return {
            "query": query,
            "response": "요청 처리에 시간이 오래 걸려 응답을 만들지 못했습니다. 잠시 후 다시 시도해주세요.",
        }
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
    _log_writer_inputs(state)
    logger.info("writer_agent started: query=%r doc_count=%s", state.get("query"), len(docs))
    payload = {
        "original_query": state.get("raw_query") or state.get("query"),
        "active_query": state.get("query"),
        "verification_notes": state.get("verification_notes", ""),
        "search_results_sample": state.get("search_results", [])[:8],
        "documents": docs,
    }

    try:
        response = await _invoke_with_timeout(
            llm,
            [
                ("system", system_prompt),
                ("user", json.dumps(payload, ensure_ascii=False)),
            ],
            cfg.llm_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("writer_agent LLM timeout: query=%r timeout=%s", state.get("query"), cfg.llm_timeout)
        docs = state.get("documents", [])
        headline = state.get("active_query") or state.get("query") or state.get("raw_query") or ""
        lines = [
            f"- {d.get('title', '(제목 없음)')} ({d.get('url', '-')})"
            for d in docs[:8]
            if isinstance(d, dict)
        ]
        return {
            "response": (
                f"{headline}에 대한 요약을 생성하지 못했습니다.\n"
                "요청 수집 자료 임시 목록입니다.\n" + ("\n".join(lines) if lines else "수집된 자료가 없습니다.")
            )
        }
    except Exception as exc:
        logger.warning("writer_agent LLM 호출 실패: query=%r error=%s", state.get("query"), exc)
        response = None

    if response is None:
        return {
            "response": "요약을 생성하지 못했습니다. 잠시 후 다시 시도해주세요.",
        }
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
