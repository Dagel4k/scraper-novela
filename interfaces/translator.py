import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from core.domain import ChapterContent, Glossary, TranslationResult
from core.text_processor import TextProcessor
from utils.logger import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


class TranslatorAdapter(ABC):
    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @property
    @abstractmethod
    def adapter_name(self) -> str: ...

    @abstractmethod
    async def translate_chunk(
        self,
        system_prompt: str,
        user_text: str,
        *,
        temperature: float = 0.2,
        timeout: float = 120.0,
    ) -> str: ...


class PromptBuilder:
    def __init__(self, settings: dict, glossary: Glossary, source_lang: str = "en") -> None:
        self._settings = settings
        self._glossary = glossary
        self._source_lang = source_lang

    def build_system_prompt(self, title_en: str) -> str:
        prompt_cfg = self._settings.get("prompt", {})
        parts: List[str] = []

        preamble_key = "preamble_cn" if self._source_lang == "cn" else "preamble"
        preamble = prompt_cfg.get(preamble_key, prompt_cfg.get("preamble", ""))
        if preamble:
            parts.append(preamble.strip())

        rules = prompt_cfg.get("translation_rules", [])
        for rule in rules:
            parts.append(f"- {rule}")

        if self._glossary.never_translate:
            parts.append("")
            parts.append(
                "Lista de términos que NO se traducen (mantener exactamente): "
                + ", ".join(sorted(set(self._glossary.never_translate)))
            )
        if self._glossary.translations:
            pairs = "; ".join(
                f"{k} => {v}" for k, v in self._glossary.translations.items()
            )
            parts.append("")
            parts.append("Glosario de traducciones forzadas: " + pairs)

        parts.append(f"\nTítulo del capítulo (referencia): {title_en}")
        if self._source_lang == "cn":
            parts.append("Nota: El texto de entrada está en CHINO. Tradúcelo directamente al ESPAÑOL.")
        return "\n".join(parts)

    def build_user_message(self, text: str) -> str:
        prompt_cfg = self._settings.get("prompt", {})
        template = prompt_cfg.get(
            "user_template",
            "Traduce fielmente el siguiente fragmento. Responde EN ESPAÑOL. "
            "Devuelve SOLO el texto traducido, sin notas ni etiquetas.\n\n{text}",
        )
        return template.format(text=text)


class TranslationPipeline:
    def __init__(
        self,
        adapter: TranslatorAdapter,
        text_processor: TextProcessor,
        prompt_builder: PromptBuilder,
        chunk_chars: int = 7000,
        max_concurrent: int = 3,
        request_delay: float = 0,
        temperature: float = 0.2,
        timeout: float = 120.0,
    ) -> None:
        self.adapter = adapter
        self.tp = text_processor
        self.pb = prompt_builder
        self.chunk_chars = chunk_chars
        self.max_concurrent = max_concurrent
        self.request_delay = request_delay
        self.temperature = temperature
        self.timeout = timeout

    async def translate_chapter(
        self, chapter: ChapterContent
    ) -> TranslationResult:
        t0 = time.time()
        system_prompt = self.pb.build_system_prompt(chapter.title)

        # Translate title
        title_src = self.tp.prepare_text(chapter.title)
        logger.info("Translating title...")
        title_raw = await self.adapter.translate_chunk(
            system_prompt,
            self.pb.build_user_message(title_src),
            temperature=self.temperature,
            timeout=self.timeout,
        )
        title_es = self.tp.finalize_text(title_raw)
        
        # Pause after title translation before starting chunks
        if self.request_delay > 0:
            logger.debug("Waiting %.1fs (rate-limit delay after title)...", self.request_delay)
            await asyncio.sleep(self.request_delay)

        # Chunk and translate paragraphs
        chunks = TextProcessor.chunk_paragraphs(
            chapter.paragraphs, max_chars=self.chunk_chars
        )

        if not chunks:
            logger.info(
                "Cap %d traducido en %.1fs", chapter.number, time.time() - t0
            )
            return TranslationResult(
                number=chapter.number,
                title_en=chapter.title,
                title_es=title_es,
                paragraphs_es=[],
                model=self.adapter.model_name,
                adapter_name=self.adapter.adapter_name,
            )

        logger.info(
            "Translating %d chunks sequentially...",
            len(chunks),
        )

        translated_paragraphs: List[str] = []

        for idx, chunk in enumerate(chunks):
            text = "\n\n".join(chunk)
            text_src = self.tp.prepare_text(text)
            tc0 = time.time()
            logger.info(
                "  Chunk %d/%d (%d chars)...", idx + 1, len(chunks), len(text)
            )
            
            out = await self.adapter.translate_chunk(
                system_prompt,
                self.pb.build_user_message(text_src),
                temperature=self.temperature,
                timeout=self.timeout,
            )
            
            out = self.tp.finalize_text(out)
            out_pars = [p.strip() for p in out.split("\n\n") if p.strip()]
            translated_paragraphs.extend(out_pars)
            
            logger.info(
                "  Chunk %d/%d done in %.1fs",
                idx + 1,
                len(chunks),
                time.time() - tc0,
            )
            
            # Pause between chunks if delay is set
            if self.request_delay > 0 and idx < len(chunks) - 1:
                logger.debug("Waiting %.1fs (rate-limit delay)...", self.request_delay)
                await asyncio.sleep(self.request_delay)

        total = time.time() - t0
        logger.info(
            "Chapter %d translated in %.1fs (%.1f min)",
            chapter.number,
            total,
            total / 60,
        )

        return TranslationResult(
            number=chapter.number,
            title_en=chapter.title,
            title_es=title_es,
            paragraphs_es=translated_paragraphs,
            model=self.adapter.model_name,
            adapter_name=self.adapter.adapter_name,
        )
