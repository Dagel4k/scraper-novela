"""Abstract base adapter with exponential-backoff retry logic."""

import asyncio
import logging
from abc import abstractmethod
from typing import Optional

from interfaces.translator import TranslatorAdapter
from utils.logger import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


class BaseAdapter(TranslatorAdapter):
    """Base adapter that implements the retry/backoff loop.

    Concrete adapters only need to implement ``_call_api()`` and optionally
    override ``_backoff_seconds()`` for API-specific wait strategies.
    """

    @abstractmethod
    async def _call_api(
        self,
        system_prompt: str,
        user_text: str,
        *,
        temperature: float,
        timeout: float,
    ) -> str:
        """Single API call — no retry logic."""
        ...

    def _is_rate_limit(self, error: Exception) -> bool:
        """Return True if the error is a rate-limit/quota error."""
        msg = str(error)
        return "429" in msg or "RESOURCE_EXHAUSTED" in msg or "rate_limit" in msg.lower()

    def _backoff_seconds(self, attempt: int, error: Exception, backoff: float) -> float:
        """Compute wait time for this attempt (exponential by default)."""
        return backoff ** (attempt - 1)

    async def translate_chunk(
        self,
        system_prompt: str,
        user_text: str,
        *,
        temperature: float = 0.2,
        timeout: float = 120.0,
        retries: int = 5,
        backoff: float = 2.0,
    ) -> str:
        last_err: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                return await self._call_api(
                    system_prompt, user_text,
                    temperature=temperature, timeout=timeout,
                )
            except Exception as exc:
                last_err = exc
                rate_limited = self._is_rate_limit(exc)
                if attempt < retries:
                    wait = self._backoff_seconds(attempt, exc, backoff)
                    logger.warning(
                        "%s attempt %d/%d failed%s: %s — retrying in %.0fs",
                        self.adapter_name, attempt, retries,
                        " (rate limit)" if rate_limited else "",
                        str(exc)[:120], wait,
                    )
                    await asyncio.sleep(wait)
        raise RuntimeError(
            f"{self.adapter_name} failed after {retries} attempts: {last_err}"
        )
