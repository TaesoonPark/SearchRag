# Telegram LangGraph News Agent

키워드/뉴스 단신을 입력받아 검색 → 충분성 검증 → 요약 작성 후 텔레그램으로 응답하는 Python 애플리케이션입니다.

## 구성
- LangGraph 기반 다중 에이전트 3단계 파이프라인
- SearXNG + 네이버 검색 API(webkr)로 검색
- BeautifulSoup 기반 HTML 정규화/요약
- Telegram Bot으로 대화

## 설치
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 설정
```bash
mkdir -p configuration
cp configuration/.env.example configuration/.env
```
`configuration/.env`에 아래 값을 채우세요.
- `LLM_BASE_URL` (vLLM OpenAI-compatible 서버 URL)
- `LLM_MODEL` (예: gpt-oss-120b)
- `LLM_TIMEOUT`
- `GRAPH_TIMEOUT` (기본 600초, 0으로 두면 제한 없음)
- `TELEGRAM_BOT_TOKEN`
- `SEARXNG_BASE_URL`
- `SEARXNG_ENABLED` (on/off: true/false)
- `NAVER_CLIENT_ID` (네이버 검색 API Client ID)
- `NAVER_CLIENT_SECRET` (네이버 검색 API Client Secret)
- `NAVER_SEARCH_TYPE` (예: `webkr`, `news`)
- `NAVER_SEARCH_SORT` (`sim` 또는 `date`)
- `NAVER_SEARCH_DISPLAY` (최대 100)
- `NAVER_SEARCH_START` (페이징 시작 오프셋)
- `RUN_IN_BACKGROUND` (실행 모드, 기본값 false)

SearXNG 엔진은 인스턴스에서 활성화된 엔진 이름과 일치해야 합니다.
네이버 검색 API는 `NAVER_SEARCH_TYPE`, `NAVER_SEARCH_SORT`, `NAVER_SEARCH_DISPLAY`, `NAVER_SEARCH_START`로 호출 방식을 제어합니다.
기본은 `webkr + date`이며, 뉴스 결과 중심으로 보고 싶다면 `NAVER_SEARCH_TYPE=news`로 변경하세요.
네이버 검색 API는 `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`가 모두 입력된 경우에만 동작합니다.

## 실행
```bash
python main.py
```

## 사용
텔레그램 봇에게 키워드 또는 뉴스 단신을 전송하면 요약이 응답됩니다.
