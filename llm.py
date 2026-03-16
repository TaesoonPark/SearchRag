from __future__ import annotations

from langchain_openai import ChatOpenAI

from config import Config


def build_llm(cfg: Config, temperature: float = 0.2) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=cfg.llm_base_url,
        api_key=cfg.llm_api_key,
        model=cfg.llm_model,
        temperature=temperature,
    )
