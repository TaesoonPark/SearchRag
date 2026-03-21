from __future__ import annotations

import asyncio
import base64
import uuid
import os
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Any, List, Optional

from app.config import Config

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
]

try:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    GOOGLE_AVAILABLE = True
except Exception:
    GOOGLE_AVAILABLE = False


def _is_configured(cfg: Config) -> bool:
    return GOOGLE_AVAILABLE and (
        bool(cfg.google_service_account_path)
        or bool(cfg.google_oauth_token_path)
    )


def _build_gmail_service(cfg: Config):
    if not GOOGLE_AVAILABLE:
        raise RuntimeError("구글 라이브러리가 설치되지 않았습니다.")
    if cfg.google_service_account_path:
        credentials = service_account.Credentials.from_service_account_file(
            cfg.google_service_account_path,
            scopes=SCOPES,
        )
        if cfg.google_user_email:
            credentials = credentials.with_subject(cfg.google_user_email)
    elif cfg.google_oauth_token_path:
        if not os.path.exists(cfg.google_oauth_token_path):
            raise RuntimeError("GOOGLE_OAUTH_TOKEN_PATH 가 존재하지 않습니다.")
        try:
            credentials = Credentials.from_authorized_user_file(cfg.google_oauth_token_path, SCOPES)
        except Exception as exc:
            raise RuntimeError(
                "GOOGLE_OAUTH_TOKEN_PATH 파일 형식이 잘못되었습니다. "
                "authorized user JSON(token, refresh_token, client_id/client_secret/token_uri, scopes)인지 확인하세요."
            ) from exc
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            with open(cfg.google_oauth_token_path, "w", encoding="utf-8") as f:
                f.write(credentials.to_json())
    else:
        raise RuntimeError("구글 인증 설정이 없습니다.")

    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def _build_calendar_service(cfg: Config):
    if not GOOGLE_AVAILABLE:
        raise RuntimeError("구글 라이브러리가 설치되지 않았습니다.")
    if cfg.google_service_account_path:
        credentials = service_account.Credentials.from_service_account_file(
            cfg.google_service_account_path,
            scopes=SCOPES,
        )
        if cfg.google_user_email:
            credentials = credentials.with_subject(cfg.google_user_email)
    elif cfg.google_oauth_token_path:
        if not os.path.exists(cfg.google_oauth_token_path):
            raise RuntimeError("GOOGLE_OAUTH_TOKEN_PATH 가 존재하지 않습니다.")
        try:
            credentials = Credentials.from_authorized_user_file(cfg.google_oauth_token_path, SCOPES)
        except Exception as exc:
            raise RuntimeError(
                "GOOGLE_OAUTH_TOKEN_PATH 파일 형식이 잘못되었습니다. "
                "authorized user JSON(token, refresh_token, client_id/client_secret/token_uri, scopes)인지 확인하세요."
            ) from exc
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            with open(cfg.google_oauth_token_path, "w", encoding="utf-8") as f:
                f.write(credentials.to_json())
    else:
        raise RuntimeError("구글 인증 설정이 없습니다.")

    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _header_value(metadata_headers: List[dict], name: str) -> str:
    name_lower = name.lower()
    for header in metadata_headers:
        if header.get("name", "").lower() == name_lower:
            return header.get("value", "")
    return ""

async def list_recent_gmail_messages(
    cfg: Config,
    *,
    query: str = "in:inbox is:unread",
    max_results: int = 5,
) -> list[dict[str, Any]]:
    if not _is_configured(cfg):
        raise RuntimeError(
            "Google 연결이 설정되지 않았습니다. GOOGLE_SERVICE_ACCOUNT_PATH 또는 GOOGLE_OAUTH_TOKEN_PATH를 지정하세요."
        )

    def _runner():
        service = _build_gmail_service(cfg)
        response = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        messages = response.get("messages", []) or []
        results: list[dict[str, Any]] = []
        for item in messages:
            msg_id = item.get("id")
            if not msg_id:
                continue
            detail = (
                service.users()
                .messages()
                .get(userId="me", id=msg_id, format="metadata", metadataHeaders=["Subject", "From", "Date"])
                .execute()
            )
            headers = detail.get("payload", {}).get("headers", [])
            results.append(
                {
                    "id": msg_id,
                    "subject": _header_value(headers, "Subject") or "(제목 없음)",
                    "from": _header_value(headers, "From"),
                    "date": _header_value(headers, "Date"),
                    "snippet": detail.get("snippet", ""),
                    "threadId": detail.get("threadId", ""),
                }
            )
        return results

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _runner)


async def list_google_events(
    cfg: Config,
    *,
    query: str = "",
    max_results: int = 10,
    time_min: Optional[datetime] = None,
    time_max: Optional[datetime] = None,
    calendar_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    if not _is_configured(cfg):
        raise RuntimeError("Google 연결이 설정되지 않았습니다.")
    start = time_min or datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    cal_id = calendar_id or cfg.google_calendar_id
    end = time_max
    if end is not None and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    def _runner():
        service = _build_calendar_service(cfg)
        kwargs = {
            "calendarId": cal_id,
            "timeMin": start.isoformat(),
            "maxResults": max_results,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if end is not None:
            kwargs["timeMax"] = end.isoformat()
        result = service.events().list(**kwargs).execute()
        return result.get("items", [])

    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(None, _runner)
    if query:
        keyword = query.lower()
        filtered = []
        for item in items:
            title = (item.get("summary") or "").lower()
            desc = (item.get("description") or "").lower()
            if keyword in title or keyword in desc:
                filtered.append(item)
        items = filtered
    return items


def _parse_event_datetime(value: str) -> datetime:
    if not value:
        raise ValueError("시간 값이 비어있습니다.")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone(timedelta(hours=9)))
            return dt
        except ValueError:
            continue
    raise ValueError("지원하지 않는 시간 형식입니다. 예: 2026-03-20 10:30")


async def create_google_event(
    cfg: Config,
    *,
    title: str,
    start: str,
    end: str,
    description: str = "",
    calendar_id: Optional[str] = None,
) -> dict[str, Any]:
    if not title.strip():
        raise ValueError("이벤트 제목이 필요합니다.")
    start_dt = _parse_event_datetime(start)
    end_dt = _parse_event_datetime(end)
    if end_dt <= start_dt:
        raise ValueError("종료 시간은 시작 시간보다 빨라야 합니다.")

    cal_id = calendar_id or cfg.google_calendar_id

    def _runner():
        service = _build_calendar_service(cfg)
        body = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Seoul"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Seoul"},
        }
        return service.events().insert(calendarId=cal_id, body=body).execute()

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _runner)


def format_gmail_messages(messages: list[dict[str, Any]]) -> str:
    if not messages:
        return "메일이 없습니다."
    lines = ["최근 메일"]
    for idx, item in enumerate(messages, 1):
        subject = item.get("subject", "(제목 없음)")
        sender = item.get("from", "(발신자 없음)")
        date = item.get("date", "-")
        snippet = item.get("snippet", "").replace("\n", " ")[:160]
        lines.append(f"{idx}. {subject}\n  보낸사람: {sender}\n  수신: {date}\n  미리보기: {snippet}")
    return "\n".join(lines)


def format_gmail_send_result(result: dict[str, Any], *, to: str, subject: str) -> str:
    message_id = result.get("id", "")
    thread_id = result.get("threadId", "")
    labels = result.get("labelIds") or []
    trace_id = result.get("x_trace_message_id", "")
    lines = ["메일 전송 성공"]
    lines.append(f"수신: {to}")
    lines.append(f"제목: {subject}")
    if labels:
        lines.append(f"상태: {', '.join(labels)}")
    if trace_id:
        lines.append(f"추적ID: {trace_id}")
    if message_id:
        lines.append(f"메시지 ID: {message_id}")
    if thread_id:
        lines.append(f"스레드 ID: {thread_id}")
    return "\n".join(lines)


def _build_raw_email(
    to: str,
    subject: str,
    body: str,
    *,
    from_email: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> str:
    message = MIMEText(body or "", _charset="utf-8")
    message["to"] = to
    if from_email:
        message["from"] = from_email
    message["subject"] = subject
    if trace_id:
        message["X-Trace-Message-Id"] = trace_id
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
    return raw_message


async def send_gmail_message(
    cfg: Config,
    *,
    to: str,
    subject: str,
    body: str,
) -> dict[str, Any]:
    if not _is_configured(cfg):
        raise RuntimeError(
            "Google 연결이 설정되지 않았습니다. GOOGLE_SERVICE_ACCOUNT_PATH 또는 GOOGLE_OAUTH_TOKEN_PATH를 지정하세요."
        )
    to = to.strip()
    if not to:
        raise ValueError("수신자 이메일이 비어 있습니다.")
    if not subject.strip():
        raise ValueError("메일 제목이 비어 있습니다.")

    def _runner():
        service = _build_gmail_service(cfg)
        profile = service.users().getProfile(userId="me").execute()
        from_email = cfg.google_user_email or profile.get("emailAddress", "")
        trace_id = str(uuid.uuid4())
        raw_message = _build_raw_email(
            to=to,
            subject=subject,
            body=body,
            from_email=from_email,
            trace_id=trace_id,
        )
        result = service.users().messages().send(
            userId="me",
            body={"raw": raw_message},
        ).execute()
        if isinstance(result, dict):
            result = dict(result)
            result["x_trace_message_id"] = trace_id
        return result

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _runner)


def format_calendar_events(items: list[dict[str, Any]]) -> str:
    if not items:
        return "예정된 일정이 없습니다."
    lines = ["예정된 일정"]
    for idx, item in enumerate(items, 1):
        title = item.get("summary", "(제목 없음)")
        start_info = item.get("start", {})
        end_info = item.get("end", {})
        start = start_info.get("dateTime", start_info.get("date", ""))
        end = end_info.get("dateTime", end_info.get("date", ""))
        lines.append(f"{idx}. {title}")
        lines.append(f"  시작: {start}")
        lines.append(f"  종료: {end}")
        if item.get("location"):
            lines.append(f"  장소: {item.get('location')}")
    return "\n".join(lines)
