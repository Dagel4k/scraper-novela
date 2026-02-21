import asyncio
import logging
from typing import Optional

from adapters.base import BaseAdapter
from utils.logger import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


class OpenAIAdapter(BaseAdapter):
    def __init__(self, model: str = "gpt-4o-mini", api_key: Optional[str] = None) -> None:
        from openai import OpenAI

        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is missing. Set the environment variable or add it to your .env file."
            )
        self._model = model
        self._client = OpenAI(api_key=api_key)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def adapter_name(self) -> str:
        return "openai"

    def _backoff_seconds(self, attempt: int, error: Exception, backoff: float) -> float:
        wait = backoff ** (attempt - 1)
        if self._is_rate_limit(error):
            return max(wait, 10.0)
        return wait

    async def _call_api(
        self,
        system_prompt: str,
        user_text: str,
        *,
        temperature: float,
        timeout: float,
    ) -> str:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                temperature=temperature,
                timeout=timeout,
            ),
        )
        return response.choices[0].message.content.strip()
