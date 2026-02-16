import asyncio
import logging
import re
from typing import Optional

from interfaces.translator import TranslatorAdapter

logger = logging.getLogger("scraper-novela")


def _parse_retry_delay(error_msg: str) -> Optional[float]:
    """Extract retry delay from Gemini 429 error message."""
    m = re.search(r"retry in ([\d.]+)s", error_msg, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"retryDelay.*?'(\d+)s'", error_msg)
    if m:
        return float(m.group(1))
    return None


class GeminiAdapter(TranslatorAdapter):
    def __init__(self, model: str = "gemini-2.0-flash", api_key: Optional[str] = None) -> None:
        from google import genai

        if not api_key:
            raise RuntimeError(
                "Falta GEMINI_API_KEY. Exporta la variable de entorno o agrégala a tu archivo .env."
            )
        self._model = model
        self._client = genai.Client(api_key=api_key)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def adapter_name(self) -> str:
        return "gemini"

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
        from google.genai import types

        loop = asyncio.get_running_loop()
        last_err: Optional[Exception] = None

        for attempt in range(1, retries + 1):
            try:
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
            except Exception as e:
                last_err = e
                err_msg = str(e)
                is_rate_limit = "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg

                if attempt < retries:
                    if is_rate_limit:
                        api_delay = _parse_retry_delay(err_msg)
                        wait = max(api_delay or 30.0, 10.0)
                    else:
                        wait = backoff ** (attempt - 1)

                    logger.warning(
                        "Gemini intento %d/%d falló%s: %s — reintentando en %.0fs",
                        attempt,
                        retries,
                        " (rate limit)" if is_rate_limit else "",
                        err_msg[:120],
                        wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    if is_rate_limit:
                        raise RuntimeError(
                            f"Gemini rate limit después de {retries} intentos. "
                            f"Tu API key puede necesitar unos minutos para activarse, "
                            f"o necesitas habilitar billing en https://aistudio.google.com/apikey"
                        )

        raise RuntimeError(
            f"Gemini falló después de {retries} intentos: {last_err}"
        )
