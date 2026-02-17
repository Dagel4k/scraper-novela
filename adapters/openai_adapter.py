import asyncio
import logging
from typing import Optional

from interfaces.translator import TranslatorAdapter

logger = logging.getLogger("scraper-novela")


class OpenAIAdapter(TranslatorAdapter):
    def __init__(self, model: str = "gpt-4o-mini", api_key: Optional[str] = None) -> None:
        from openai import OpenAI

        if not api_key:
            raise RuntimeError(
                "Falta OPENAI_API_KEY. Exporta la variable de entorno o agrégala a tu archivo .env."
            )
        self._model = model
        self._client = OpenAI(api_key=api_key)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def adapter_name(self) -> str:
        return "openai"

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
                # Use asyncio.to_thread for the blocking OpenAI call if needed, 
                # but openai>=1.0 has a sync client that works well in threads.
                # However, for consistency with Gemini adapter, we use loop.run_in_executor
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
            except Exception as e:
                last_err = e
                err_msg = str(e)
                is_rate_limit = "429" in err_msg or "rate_limit" in err_msg.lower()

                if attempt < retries:
                    wait = backoff ** (attempt - 1)
                    if is_rate_limit:
                        wait = max(wait, 10.0)

                    logger.warning(
                        "OpenAI intento %d/%d falló%s: %s — reintentando en %.0fs",
                        attempt,
                        retries,
                        " (rate limit)" if is_rate_limit else "",
                        err_msg[:120],
                        wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    break

        raise RuntimeError(
            f"OpenAI falló después de {retries} intentos: {last_err}"
        )
