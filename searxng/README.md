# SearXNG Docker

이 폴더는 `SearchRag`에서 사용할 기본 SearXNG 로컬 구동 설정입니다.

## 실행

```bash
cd searxng
docker compose up -d
```

기본 주소:

```text
http://localhost:8001
```

프로젝트 `.env` 예시:

```env
SEARXNG_BASE_URL=http://localhost:8001
SEARXNG_ENGINES=
```

## 중지

```bash
cd searxng
docker compose down
```

## 참고

- `settings.yml`의 `secret_key`는 실제 사용 전에 변경하는 편이 좋습니다.
- 기본적으로 JSON 응답을 허용하도록 설정되어 있어 현재 프로젝트의 검색 코드와 맞습니다.
- `brave` 엔진은 비활성화해 두었습니다.
