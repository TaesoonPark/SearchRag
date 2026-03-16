# Telegram LLM Agent

Minimal Telegram bot that routes messages to a local LLM endpoint and maintains short per-chat memory.

## Setup

1. Create a Telegram bot and get a token (BotFather).
2. Edit `config.json` and run.

```bash
python3 bot.py
```

## Webhook mode

Set `bot_mode` to `webhook` and provide `webhook_url` in `config.json`. The bot will call Telegram `setWebhook` on startup and start an HTTP server on `webhook_host:webhook_port` serving `webhook_path`.

## News RAG mode

`agent_mode` is set to `news_rag` by default. For each incoming message, the bot:

1. Uses the LLM to generate search queries.
2. Searches multiple sources using `search_providers`.
3. Iterates until it has at least `search_min_docs` and the LLM judges coverage >= `search_min_coverage`.
4. Produces a Korean summary with citations and a Sources list.

### Search providers

`search_providers` is a list. Supported types:

- `google_cse`: Uses Google Custom Search API. Requires `api_key` and `cx`. You can force site coverage using `site_filters`.
- `reddit_json`: Uses Reddit public search JSON.
- `searxng`: Uses a SearXNG instance (`base_url`).

For Google/Yahoo Finance/DCInside/Reddit coverage, configure `google_cse` with `site_filters` that include those domains, and keep `reddit_json` enabled for direct Reddit coverage.
