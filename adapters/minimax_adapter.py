import asyncio
import logging
import os
from typing import Optional

import requests

from adapters.base import BaseAdapter
from utils.logger import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

class MinimaxAdapter(BaseAdapter):
    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._model = model or os.getenv("ANTHROPIC_MODEL", "MiniMax-M2.7")
        self._base_url = base_url or os.getenv("ANTHROPIC_BASE_URL", "https://api.minimax.io/anthropic")
        
        if not self._api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is missing for Minimax adapter.")
            
        # Ensure the endpoint targets the Anthropic messages API
        if not self._base_url.rstrip("/").endswith("v1/messages"):
            # Minimax endpoint for anthropic layer typically ends in /v1/messages
            if self._base_url.endswith("/anthropic"):
                self._base_url = self._base_url.rstrip("/") + "/v1/messages"
            elif not self._base_url.endswith("/messages"):
                self._base_url = self._base_url.rstrip("/") + "/v1/messages"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def adapter_name(self) -> str:
        return "minimax"

    def _call_single_pass_sync(self, system_prompt: str, user_text: str, temperature: float, timeout: float) -> str:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        payload = {
            "model": self._model,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_text}],
            "max_tokens": 4096,
            "temperature": temperature
        }
        # Minimax M2.7 is a reasoning model, increase the timeout.
        actual_timeout = max(timeout, 300.0)
        resp = requests.post(self._base_url, headers=headers, json=payload, timeout=actual_timeout)
        resp.raise_for_status()
        data = resp.json()
        
        # Minimax M2.7 may return 'thinking' blocks before 'text' blocks.
        for block in data.get("content", []):
            if block.get("type") == "text":
                return block.get("text", "").strip()
                
        # Fallback if we cannot find a text block
        return data.get("content", [{}])[-1].get("text", "").strip()

    async def _call_api(
        self,
        system_prompt: str,
        user_text: str,
        *,
        temperature: float,
        timeout: float,
    ) -> str:
        loop = asyncio.get_running_loop()
        
        # Pasada 1: Traducción pura
        logger.info("[Minimax] Pass 1: Raw Translation")
        pass1_out = await loop.run_in_executor(
            None, lambda: self._call_single_pass_sync(system_prompt, user_text, temperature, timeout)
        )
        
        # Pasada 2: Sintaxis y Gramática
        logger.info("[Minimax] Pass 2: Syntax and Grammar")
        sys2 = (
            "Eres un experto corrector de textos de novelas. "
            "Corrige cualquier error gramatical o de sintaxis en el texto proporcionado, "
            "sin eliminar información y conservando el tono. "
            "Devuelve SOLO el texto corregido, sin notas adicionales."
        )
        prompt2 = f"Texto a corregir:\n\n{pass1_out}"
        pass2_out = await loop.run_in_executor(
            None, lambda: self._call_single_pass_sync(sys2, prompt2, temperature, timeout)
        )
        
        # Pasada 3: Redacción y Estilo
        logger.info("[Minimax] Pass 3: Polishing and Style")
        sys3 = (
            "Eres un editor profesional de novelas. "
            "Mejora la redacción y fluidez del texto aportado para que suene natural, dinámico "
            "y de alta calidad literaria en español de LATAM. "
            "Reestructura oraciones torpes si es necesario, pero mantén el significado íntegro. "
            "Devuelve SOLO el texto pulido final sin comentarios adicionales."
        )
        prompt3 = f"Texto a mejorar:\n\n{pass2_out}"
        pass3_out = await loop.run_in_executor(
            None, lambda: self._call_single_pass_sync(sys3, prompt3, temperature, timeout)
        )
        
        return pass3_out
