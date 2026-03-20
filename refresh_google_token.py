#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from config import load_config
from google_auth_oauthlib.flow import InstalledAppFlow


def _resolve_path(base_dir: Path, value: str) -> Path:
    raw = value.strip()
    if not raw:
        raise ValueError("경로 값이 비어있습니다.")
    path = Path(raw).expanduser()
    return path if path.is_absolute() else base_dir / path


def _build_scopes() -> list[str]:
    return [
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar",
    ]


def _guess_client_secret_path(base_dir: Path) -> list[Path]:
    candidates = []
    for item in (base_dir / "configuration").glob("*.json"):
        if "token" in item.name.lower():
            continue
        candidates.append(item)
    return sorted(candidates)


def _describe_client_secret(client_path: Path) -> str:
    try:
        data = json.loads(client_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"OAuth client 파일 JSON 파싱 실패: {client_path}") from exc

    if isinstance(data, dict) and "installed" in data:
        return "installed"
    if isinstance(data, dict) and "web" in data:
        return "web"
    return "unknown"


def main() -> None:
    cfg = load_config()

    parser = argparse.ArgumentParser(description="Google OAuth 토큰(token.json) 재발급 도구")
    parser.add_argument(
        "--client-path",
        default=cfg.google_oauth_client_path or "",
        help="OAuth client_secret JSON 경로 (예: configuration/client_secret.json)",
    )
    parser.add_argument(
        "--token-path",
        default=cfg.google_oauth_token_path or "configuration/token.json",
        help="재생성할 토큰 저장 경로",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="OAuth 로컬 콜백 포트 (0이면 임시 포트 사용)",
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="OAuth 로컬 콜백 호스트 (기본: localhost)",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="브라우저 자동 실행 없이 로컬 콜백 방식으로 인증",
    )
    args = parser.parse_args()

    client_candidates = _guess_client_secret_path(base_dir=Path(__file__).resolve().parent)
    if not args.client_path:
        if len(client_candidates) == 1:
            args.client_path = str(client_candidates[0])
        elif len(client_candidates) > 1:
            listed = "\n".join(f"  - {path}" for path in client_candidates)
            raise SystemExit(
                "GOOGLE_OAUTH_CLIENT_PATH가 설정되어 있지 않고, candidate OAuth client 파일이 1개가 아닙니다.\n"
                "다음 중 하나를 선택해 --client-path로 지정하세요.\n"
                f"{listed}\n\n"
                "예: python refresh_google_token.py --client-path configuration/<your_client_secret>.json"
            )
        else:
            raise SystemExit(
                "GOOGLE_OAUTH_CLIENT_PATH가 설정되어 있지 않습니다. "
                "configuration/client_secret.json 위치를 확인 후 configuration/.env에 GOOGLE_OAUTH_CLIENT_PATH를 입력하거나 "
                "--client-path로 직접 지정하세요."
            )

    base_dir = Path(__file__).resolve().parent
    client_path = _resolve_path(base_dir, args.client_path)
    token_path = _resolve_path(base_dir, args.token_path)

    if not client_path.exists():
        raise SystemExit(f"OAuth client 파일을 찾을 수 없습니다: {client_path}")

    client_type = _describe_client_secret(client_path)
    if client_type == "web":
        print("참고: 현재 client_secret.json은 OAuth Client Type = web 입니다.")
        print("web 타입은 Google Cloud Console에서 redirect URI를 정확히 등록해야 합니다.")
        print("예: http://localhost:8080/")
        print("포트를 고정하려면 --port 8080 으로 실행하세요.")
        if args.port == 0:
            print("현재 --port=0(임시 포트)로 실행 중입니다. web 타입에서는 mismatch가 자주 발생할 수 있습니다.")

    token_path.parent.mkdir(parents=True, exist_ok=True)

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_path),
        scopes=_build_scopes(),
    )

    if args.console:
        credentials = flow.run_local_server(
            host=args.host,
            port=args.port,
            open_browser=False,
        )
    else:
        credentials = flow.run_local_server(
            host=args.host,
            port=args.port,
        )

    token_path.write_text(credentials.to_json(), encoding="utf-8")
    print(f"토큰이 저장되었습니다: {token_path}")


if __name__ == "__main__":
    main()
