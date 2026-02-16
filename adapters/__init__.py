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

    raise ValueError(
        f"Adapter desconocido: '{name}'. Disponibles: gemini"
    )
