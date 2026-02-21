import os
from typing import Optional

from interfaces.translator import TranslatorAdapter


def get_adapter(name: str, config: dict) -> TranslatorAdapter:
    if name == "gemini":
        from adapters.gemini_adapter import GeminiAdapter

        gemini_cfg = config.get("gemini", {})
        api_key = os.environ.get(gemini_cfg.get("api_key_env", "GEMINI_API_KEY"))
        model = gemini_cfg.get("model", "gemini-2.0-flash")
        return GeminiAdapter(model=model, api_key=api_key)

    if name == "openai":
        from adapters.openai_adapter import OpenAIAdapter

        openai_cfg = config.get("openai", {})
        api_key = os.environ.get(openai_cfg.get("api_key_env", "OPENAI_API_KEY"))
        model = openai_cfg.get("model", "gpt-4o-mini")
        return OpenAIAdapter(model=model, api_key=api_key)

    raise ValueError(
        f"Unknown adapter: '{name}'. Available: gemini, openai"
    )
