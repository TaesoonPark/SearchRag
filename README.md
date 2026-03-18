# Telegram LangGraph News Agent

키워드/뉴스 단신을 입력받아 검색 → 충분성 검증 → 요약 작성 후 텔레그램으로 응답하는 Python 애플리케이션입니다.

## 구성
- LangGraph 기반 다중 에이전트 3단계 파이프라인
- SearXNG로 검색
- 기존 Brave 검색 구현은 `brave_search.py`로 분리
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
cp .env.example .env
```
`.env`에 아래 값을 채우세요.
- `LLM_BASE_URL` (vLLM OpenAI-compatible 서버 URL)
- `LLM_MODEL` (예: gpt-oss-120b)
- `TELEGRAM_BOT_TOKEN`
- `SEARXNG_BASE_URL`

SearXNG 엔진은 인스턴스에서 활성화된 엔진 이름과 일치해야 합니다.

## 실행
```bash
python main.py
```

## 사용
텔레그램 봇에게 키워드 또는 뉴스 단신을 전송하면 요약이 응답됩니다.
