# Telegram LangGraph Assistant Bot

검색/요약, Gmail 조회·발송, Google Calendar 조회·등록, 예약 실행을 지원하는 Telegram 기반 생산성 봇입니다.

## 구성
- LangGraph 기반 멀티 에이전트 파이프라인
- SearXNG + 네이버 검색 API(webkr/news) 검색
- Gmail 조회/발송 + Calendar 조회/생성
- Telegram Bot LLM 우선 자연어 라우팅(키워드 백업 규칙 파일 포함)
- 검색 예약(일회성/반복) 스케줄러

## 설치
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 설정
```bash
mkdir -p configuration
cp configuration/.env.example configuration/.env
```
`configuration/.env`에 아래 값을 채우세요.

- 공통
  - `LLM_BASE_URL`: OpenAI 호환 엔드포인트(vLLM/호스팅 API)
  - `LLM_API_KEY`
  - `LLM_MODEL`
  - `LLM_TIMEOUT`
  - `GRAPH_TIMEOUT`
  - `TELEGRAM_BOT_TOKEN`
  - `TELEGRAM_ALLOWED_CHAT_IDS` (권장, 쉼표 구분)
  - `TELEGRAM_OPEN_ACCESS` (`true`면 모든 채팅 허용, 기본 `false`)
  - `RUN_IN_BACKGROUND` (`true`/`false`)
- SearXNG/네이버 검색
  - `SEARXNG_BASE_URL`
  - `SEARXNG_ENABLED` (`true`/`false`)
  - `SEARXNG_ENGINES`
  - `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`
  - `NAVER_SEARCH_TYPE` (`webkr`, `news` 등)
  - `NAVER_SEARCH_SORT` (`sim`, `date`)
  - `NAVER_SEARCH_DISPLAY`, `NAVER_SEARCH_START`
- Gmail / Calendar
  - `GOOGLE_SERVICE_ACCOUNT_PATH` (서비스 계정 사용 시)
  - `GOOGLE_USER_EMAIL` (서비스 계정 위임 시 선택)
  - `GOOGLE_OAUTH_CLIENT_PATH` (`configuration/client_secret.json` 추천)
  - `GOOGLE_OAUTH_TOKEN_PATH` (`configuration/token.json`)
  - `GOOGLE_CALENDAR_ID` (기본 `primary`)

> OAuth 방식은 `GOOGLE_OAUTH_CLIENT_PATH`와 `GOOGLE_OAUTH_TOKEN_PATH`로 동작합니다.
>
> 보안 기본값: `TELEGRAM_OPEN_ACCESS=false` 이므로 `TELEGRAM_ALLOWED_CHAT_IDS`를 지정하지 않으면 실행이 거부됩니다.

Google OAuth 토큰이 없거나 오래되었다면 다음으로 갱신하세요.
```bash
venv/bin/python refresh_google_token.py --client-path configuration/client_secret.json --port 8080 --console
```

`--console`은 브라우저 자동 실행 없이 로컬 콜백 방식으로 인증합니다. (web 타입일 때는 리다이렉트 URI를 사전 등록)

## 실행
```bash
python main.py
```

## 빠른 시작 가이드
- 1) 저장소 클론
```bash
git clone https://github.com/TaesoonPark/SearchRag.git
cd SearchRag
```
- 2) 가상환경 생성/패키지 설치
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
- 3) 환경 파일 생성
```bash
mkdir -p configuration
cp configuration/.env.example configuration/.env
cp configuration/client_secret.json 샘플이 있을 경우 configuration/client_secret.json
```
`configuration/.env`에서 `TELEGRAM_BOT_TOKEN` 등 필수 값만 우선 입력하고 나머지는 기본값으로 시작해도 됩니다.
- 4) OAuth 토큰 준비(구글 연동 사용 시)
```bash
venv/bin/python refresh_google_token.py --client-path configuration/client_secret.json --port 8080 --console
```
- 5) 실행
```bash
python main.py
```

## 사용(텔레그램 명령 예시)
- 검색: `검색 <텍스트>`
- 예약 등록: `예약 <HH:MM> <작업> <파라미터>`
  - 기본 예시: `예약 14:30 검색 오늘 주요 뉴스`
  - 반복 예시: `예약 매일 09:00 일정 생성 회의 | 2026-03-20 09:00 | 2026-03-20 10:00 | 회의`
- 예약 조회/삭제: `예약목록`, `예약삭제 R001`, `예약삭제 all`
- Gmail 조회: `메일`, `메일 조회`, `메일 검색 <키워드>`, `메일 5개 보기`
- Gmail 발송: `메일 보내기 <받는사람> | <제목> | <내용>`
- Calendar 조회: `일정 조회`, `일정 오늘`, `일정 내일`, `일정 <키워드>`
- Calendar 생성: `일정 생성 <제목> | <시작> | <종료> | <설명>`
- 도움말: `/help`, `/start`

## 참고
- `configuration/.env.example`을 기준으로 `configuration/.env`를 만들면 처음 사용자도 바로 시작 가능합니다.
- 배포 환경에서는 `RUN_IN_BACKGROUND=true`를 켜거나, 시스템 서비스로 관리하세요.
- 명령 키워드 백업 규칙은 `prompts/command_keyword_backup.json`에 저장되며, 라우터 LLM 실패 시에만 사용됩니다.
