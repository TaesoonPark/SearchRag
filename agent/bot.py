#!/usr/bin/env python3
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer


TELEGRAM_TOKEN = ""
TELEGRAM_API_BASE = ""

LLM_BASE_URL = ""
LLM_CHAT_PATH = ""
LLM_MODEL = ""
LLM_TEMPERATURE = 0.4
LLM_MAX_TOKENS = 800

SYSTEM_PROMPT = ""

MEMORY_TURNS = 10
POLL_TIMEOUT = 25
MEMORY_FILE = ""

BOT_MODE = "polling"
WEBHOOK_HOST = "0.0.0.0"
WEBHOOK_PORT = 8080
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = ""

AGENT_MODE = "news_rag"
SEARCH_MIN_DOCS = 4
SEARCH_MIN_COVERAGE = 0.8
SEARCH_MAX_ROUNDS = 3
SEARCH_PROVIDERS = []


def _http_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get(url, params):
    qs = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url}?{qs}", timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def telegram_get_updates(offset):
    return _http_get(
        f"{TELEGRAM_API_BASE}/getUpdates",
        {"timeout": POLL_TIMEOUT, "offset": offset},
    )


def telegram_send_message(chat_id, text, reply_to_message_id=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    return _http_json(f"{TELEGRAM_API_BASE}/sendMessage", payload)


def llm_chat(messages):
    url = f"{LLM_BASE_URL}{LLM_CHAT_PATH}"
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": LLM_TEMPERATURE,
        "max_tokens": LLM_MAX_TOKENS,
        "stream": False,
    }
    return _http_json(url, payload)


def extract_reply(resp):
    if not resp:
        return "No response."
    if "choices" in resp and resp["choices"]:
        choice = resp["choices"][0]
        if "message" in choice and "content" in choice["message"]:
            return choice["message"]["content"].strip()
        if "text" in choice:
            return choice["text"].strip()
    if "response" in resp and isinstance(resp["response"], str):
        return resp["response"].strip()
    return "No response."


def _load_config():
    base_dir = os.path.dirname(__file__)
    path = os.path.join(base_dir, "config.json")
    if not os.path.exists(path):
        raise SystemExit(f"Missing config file: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SystemExit("Invalid config format. Expected a JSON object.")
    return data


def _apply_config(cfg):
    global TELEGRAM_TOKEN, TELEGRAM_API_BASE
    global LLM_BASE_URL, LLM_CHAT_PATH, LLM_MODEL, LLM_TEMPERATURE, LLM_MAX_TOKENS
    global SYSTEM_PROMPT, MEMORY_TURNS, POLL_TIMEOUT, MEMORY_FILE
    global BOT_MODE, WEBHOOK_HOST, WEBHOOK_PORT, WEBHOOK_PATH, WEBHOOK_URL
    global AGENT_MODE, SEARCH_MIN_DOCS, SEARCH_MIN_COVERAGE, SEARCH_MAX_ROUNDS, SEARCH_PROVIDERS

    TELEGRAM_TOKEN = str(cfg.get("telegram_token", "")).strip()
    if not TELEGRAM_TOKEN:
        raise SystemExit("Missing telegram_token in config.json.")
    TELEGRAM_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

    LLM_BASE_URL = str(cfg.get("llm_url", "http://172.30.1.93:8000")).rstrip("/")
    LLM_CHAT_PATH = str(cfg.get("llm_chat_path", "/v1/chat/completions"))
    LLM_MODEL = str(cfg.get("llm_model", "gpt-oss-120b"))
    LLM_TEMPERATURE = float(cfg.get("llm_temperature", 0.4))
    LLM_MAX_TOKENS = int(cfg.get("llm_max_tokens", 800))

    SYSTEM_PROMPT = str(
        cfg.get(
            "system_prompt",
            "You are a concise, capable assistant for Telegram. Provide direct, helpful answers.",
        )
    )

    MEMORY_TURNS = int(cfg.get("memory_turns", 10))
    POLL_TIMEOUT = int(cfg.get("poll_timeout", 25))
    MEMORY_FILE = str(
        cfg.get("memory_file", os.path.join(os.path.dirname(__file__), "memory.json"))
    )

    BOT_MODE = str(cfg.get("bot_mode", "polling")).lower()
    WEBHOOK_HOST = str(cfg.get("webhook_host", "0.0.0.0"))
    WEBHOOK_PORT = int(cfg.get("webhook_port", 8080))
    WEBHOOK_PATH = str(cfg.get("webhook_path", "/webhook"))
    WEBHOOK_URL = str(cfg.get("webhook_url", "")).strip()

    AGENT_MODE = str(cfg.get("agent_mode", "news_rag")).lower()
    SEARCH_MIN_DOCS = int(cfg.get("search_min_docs", 4))
    SEARCH_MIN_COVERAGE = float(cfg.get("search_min_coverage", 0.8))
    SEARCH_MAX_ROUNDS = int(cfg.get("search_max_rounds", 3))
    SEARCH_PROVIDERS = cfg.get("search_providers", [])
    if not isinstance(SEARCH_PROVIDERS, list):
        raise SystemExit("search_providers must be a list in config.json.")


def _load_memory():
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_memory(memory):
    tmp_path = f"{MEMORY_FILE}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=True, indent=2)
    os.replace(tmp_path, MEMORY_FILE)


def _llm_json(system_prompt, user_prompt):
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    resp = llm_chat(messages)
    text = extract_reply(resp)
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return None
    return None


def _search_google_cse(query, provider):
    api_key = str(provider.get("api_key", "")).strip()
    cx = str(provider.get("cx", "")).strip()
    if not api_key or not cx:
        return []

    site_filters = provider.get("site_filters", [])
    mode = str(provider.get("site_filters_mode", "or")).lower()
    q = query
    if site_filters:
        if mode == "each":
            pass
        else:
            clause = " OR ".join([f"site:{d}" for d in site_filters])
            q = f"{query} ({clause})"

    per_query = int(provider.get("per_query", 5))
    params = {
        "key": api_key,
        "cx": cx,
        "q": q,
        "num": per_query,
    }
    url = "https://www.googleapis.com/customsearch/v1"
    try:
        data = _http_get(url, params)
    except Exception:
        return []

    items = data.get("items", []) if isinstance(data, dict) else []
    docs = []
    for item in items:
        docs.append(
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "source": "Google CSE",
            }
        )
    if site_filters and mode == "each":
        for domain in site_filters:
            qd = f"{query} site:{domain}"
            params["q"] = qd
            try:
                data = _http_get(url, params)
            except Exception:
                continue
            items = data.get("items", []) if isinstance(data, dict) else []
            for item in items:
                docs.append(
                    {
                        "title": item.get("title", ""),
                        "url": item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                        "source": f"Google CSE ({domain})",
                    }
                )
    return docs


def _search_reddit_json(query, provider):
    per_query = int(provider.get("per_query", 5))
    params = {"q": query, "sort": "relevance", "limit": per_query}
    url = "https://www.reddit.com/search.json"
    headers = {"User-Agent": "telegram-news-agent/1.0"}
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []

    docs = []
    children = data.get("data", {}).get("children", [])
    for child in children:
        post = child.get("data", {})
        permalink = post.get("permalink", "")
        url_full = f"https://www.reddit.com{permalink}" if permalink else post.get("url", "")
        docs.append(
            {
                "title": post.get("title", ""),
                "url": url_full,
                "snippet": post.get("selftext", "")[:300],
                "source": "Reddit",
            }
        )
    return docs


def _search_searxng(query, provider):
    base_url = str(provider.get("base_url", "")).rstrip("/")
    if not base_url:
        return []
    per_query = int(provider.get("per_query", 5))
    params = {"q": query, "format": "json", "safesearch": 1, "count": per_query}
    url = f"{base_url}/search"
    try:
        data = _http_get(url, params)
    except Exception:
        return []
    results = data.get("results", []) if isinstance(data, dict) else []
    docs = []
    for item in results:
        docs.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
                "source": "SearXNG",
            }
        )
    return docs


def _search_all(queries):
    docs = []
    for query in queries:
        for provider in SEARCH_PROVIDERS:
            ptype = str(provider.get("type", "")).lower()
            if ptype == "google_cse":
                docs.extend(_search_google_cse(query, provider))
            elif ptype == "reddit_json":
                docs.extend(_search_reddit_json(query, provider))
            elif ptype == "searxng":
                docs.extend(_search_searxng(query, provider))
    # dedupe by url
    seen = set()
    deduped = []
    for d in docs:
        url = d.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(d)
    return deduped


def _dedupe_docs(docs):
    seen = set()
    out = []
    for d in docs:
        url = d.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(d)
    return out


def _plan_queries(brief):
    system = "You are a research planner. Return JSON only."
    user = (
        "Given this news brief, create 3-6 search queries and key keywords.\n\n"
        f"Brief: {brief}\n\n"
        'Return JSON: {"queries":[...], "keywords":[...], "topic":"..."}'
    )
    data = _llm_json(system, user) or {}
    queries = data.get("queries", [])
    if not isinstance(queries, list) or not queries:
        queries = [brief]
    keywords = data.get("keywords", [])
    if not isinstance(keywords, list):
        keywords = []
    return queries, keywords


def _assess_coverage(brief, keywords, docs):
    system = "You judge coverage. Return JSON only."
    doc_lines = []
    for i, d in enumerate(docs[:12], start=1):
        doc_lines.append(
            f"[{i}] {d.get('title','')} | {d.get('source','')} | {d.get('url','')} | {d.get('snippet','')}"
        )
    user = (
        "Given a brief and candidate documents, decide if coverage is sufficient.\n"
        f"Brief: {brief}\n"
        f"Keywords: {keywords}\n"
        "Docs:\n" + "\n".join(doc_lines) + "\n\n"
        'Return JSON: {"enough":true/false,"coverage":0-1,"missing":[...],'
        '"followup_queries":[...],"reason":"..."}'
    )
    data = _llm_json(system, user) or {}
    return data


def _final_answer(brief, docs):
    doc_lines = []
    for i, d in enumerate(docs[:12], start=1):
        doc_lines.append(
            f"[{i}] {d.get('title','')} | {d.get('source','')} | {d.get('url','')} | {d.get('snippet','')}"
        )
    system = "You are a Korean news summarizer. Use citations."
    user = (
        "Summarize the brief using the documents below. "
        "Provide a short summary paragraph, then 3-5 bullet points. "
        "Use citations like [1] next to claims. End with a Sources list using the same numbers.\n\n"
        f"Brief: {brief}\n\nDocuments:\n" + "\n".join(doc_lines)
    )
    resp = llm_chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    return extract_reply(resp)


def run_news_rag(brief):
    if not SEARCH_PROVIDERS:
        return "search_providers is empty in config.json."

    queries, keywords = _plan_queries(brief)
    docs = []

    for _ in range(SEARCH_MAX_ROUNDS):
        new_docs = _search_all(queries)
        docs = _dedupe_docs(docs + new_docs)
        if len(docs) < SEARCH_MIN_DOCS:
            queries = [brief] + keywords if keywords else [brief]
            continue

        assessment = _assess_coverage(brief, keywords, docs)
        enough = bool(assessment.get("enough", False))
        coverage = float(assessment.get("coverage", 0.0))
        followups = assessment.get("followup_queries", [])

        if enough and coverage >= SEARCH_MIN_COVERAGE:
            break
        if isinstance(followups, list) and followups:
            queries = followups
            continue
        break

    if not docs:
        return "No documents found."
    return _final_answer(brief, docs)


def _handle_message(memory, message):
    chat_id = message["chat"]["id"]
    msg_id = message.get("message_id")
    text = (message.get("text") or "").strip()
    if not text:
        return

    if text in ("/start", "/help"):
        telegram_send_message(
            chat_id,
            "Send a message and I will answer using the local LLM.",
            reply_to_message_id=msg_id,
        )
        return

    if AGENT_MODE == "news_rag":
        reply = run_news_rag(text)
        telegram_send_message(chat_id, reply, reply_to_message_id=msg_id)
        return

    history = memory.get(str(chat_id), [])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    messages.append({"role": "user", "content": text})

    try:
        llm_resp = llm_chat(messages)
        reply = extract_reply(llm_resp)
    except urllib.error.URLError:
        reply = "LLM backend is unreachable."

    telegram_send_message(chat_id, reply, reply_to_message_id=msg_id)

    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": reply})
    memory[str(chat_id)] = history[-(MEMORY_TURNS * 2) :]
    _save_memory(memory)


def _set_webhook():
    if not WEBHOOK_URL:
        raise SystemExit("Missing webhook_url in config.json for webhook mode.")
    payload = {"url": WEBHOOK_URL}
    return _http_json(f"{TELEGRAM_API_BASE}/setWebhook", payload)


class _WebhookHandler(BaseHTTPRequestHandler):
    memory = None

    def _send(self, status, body=b""):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self):
        if self.path != WEBHOOK_PATH:
            self._send(404, b"{}")
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            update = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, b"{}")
            return

        message = update.get("message") or update.get("edited_message")
        if message:
            _handle_message(self.memory, message)

        self._send(200, b"{}")

    def log_message(self, format, *args):
        return


def run_polling():
    memory = _load_memory()
    offset = 0

    while True:
        try:
            updates = telegram_get_updates(offset)
            if not updates.get("ok"):
                time.sleep(1)
                continue

            for update in updates.get("result", []):
                offset = max(offset, update.get("update_id", 0) + 1)
                message = update.get("message") or update.get("edited_message")
                if message:
                    _handle_message(memory, message)

        except urllib.error.URLError:
            time.sleep(2)
        except Exception:
            time.sleep(1)


def run_webhook():
    _set_webhook()
    memory = _load_memory()
    _WebhookHandler.memory = memory
    server = HTTPServer((WEBHOOK_HOST, WEBHOOK_PORT), _WebhookHandler)
    server.serve_forever()


def main():
    cfg = _load_config()
    _apply_config(cfg)

    if BOT_MODE == "webhook":
        run_webhook()
    else:
        run_polling()


if __name__ == "__main__":
    main()
