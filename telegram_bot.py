from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable, Optional
from datetime import datetime, timedelta
from pathlib import Path
import json

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from config import Config
from llm import build_llm
from google_tools import (
    create_google_event,
    format_calendar_events,
    format_gmail_send_result,
    format_gmail_messages,
    list_google_events,
    list_recent_gmail_messages,
    send_gmail_message,
)

logger = logging.getLogger(__name__)

MAX_TELEGRAM_MESSAGE_LEN = 3500
SEARCH_PREFIX = "검색 "
RESERVATION_PREFIX = "예약 "
RESERVATION_LIST_PREFIX = "예약목록"
RESERVATION_CANCEL_PREFIX = "예약삭제 "
RESERVATION_HELP_PREFIX = "도움말"
GMAIL_PREFIX = "메일 "
CALENDAR_PREFIX = "일정 "
ROUTER_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "command_router_system.txt"

RESERVATION_COMMAND_HINT = (
    "예약 기능 형식(표준):\n"
    "`예약 [반복] HH:MM [작업] [매개변수]`\n"
    "- 반복 생략 시 1회성 실행\n"
    "- 반복: 매일 | 평일 | 주말 | 매주 월,화,수 ...\n"
    "- 작업: 검색, 메일, 일정 (생략 가능, 기본 검색)\n"
    "예시:\n"
    "- `예약 14:30 검색 구글`\n"
    "- `예약 매일 14:30 검색 구글` (검색 기본)\n"
    "- `예약 주말 07:00 검색 주간 뉴스 요약`\n"
    "- `예약 매주 월,수 09:00 검색 상위차트`\n"
    "목록/관리:\n"
    "- `예약목록`, `예약삭제 R001`, `예약삭제 all`"
)
COMMAND_LIST_HINT = (
    "도움말\n"
    "명령은 아래 형식처럼 입력하거나, 의미를 그대로 적어도 대부분 자동으로 해석합니다.\n"
    "- `예약 <HH:MM> [작업] <파라미터>`: 예약 등록 (기본은 1회성)\n"
    "- `예약목록`: 현재 채팅의 예약 목록 보기\n"
    "- `예약삭제 <예약ID>`: 예약 삭제 (`예약삭제 all`로 전부 삭제)\n"
    "- `검색 <텍스트>`: 검색/요약 요청\n"
    "- `메일 검색 <검색어>`: Gmail 조회\n"
    "- `메일` 또는 `메일 조회`: 최근 미확인 메일 조회\n"
    "- `메일 보내기 <받는사람> | <제목> | <내용>`: Gmail 메일 보내기\n"
    "- `일정 조회`: 달력 최근 일정 조회\n"
    "- `일정 생성 <제목> | <시작> | <종료> | <설명>`: 달력 일정 등록\n"
    "- `메일 <명령>`: Gmail 조회\n"
    "- `일정 <명령>`: Google Calendar 조회/생성\n"
    "\n"
    "주기성 설정\n"
    "- 안 할 때(1회성): `예약 14:30 검색 구글`\n"
    "- 매일: `예약 매일 14:30 검색 구글`\n"
    "- 평일: `예약 평일 14:30 검색 구글`\n"
    "- 주말: `예약 주말 14:30 검색 구글`\n"
    "- 매주(요일 지정): `예약 매주 월,수 14:30 검색 구글`\n"
    "- 일정 예약 예시: `예약 매일 09:00 일정 생성 회의 | 2026-03-20 09:00 | 2026-03-20 10:00 | 주간회의`\n"
    "- 메일 예약 예시: `예약 평일 10:00 메일 보내기 a@example.com | 점심 약속 | 15시 만나기`\n"
    "- `/help`: 이 도움말 다시 보기\n"
    "- `/start`: 시작 안내"
)
ROUTER_PROMPT_CACHE: dict[str, str] = {}


def _router_prompt() -> str:
    cached = ROUTER_PROMPT_CACHE.get("text")
    if cached is not None:
        return cached
    prompt = ROUTER_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    ROUTER_PROMPT_CACHE["text"] = prompt
    return prompt


def _extract_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object found")
    return json.loads(text[start : end + 1])


def _parse_time(value: str) -> Optional[tuple[int, int]]:
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _normalize_weekday_name(value: str) -> Optional[int]:
    if not isinstance(value, str):
        return None
    return WEEKDAY_ALIAS.get(value.strip().lower())


def _normalize_weekdays(values: Any) -> list[int]:
    if not values:
        return []
    normalized: list[int] = []
    for item in values if isinstance(values, list) else [values]:
        day = _normalize_weekday_name(str(item))
        if day is None:
            continue
        if day not in normalized:
            normalized.append(day)
    normalized.sort()
    return normalized
SUPPORTED_JOB_TYPES = {
    "search": {
        "aliases": {"검색", "search"},
        "label": "검색",
        "help": "채팅 검색/요약 실행",
    },
    "gmail": {
        "aliases": {"메일", "gmail", "메일조회", "메일 조회"},
        "label": "Gmail 조회",
        "help": "Gmail 조회 실행",
    },
    "calendar": {
        "aliases": {"일정", "calendar", "캘린더"},
        "label": "달력 조회",
        "help": "Google Calendar 조회/등록",
    }
}

WEEKDAY_ALIAS = {
    "월": 0,
    "월요일": 0,
    "monday": 0,
    "화": 1,
    "화요일": 1,
    "tuesday": 1,
    "수": 2,
    "수요일": 2,
    "wednesday": 2,
    "목": 3,
    "목요일": 3,
    "thursday": 3,
    "금": 4,
    "금요일": 4,
    "friday": 4,
    "토": 5,
    "토요일": 5,
    "saturday": 5,
    "일": 6,
    "일요일": 6,
    "sunday": 6,
}


def _chunk_text(text: str, max_len: int = MAX_TELEGRAM_MESSAGE_LEN) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    current = []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if current_len + len(line) > max_len and current:
            chunks.append("".join(current).rstrip())
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("".join(current).rstrip())
    return chunks

def _is_allowed(cfg: Config, chat_id: int) -> bool:
    if not cfg.telegram_allowed_chat_ids:
        return True
    return chat_id in cfg.telegram_allowed_chat_ids


def _parse_reservation(text: str) -> Optional[dict]:
    if not text.startswith(RESERVATION_PREFIX):
        return None

    body = text[len(RESERVATION_PREFIX) :].strip()
    if not body:
        return None

    tokens = body.split()
    if len(tokens) < 2:
        return None

    mode = "once"
    weekdays: set[int] = set()
    i = 0

    if tokens[i] == "매일":
        mode = "daily"
        i += 1
    elif tokens[i] == "평일":
        mode = "weekdays"
        i += 1
    elif tokens[i] == "주말":
        mode = "weekend"
        i += 1
    elif tokens[i] == "매주":
        mode = "weekly"
        i += 1
        day_tokens: list[str] = []
        while i < len(tokens):
            if re.fullmatch(r"\d{1,2}:\d{2}", tokens[i]):
                break
            day_tokens.append(tokens[i])
            i += 1
        parsed_days = _parse_weekdays(day_tokens)
        if not parsed_days:
            return None
        weekdays = parsed_days

    if i >= len(tokens):
        return None

    time_raw = tokens[i]
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", time_raw)
    if not m:
        return None

    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    job = _parse_job(tokens[i + 1 :])
    if job is None:
        return None

    if mode == "weekly" and not weekdays:
        weekdays = _parse_weekdays(["월", "화", "수", "목", "금", "토", "일"])
        if not weekdays:
            return None

    if not mode == "weekly":
        weekdays = _default_weekdays_for_mode(mode)

    return {
        "hour": hour,
        "minute": minute,
        "job": job,
        "mode": mode,
        "weekdays": sorted(weekdays),
        "raw_input": text,
    }


def _parse_job(tokens: list[str]) -> Optional[dict[str, Any]]:
    if not tokens:
        return None

    maybe_type = tokens[0].strip().lower()
    for job_type, meta in SUPPORTED_JOB_TYPES.items():
        if maybe_type in meta["aliases"]:
            if len(tokens) < 2:
                return None
            payload = " ".join(tokens[1:]).strip()
            if job_type == "gmail":
                action = "search"
                if payload.startswith("보내기") or payload.startswith("전송"):
                    send = _parse_gmail_send_payload(payload)
                    if send is None:
                        return None
                    return {
                        "type": job_type,
                        "label": meta["label"],
                        "payload": payload,
                        "action": "send",
                        "to": send["to"],
                        "subject": send["subject"],
                        "body": send["body"],
                    }
                if payload.startswith("검색"):
                    return {
                        "type": job_type,
                        "label": meta["label"],
                        "payload": payload[len("검색") :].strip(),
                        "action": "search",
                    }
                if payload.startswith("읽지않은") or payload.startswith("안읽은") or payload.startswith("미확인"):
                    return {
                        "type": job_type,
                        "label": meta["label"],
                        "payload": "in:inbox is:unread",
                        "action": "unread",
                    }
                return {
                    "type": job_type,
                    "label": meta["label"],
                    "payload": payload or "in:inbox",
                    "action": action,
                }
            if job_type == "calendar":
                action = "create" if payload.startswith("생성") else "list"
                if action == "create" and payload.startswith("생성"):
                    payload = payload[len("생성") :].strip()
                return {
                    "type": job_type,
                    "label": meta["label"],
                    "payload": payload,
                    "action": action,
                }
            return {
                "type": job_type,
                "label": meta["label"],
                "payload": payload,
            }

    # 기본 동작: 검색 쿼리
    return {"type": "search", "label": SUPPORTED_JOB_TYPES["search"]["label"], "payload": " ".join(tokens).strip()}


def _parse_gmail_send_payload(text: str) -> Optional[dict[str, str]]:
    target = text.strip()
    for prefix in ("보내기", "전송", "메일보내기", "메일 전송"):
        if target.startswith(prefix):
            target = target[len(prefix) :].strip()
            break
    else:
        return None

    if target.startswith("|"):
        target = target[1:].strip()
    parts = [item.strip() for item in target.split("|", 3)]
    if len(parts) < 3:
        return None

    to = parts[0]
    subject = parts[1]
    body = parts[2]
    if not to or not subject:
        return None

    return {"to": to, "subject": subject, "body": body}


def _parse_gmail_command(text: str) -> Optional[dict[str, Any]]:
    body = text[len(GMAIL_PREFIX) :].strip()
    if not body:
        return {"task": "gmail", "action": "unread", "query": "in:inbox is:unread", "max_results": 5}

    if body.startswith("읽지않은") or body.startswith("안읽은") or body.startswith("미확인"):
        return {"task": "gmail", "action": "unread", "query": "in:inbox is:unread", "max_results": 5}

    m = re.match(r"^(\d+)\s*(개|개?)?\s*(메일|이메일)?\s*보기$", body)
    if m:
        return {
            "task": "gmail",
            "action": "unread",
            "query": "in:inbox is:unread",
            "max_results": max(1, min(int(m.group(1)), 20)),
        }

    if body.startswith("검색"):
        query = body[len("검색") :].strip()
        return {"task": "gmail", "action": "search", "query": query, "max_results": 5}

    if body.startswith("보내기") or body.startswith("전송"):
        payload = _parse_gmail_send_payload(body)
        if payload is None:
            return {
                "task": "gmail",
                "action": "send",
                "error": "format",
                "message": "메일 보내기 형식이 맞지 않습니다.\n예: `메일 보내기 a@example.com | 제목 | 내용`",
            }
        return {
            "task": "gmail",
            "action": "send",
            "to": payload["to"],
            "subject": payload["subject"],
            "body": payload["body"],
        }

    return {"task": "gmail", "action": "search", "query": body, "max_results": 5}


def _parse_calendar_command(text: str) -> Optional[dict[str, Any]]:
    body = text[len(CALENDAR_PREFIX) :].strip()
    if not body:
        return None

    if body.startswith("조회") or body.startswith("리스트") or body.startswith("목록"):
        return {"task": "calendar", "action": "list", "scope": "upcoming", "max_results": 10}

    if body in {"오늘", "오늘일정", "오늘 일정"}:
        today = datetime.now()
        start = datetime(today.year, today.month, today.day)
        end = start.replace(hour=23, minute=59, second=59)
        return {
            "task": "calendar",
            "action": "list",
            "time_min": start,
            "time_max": end,
            "max_results": 20,
        }

    if body.startswith("내일"):
        base = datetime.now() + timedelta(days=1)
        start = datetime(base.year, base.month, base.day)
        end = start.replace(hour=23, minute=59, second=59)
        return {
            "task": "calendar",
            "action": "list",
            "time_min": start,
            "time_max": end,
            "max_results": 20,
        }

    if body.startswith("생성") or body.startswith("추가"):
        payload = body[len("생성") :].strip()
        if not payload:
            payload = body[len("추가") :].strip()
        if not payload:
            return None
        # 포맷: "제목 | 시작 | 종료 | 설명"
        parts = [item.strip() for item in payload.split("|")]
        if len(parts) < 3:
            return None
        return {
            "task": "calendar",
            "action": "create",
            "title": parts[0],
            "start": parts[1],
            "end": parts[2],
            "description": parts[3] if len(parts) > 3 else "",
        }

    return {"task": "calendar", "action": "list", "scope": "upcoming", "max_results": 10, "query": body}


def _build_schedule_from_route(route: dict[str, Any], raw_text: str) -> Optional[dict]:
    task = route.get("mode", "once")
    mode = task if task in {"once", "daily", "weekdays", "weekend", "weekly"} else "once"

    parsed = _parse_time(route.get("time", ""))
    if not parsed:
        return None
    hour, minute = parsed

    weekdays: set[int] = set()
    if mode == "weekly":
        weekdays = set(_normalize_weekdays(route.get("weekdays")))
        if not weekdays:
            return None
    else:
        weekdays = _default_weekdays_for_mode(mode)

    job_type = str(route.get("job", "search")).strip().lower()
    if job_type not in SUPPORTED_JOB_TYPES:
        job_type = "search"
    query = (route.get("query") or "").strip()
    if not query:
        query = (route.get("message") or "").strip()
    if not query:
        if job_type == "gmail":
            query = "in:inbox is:unread"
        elif job_type == "calendar":
            query = ""
        elif "payload" in route and isinstance(route["payload"], str) and route["payload"].strip():
            query = route["payload"].strip()
        else:
            return None
    payload = (route.get("query") or route.get("payload") or route.get("message") or "").strip()
    if not payload:
        payload = route.get("search_query", "").strip()

    payload_source = route.get("payload", "") if isinstance(route.get("payload"), str) else payload
    if job_type == "gmail" and str(route.get("action", "")).strip() == "send":
        to = str(route.get("to", "")).strip()
        subject = str(route.get("subject", "")).strip()
        body = str(route.get("body", "")).strip()
        if not to or not subject or not body:
            return None
        payload_source = f"{to} | {subject} | {body}"

    return {
        "hour": hour,
        "minute": minute,
        "job": {
            "type": job_type,
            "label": SUPPORTED_JOB_TYPES[job_type]["label"],
            "action": route.get("action", ""),
            "max_results": int(route.get("max_results", 5)),
            "payload": payload_source or query,
            "to": route.get("to"),
            "subject": route.get("subject"),
            "body": route.get("body"),
        },
        "mode": mode,
        "weekdays": sorted(weekdays),
        "raw_input": raw_text,
    }


async def _route_user_input(text: str, cfg: Config) -> dict[str, Any]:
    if text == RESERVATION_LIST_PREFIX:
        return {"task": "schedule_list"}
    if text.startswith(RESERVATION_CANCEL_PREFIX):
        target = text[len(RESERVATION_CANCEL_PREFIX) :].strip()
        if not target:
            return {"task": "schedule_delete", "reservation_id": "", "error": "missing_reservation_id"}
        if target.lower() in {"all", "전체", "전부"}:
            target = "all"
        return {"task": "schedule_delete", "reservation_id": target.strip().upper()}
    if text == RESERVATION_HELP_PREFIX or text.startswith("/help"):
        return {"task": "help"}
    if text.startswith(GMAIL_PREFIX):
        parsed = _parse_gmail_command(text)
        if parsed is not None:
            return parsed
    if text.startswith(CALENDAR_PREFIX):
        parsed = _parse_calendar_command(text)
        if parsed is not None:
            return parsed
    if text.startswith(RESERVATION_PREFIX):
        legacy = _parse_reservation(text)
        if legacy is not None:
            return {"task": "schedule", **legacy}
        return {"task": "unknown", "message": text}
    if text.startswith(SEARCH_PREFIX):
        return {"task": "search", "query": text[len(SEARCH_PREFIX) :].strip() or text}

    try:
        llm = build_llm(cfg, temperature=0.0)
        prompt = _router_prompt()
        response = await asyncio.wait_for(
            llm.ainvoke([("system", prompt), ("user", text)]),
            timeout=max(1, cfg.llm_timeout),
        )
        content = response.content if hasattr(response, "content") else str(response)
        route = _extract_json(str(content))
    except Exception:
        return {"task": "chat", "query": text}

    if not isinstance(route, dict):
        return {"task": "chat", "query": text}

    task = str(route.get("task", "unknown")).strip()
    if task == "search":
        return {"task": "search", "query": str(route.get("query", text)).strip() or text}
    if task == "gmail":
        action = str(route.get("action", "search")).strip() or "search"
        result = {
            "task": "gmail",
            "action": action,
            "query": str(route.get("query", "in:inbox is:unread")).strip(),
            "max_results": int(route.get("max_results", 5)) if str(route.get("max_results", "5")).isdigit() else 5,
        }
        if action == "send":
            result["to"] = str(route.get("to", "")).strip()
            result["subject"] = str(route.get("subject", "")).strip()
            result["body"] = str(route.get("body", "")).strip()
        return result
    if task == "calendar":
        parsed = {
            "task": "calendar",
            "action": str(route.get("action", "list")).strip() or "list",
            "query": route.get("query"),
            "max_results": int(route.get("max_results", 10)) if str(route.get("max_results", "10")).isdigit() else 10,
        }
        if route.get("time_min"):
            parsed["time_min"] = route["time_min"]
        if route.get("time_max"):
            parsed["time_max"] = route["time_max"]
        if route.get("title"):
            parsed["title"] = str(route["title"])
        if route.get("start"):
            parsed["start"] = str(route["start"])
        if route.get("end"):
            parsed["end"] = str(route["end"])
        if route.get("description"):
            parsed["description"] = str(route.get("description", ""))
        if route.get("scope") == "upcoming":
            parsed["time_min"] = datetime.now()
        return parsed
    if task == "chat":
        return {"task": "chat", "query": str(route.get("message", text)).strip() or text}
    if task == "help":
        return {"task": "help"}
    if task == "schedule_list":
        return {"task": "schedule_list"}
    if task == "schedule_delete":
        reservation_id = str(route.get("reservation_id", "")).strip()
        if not reservation_id:
            return {"task": "schedule_delete", "reservation_id": "", "error": "missing_reservation_id"}
        if reservation_id.lower() in {"all", "전체", "전부"}:
            reservation_id = "all"
        return {"task": "schedule_delete", "reservation_id": reservation_id.upper()}
    if task == "schedule":
        legacy = _build_schedule_from_route(route, text)
        if legacy is None:
            return {"task": "unknown", "message": text}
        return {"task": "schedule", **legacy}

    return {"task": "chat", "query": text}


def _is_job_supported(job_type: str) -> bool:
    return job_type in SUPPORTED_JOB_TYPES


def _parse_weekdays(tokens: list[str]) -> set[int]:
    weekday_set: set[int] = set()
    for token in tokens:
        for part in token.split(","):
            day = WEEKDAY_ALIAS.get(part.strip().lower())
            if day is not None:
                weekday_set.add(day)
    return weekday_set


def _default_weekdays_for_mode(mode: str) -> set[int]:
    if mode == "weekdays":
        return {0, 1, 2, 3, 4}
    if mode == "weekend":
        return {5, 6}
    if mode in {"daily", "once"}:
        return set(range(7))
    return {0, 1, 2, 3, 4, 5, 6}


def _format_mode_label(mode: str) -> str:
    if mode == "once":
        return "1회성"
    if mode == "daily":
        return "매일"
    if mode == "weekdays":
        return "평일"
    if mode == "weekend":
        return "주말"
    if mode == "weekly":
        return "매주"
    return "기타"


def _format_weekdays(weekdays: set[int]) -> str:
    names = ["월", "화", "수", "목", "금", "토", "일"]
    if not weekdays:
        return "미지정"
    if set(weekdays) == set(range(7)):
        return "매일"
    if weekdays == {0, 1, 2, 3, 4}:
        return "평일"
    if weekdays == {5, 6}:
        return "주말"
    return "/".join(names[i] for i in sorted(weekdays))


def _format_remaining(seconds: float) -> str:
    seconds = max(0, int(seconds))
    day = seconds // 86400
    hour = (seconds % 86400) // 3600
    minute = (seconds % 3600) // 60
    parts = []
    if day:
        parts.append(f"{day}일")
    if hour:
        parts.append(f"{hour}시간")
    if minute:
        parts.append(f"{minute}분")
    if not parts:
        parts.append("곧 실행")
    return " ".join(parts)


def _next_run_time(now: datetime, hour: int, minute: int, weekdays: set[int]) -> datetime:
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)

    while candidate.weekday() not in weekdays:
        candidate += timedelta(days=1)
    return candidate


def _get_reservations(application) -> dict:
    return application.bot_data.setdefault("reservations", {})


def _new_reservation_id(application) -> str:
    seq = int(application.bot_data.get("reservation_seq", 0)) + 1
    application.bot_data["reservation_seq"] = seq
    return f"R{seq:03d}"


def _find_reservations_for_chat(application, chat_id: int) -> list[tuple[str, dict]]:
    return sorted(
        [
            (reservation_id, reservation)
            for reservation_id, reservation in _get_reservations(application).items()
            if reservation.get("chat_id") == chat_id
        ],
        key=lambda item: item[0],
    )


async def _run_reservation_loop(
    context,
    cfg: Config,
    reservation_id: str,
    graph_runner: Callable[[str], str],
    job: dict,
    hour: int,
    minute: int,
    mode: str,
    weekdays: list[int],
):
    reservation_store = _get_reservations(context.application)
    send_message = context.bot.send_message
    current_task = asyncio.current_task()

    try:
        while True:
            now = datetime.now()
            target = _next_run_time(now, hour, minute, set(weekdays))
            wait_seconds = (target - now).total_seconds()
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)

            reservation = reservation_store.get(reservation_id, {})
            if not reservation:
                return

            try:
                response = await _run_job(cfg, context, graph_runner, reservation, job)
                display_payload = reservation.get("payload", "")
                for chunk in _chunk_text(
                    f"[예약 실행] {job.get('label', job.get('type'))} {display_payload}\n\n{response}"
                ):
                    await send_message(chat_id=reservation["chat_id"], text=chunk)
                reservation["run_count"] = reservation.get("run_count", 0) + 1
                reservation["last_status"] = "success"
                reservation["last_run_at"] = datetime.now().isoformat(timespec="seconds")
                reservation["last_error"] = ""
            except Exception as exc:
                logger.exception("Reservation run failed: chat_id=%s job=%r", reservation["chat_id"], job)
                reservation["run_count"] = reservation.get("run_count", 0) + 1
                reservation["failure_count"] = reservation.get("failure_count", 0) + 1
                reservation["last_status"] = "failed"
                reservation["last_run_at"] = datetime.now().isoformat(timespec="seconds")
                reservation["last_error"] = str(exc)
                await send_message(
                    chat_id=reservation["chat_id"],
                    text=f"예약 실행 실패: {job.get('label', job.get('type'))}\n{exc}",
                )

            if mode == "once":
                break
    except asyncio.CancelledError:
        logger.info("예약 취소됨: id=%s", reservation_id)
        raise
    finally:
        if reservation_store.get(reservation_id, {}).get("task") is current_task:
            reservation_store.pop(reservation_id, None)


def _start_reservation_task(
    context,
    cfg: Config,
    graph_runner: Callable[[str], str],
    chat_id: int,
    reservation: dict,
) -> str:
    reservation_id = _new_reservation_id(context.application)
    reservation_store = _get_reservations(context.application)

    job = reservation["job"]
    if not _is_job_supported(job.get("type")):
        raise ValueError(f"지원하지 않는 예약 유형: {job.get('type')}")

    hour = reservation["hour"]
    minute = reservation["minute"]
    mode = reservation["mode"]
    weekdays = reservation["weekdays"]
    payload = job.get("payload", "")

    task = context.application.create_task(
        _run_reservation_loop(
            cfg,
            context,
            reservation_id,
            graph_runner,
            job,
            hour,
            minute,
            mode,
            weekdays,
        )
    )

    reservation_store[reservation_id] = {
        "id": reservation_id,
        "chat_id": chat_id,
        "job": job,
        "hour": hour,
        "minute": minute,
        "mode": mode,
        "weekdays": weekdays,
        "raw_input": reservation.get("raw_input", ""),
        "payload": payload,
        "task": task,
        "last_run_at": "",
        "last_status": "ready",
        "last_error": "",
        "run_count": 0,
        "failure_count": 0,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    task.add_done_callback(lambda _: reservation_store.get(reservation_id, {}).pop("task", None))
    return reservation_id


async def _execute_gmail_task(
    cfg: Config,
    payload: str,
    action: str,
    max_results: int = 5,
    query: str = "",
    to: str = "",
    subject: str = "",
    body: str = "",
) -> str:
    task_payload = str(payload).strip()
    if action == "unread":
        gmail_query = query or "in:inbox is:unread"
        messages = await list_recent_gmail_messages(cfg, query=gmail_query, max_results=max_results)
        return format_gmail_messages(messages)

    if action == "search":
        gmail_query = task_payload or query or ""
        if not gmail_query:
            gmail_query = "in:inbox"
        messages = await list_recent_gmail_messages(cfg, query=gmail_query, max_results=max_results)
        return format_gmail_messages(messages)

    if action == "send":
        result = await send_gmail_message(
            cfg,
            to=to,
            subject=subject,
            body=body,
        )
        return format_gmail_send_result(result, to=to, subject=subject)

    raise ValueError("지원하지 않는 Gmail 작업입니다.")


async def _execute_calendar_task(
    cfg: Config,
    *,
    payload: str,
    action: str = "list",
    query: Optional[str] = None,
    time_min: Optional[datetime] = None,
    time_max: Optional[datetime] = None,
    max_results: int = 10,
) -> str:
    parts = [p.strip() for p in payload.split("|")] if "|" in payload else []
    if action == "create":
        action = "create"
        if not (parts and len(parts) >= 3 and parts[0]):
            raise ValueError("일정 생성은 '제목 | 시작 | 종료' 형식이 필요합니다.")
        title, start, end = parts[0], parts[1], parts[2]
        description = parts[3] if len(parts) > 3 else ""
        event = await create_google_event(
            cfg,
            title=title,
            start=start,
            end=end,
            description=description,
        )
        return f"일정이 등록되었습니다.\n제목: {event.get('summary', '')}\n시작: {event.get('start', {}).get('dateTime', '')}\n종료: {event.get('end', {}).get('dateTime', '')}"
    if action != "create" and action != "list":
        raise ValueError("지원하지 않는 달력 작업입니다.")

    items = await list_google_events(
        cfg,
        query=query or "",
        max_results=max_results,
        time_min=time_min,
        time_max=time_max,
    )
    return format_calendar_events(items)


def _cancel_reservation(context, chat_id: int, reservation_id: str) -> bool:
    reservation_store = _get_reservations(context.application)
    reservation = reservation_store.get(reservation_id)
    if not reservation or reservation.get("chat_id") != chat_id:
        return False

    task = reservation.get("task")
    if task and not task.done():
        task.cancel()
    reservation_store.pop(reservation_id, None)
    return True


def _cancel_all_reservations(context, chat_id: int) -> int:
    reservation_store = _get_reservations(context.application)
    cancel_ids = [
        reservation_id
        for reservation_id, reservation in list(reservation_store.items())
        if reservation.get("chat_id") == chat_id
    ]
    for reservation_id in cancel_ids:
        reservation = reservation_store.get(reservation_id, {})
        task = reservation.get("task")
        if task and not task.done():
            task.cancel()
        reservation_store.pop(reservation_id, None)
    return len(cancel_ids)


def _format_reservation_list(context, chat_id: int) -> str:
    items = _find_reservations_for_chat(context.application, chat_id)
    if not items:
        return "현재 예약 내역이 없습니다."

    lines: list[str] = ["현재 예약 목록:"]
    now = datetime.now()
    for reservation_id, reservation in items:
        mode = _format_mode_label(reservation.get("mode", "once"))
        job = reservation.get("job", {})
        label = job.get("label") or job.get("type", "작업")
        payload = reservation.get("payload", "")
        hour = reservation.get("hour", 0)
        minute = reservation.get("minute", 0)
        weekdays_raw = set(reservation.get("weekdays", []))
        next_run = _next_run_time(now, hour, minute, set(weekdays_raw))
        remain = _format_remaining((next_run - now).total_seconds())
        lines.append(f"• {reservation_id} | {mode} {hour:02d}:{minute:02d} ({_format_weekdays(weekdays_raw)})")
        lines.append(f"  작업: {label}")
        lines.append(f"  내용: {payload}")
        lines.append(
            f"  다음 실행: {next_run.strftime('%Y-%m-%d %H:%M')} ({remain})"
        )
        lines.append(
            f"  실행: {reservation.get('run_count', 0)}회 / 실패: {reservation.get('failure_count', 0)}회"
            f" / 상태: {reservation.get('last_status', 'ready')}"
        )
        lines.append(
            f"  등록: {reservation.get('created_at', '-')}"
            f" / 마지막 실행: {reservation.get('last_run_at', '-') or '-'}"
        )
    return "\n".join(lines)


async def _send_command_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None:
        return
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=COMMAND_LIST_HINT,
        parse_mode="Markdown",
    )


async def _run_job(
    cfg: Config,
    context,
    graph_runner: Callable[[str], str],
    reservation: dict,
    job: dict,
) -> str:
    job_type = job.get("type")
    payload = job.get("payload", "")
    action = job.get("action", "list")
    max_results = int(job.get("max_results", 5))

    handlers = {
        "search": lambda: graph_runner(f"검색 {payload}"),
        "gmail": lambda: _execute_gmail_task(
            cfg=cfg,
            payload=payload,
            action=action or "search",
            query=payload,
            max_results=max_results,
            to=job.get("to", ""),
            subject=job.get("subject", ""),
            body=job.get("body", ""),
        ),
        "calendar": lambda: _execute_calendar_task(
            cfg=cfg,
            payload=payload,
            action=action,
        ),
    }
    runner = handlers.get(job_type)
    if runner is None:
        raise ValueError(f"지원하지 않는 예약 유형: {job_type}")

    return await runner()


def build_telegram_app(cfg: Config, graph_runner: Callable[[str], str]):
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        if not _is_allowed(cfg, update.effective_chat.id):
            return
        await update.message.reply_text(
            "명령어를 분석해 검색/예약/Gmail/캘린더 동작을 자동 판별합니다.\n"
            "`검색 {질의}` 또는 자연어로 검색할 수 있고, `예약 ...`으로 예약도 등록됩니다.\n"
            "`메일 ...`, `일정 ...` 명령도 그대로 지원합니다.\n"
            "`/help`로 사용 가능한 명령을 확인하세요.\n"
            f"{RESERVATION_COMMAND_HINT}"
        )

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None or update.message is None:
            return
        if not _is_allowed(cfg, update.effective_chat.id):
            return

        text = (update.message.text or "").strip()
        if not text:
            return

        route = await _route_user_input(text, cfg)

        if route["task"] == "help":
            await _send_command_list(update, context)
            return
        if route["task"] == "schedule_list":
            await update.message.reply_text(_format_reservation_list(context, update.effective_chat.id))
            return
        if route["task"] == "schedule_delete":
            reservation_id = route.get("reservation_id", "")
            if not reservation_id:
                await update.message.reply_text("예약 삭제 형식: `예약삭제 R001`", parse_mode="Markdown")
                return
            if reservation_id.lower() == "all":
                canceled = _cancel_all_reservations(context, update.effective_chat.id)
                if canceled:
                    await update.message.reply_text(f"`예약` {canceled}건을 일괄 삭제했습니다.", parse_mode="Markdown")
                else:
                    await update.message.reply_text("삭제할 예약이 없습니다.")
                return
            if _cancel_reservation(context, update.effective_chat.id, reservation_id):
                await update.message.reply_text(f"예약 `{reservation_id}`을(를) 삭제했습니다.", parse_mode="Markdown")
            else:
                await update.message.reply_text(f"예약 `{reservation_id}`을(를) 찾지 못했습니다.", parse_mode="Markdown")
            return
        if route["task"] == "gmail":
            try:
                gmail_query = route.get("query", "in:inbox is:unread")
                action = route.get("action", "search")
                max_results = int(route.get("max_results", 5))
                if action == "send":
                    to = str(route.get("to", "")).strip()
                    subject = str(route.get("subject", "")).strip()
                    body = str(route.get("body", "")).strip()
                    if not to or not subject:
                        await update.message.reply_text(
                            "메일 보내기 형식이 맞지 않습니다.\n"
                            "예: `메일 보내기 a@example.com | 제목 | 내용`\n"
                            "`메일 보내기` 뒤에는 받는사람, 제목, 내용이 `|`로 구분되어야 합니다.",
                            parse_mode="Markdown",
                        )
                        return
                    response = await _execute_gmail_task(
                        cfg,
                        payload="",
                        action=action,
                        query=gmail_query,
                        max_results=max_results,
                        to=to,
                        subject=subject,
                        body=body,
                    )
                else:
                    response = await _execute_gmail_task(
                        cfg,
                        payload=gmail_query,
                        action=action,
                        query=gmail_query,
                        max_results=max_results,
                    )
                for chunk in _chunk_text(response):
                    await update.message.reply_text(chunk)
            except Exception as exc:
                await update.message.reply_text(f"Gmail 작업 중 오류가 발생했습니다: {exc}")
            return
        if route["task"] == "calendar":
            try:
                action = route.get("action", "list")
                if action == "create":
                    title = str(route.get("title", "")).strip()
                    start = str(route.get("start", "")).strip()
                    end = str(route.get("end", "")).strip()
                    description = str(route.get("description", "")).strip()
                    if not (title and start and end):
                        await update.message.reply_text(
                            "캘린더 생성 형식이 올바르지 않습니다.\n예: `일정 생성 회의 | 2026-03-20 10:00 | 2026-03-20 11:00 | 회의 내용`",
                            parse_mode="Markdown",
                        )
                        return
                    payload = f"{title} | {start} | {end} | {description}".rstrip(" |")
                    response = await _execute_calendar_task(
                        cfg=cfg,
                        payload=payload,
                        action="create",
                    )
                else:
                    payload = ""
                    if route.get("query"):
                        payload = str(route.get("query"))
                    response = await _execute_calendar_task(
                        cfg=cfg,
                        payload=payload,
                        action="list",
                        query=payload if payload else None,
                        time_min=route.get("time_min"),
                        time_max=route.get("time_max"),
                        max_results=int(route.get("max_results", 10)),
                    )
                for chunk in _chunk_text(response):
                    await update.message.reply_text(chunk)
            except Exception as exc:
                await update.message.reply_text(f"캘린더 작업 중 오류가 발생했습니다: {exc}")
            return
        if route["task"] == "schedule":
            reservation = {
                "hour": route["hour"],
                "minute": route["minute"],
                "job": route["job"],
                "mode": route["mode"],
                "weekdays": route["weekdays"],
                "raw_input": route.get("raw_input", text),
            }
            reservation_id = _start_reservation_task(
                context,
                cfg,
                graph_runner,
                update.effective_chat.id,
                reservation,
            )
            await update.message.reply_text(
                f"`{reservation_id}` 생성됨\n"
                f"`{_format_mode_label(reservation['mode'])}` `{reservation['hour']:02d}:{reservation['minute']:02d}` "
                f"`{reservation['job']['label']}` `{reservation['job']['payload']}` 예약이 등록되었습니다.",
                parse_mode="Markdown",
            )
            return

        query = route.get("query", text)
        if route["task"] == "search" and not query.startswith(SEARCH_PREFIX):
            query = f"{SEARCH_PREFIX}{query}"
        if route["task"] == "search":
            waiting_message = "검색 중... 잠시만 기다려주세요."
        elif route["task"] in {"gmail", "calendar"}:
            waiting_message = "요청을 처리 중입니다. 잠시만 기다려주세요."
        else:
            waiting_message = "생각 중... 잠시만 기다려주세요."
        await update.message.reply_text(waiting_message)

        try:
            response = await graph_runner(query)
        except Exception as exc:
            logger.exception("Graph execution failed")
            await update.message.reply_text(f"처리 중 오류가 발생했습니다: {exc}")
            return
        for chunk in _chunk_text(response):
            await update.message.reply_text(chunk)

    app = ApplicationBuilder().token(cfg.telegram_bot_token).build()
    async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _send_command_list(update, context)

    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app
