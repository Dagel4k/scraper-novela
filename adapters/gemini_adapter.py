import asyncio
import logging
import re
from typing import Optional

from adapters.base import BaseAdapter
from utils.logger import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


def _parse_retry_delay(error_msg: str) -> Optional[float]:
    """Extract retry delay from Gemini 429 error message."""
    m = re.search(r"retry in ([\d.]+)s", error_msg, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"retryDelay.*?'(\d+)s'", error_msg)
    if m:
        return float(m.group(1))
    return None


class GeminiAdapter(BaseAdapter):
    def __init__(self, model: str = "gemini-2.0-flash", api_key: Optional[str] = None) -> None:
        from google import genai

        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is missing. Set the environment variable or add it to your .env file."
            )
        self._model = model
        self._client = genai.Client(api_key=api_key)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def adapter_name(self) -> str:
        return "gemini"

    def _backoff_seconds(self, attempt: int, error: Exception, backoff: float) -> float:
        """Use Gemini's suggested retry delay for rate-limit errors."""
        if self._is_rate_limit(error):
            api_delay = _parse_retry_delay(str(error))
            return max(api_delay or 30.0, 10.0)
        return backoff ** (attempt - 1)

    async def _call_api(
        self,
        system_prompt: str,
        user_text: str,
        *,
        temperature: float,
        timeout: float,
    ) -> str:
        from google.genai import types

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._client.models.generate_content(
                model=self._model,
                contents=user_text,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=temperature,
                ),
            ),
        )
        return (result.text or "").strip()
