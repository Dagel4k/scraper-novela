#!/usr/bin/env python3
"""
Traductor EN->ES optimizado específicamente para Ollama.
Incluye prompts mejorados y validación de calidad para modelos locales.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from urllib.parse import urljoin

DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_TRANSLATE_MODEL", "qwen2.5:7b")


@dataclass
class Glossary:
    never_translate: List[str]
    translations: Dict[str, str]
    protect_tokens: Dict[str, str]
    restore_tokens: Dict[str, str]
    post_replace: Dict[str, str]

    @staticmethod
    def load(path: Optional[Path]) -> "Glossary":
        if path is None or not path.exists():
            return Glossary([], {}, {}, {}, {})
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        root = data.get("glossary", data)
        pre = root.get("preprocess_rules", {}) or {}
        post = root.get("postprocess_rules", {}) or {}
        return Glossary(
            never_translate=list(root.get("never_translate", []) or []),
            translations=dict(root.get("translations", {}) or {}),
            protect_tokens=dict(pre.get("protect_tokens", {}) or {}),
            restore_tokens=dict(pre.get("restore_tokens", {}) or {}),
            post_replace=dict(post.get("replace", {}) or {}),
        )

    def to_json(self) -> dict:
        return {
            "glossary": {
                "never_translate": self.never_translate,
                "translations": self.translations,
                "preprocess_rules": {
                    "protect_tokens": self.protect_tokens,
                    "restore_tokens": self.restore_tokens,
                },
                "postprocess_rules": {
                    "replace": self.post_replace,
                },
            }
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(self.to_json(), f, ensure_ascii=False, indent=2)

    def ensure_placeholders(self) -> None:
        for term in self.never_translate:
            if term not in self.protect_tokens:
                placeholder = f"<PROTECT_{slugify(term)}_1>"
                i = 1
                ph = placeholder
                while ph in self.restore_tokens:
                    i += 1
                    ph = f"<PROTECT_{slugify(term)}_{i}>"
                self.protect_tokens[term] = ph
                self.restore_tokens[ph] = term
                # Clave base sin índice para restauración flexible
                base_key = f"<PROTECT_{slugify(term)}>"
                self.restore_tokens.setdefault(base_key, term)

    def merge(self, new: "Glossary") -> None:
        for t in new.never_translate:
            if t and t not in self.never_translate:
                self.never_translate.append(t)
        for k, v in new.translations.items():
            if k and (k not in self.translations):
                self.translations[k] = v
        self.ensure_placeholders()


def slugify(s: str) -> str:
    s2 = re.sub(r"[^A-Za-z0-9]+", "_", s.strip())
    return re.sub(r"_+", "_", s2).strip("_").upper()[:40]


# Usa la versión robusta compartida del pipeline híbrido
from scraper.translate_hybrid import protect_text as _protect_text_robust, restore_text as _restore_text_robust

def protect_text(text: str, glossary: Glossary) -> str:
    return _protect_text_robust(text, glossary)


def restore_text(text: str, glossary: Glossary) -> str:
    return _restore_text_robust(text, glossary)


def apply_postprocess(text: str, glossary: Glossary) -> str:
    for pat, repl in glossary.post_replace.items():
        text = re.sub(pat, repl, text)
    return text


def clean_ollama_translation(text: str, original_title: str, *, body_mode: bool = False) -> str:
    """Limpia errores comunes en traducciones de Ollama."""
    lines = text.splitlines()
    cleaned = []
    title_found = False
    
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        # Eliminar títulos duplicados o inventados
        if re.match(r'^Cap(í|i)tulo\s+\d+\s*:', line_stripped, re.IGNORECASE):
            # En modo cuerpo, elimina SIEMPRE cualquier línea de título
            if body_mode:
                continue
            # En modo título/documento completo, conserva solo el primero
            if not title_found:
                cleaned.append(line)
                title_found = True
            else:
                continue
        # Eliminar líneas que son solo "Capítulo 1:" sin contenido
        elif re.match(r'^Capítulo\s+\d+:\s*$', line_stripped, re.IGNORECASE) and i > 2:
            continue
        else:
            cleaned.append(line)
    
    text = "\n".join(cleaned)
    
    # Corregir errores comunes de nombres propios
    # "Praise" a menudo se confunde - si aparece como nombre propio, probablemente es "Liu Hong"
    # Solo corregir si está en contexto de diálogo o acción (no si es parte de una frase)
    text = re.sub(r'\bPraise\s+(asintió|dijo|respondió|preguntó|sonrió|suspiró|rió)', r'Liu Hong \1', text, flags=re.IGNORECASE)
    text = re.sub(r'\bPraise\s+(no|se|estaba|está|era|fue)', r'Liu Hong \1', text, flags=re.IGNORECASE)
    text = re.sub(r'\bPraise\s+(tomó|miró|vio|sabía)', r'Liu Hong \1', text, flags=re.IGNORECASE)
    
    # Eliminar tokens de protección mal formateados (con espacios)
    text = re.sub(r'<\s*PROTECT_([^>]+)\s*>', r'<PROTECT_\1>', text)
    
    # Corregir mezclas de idiomas comunes
    text = text.replace("No même", "Ni siquiera")
    text = text.replace("même", "siquiera")
    text = text.replace("Relyendo", "Aprovechando")
    text = text.replace("relyendo", "aprovechando")
    
    # Corregir género incorrecto en "honesta" cuando debería ser "honesto" (masculino)
    text = re.sub(r'\bhonesta\b', 'honesto', text)
    
    return text


def read_chapter(path: Path) -> Tuple[str, List[str]]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines:
        return "", []
    title = lines[0].strip()
    body = "\n".join(lines[2:]) if len(lines) >= 2 else ""
    paragraphs: List[str] = []
    buff: List[str] = []
    for ln in body.splitlines():
        if ln.strip():
            buff.append(ln.rstrip())
        else:
            if buff:
                paragraphs.append(" ".join(buff).strip())
                buff = []
    if buff:
        paragraphs.append(" ".join(buff).strip())
    return title, paragraphs


def chunk_paragraphs(paragraphs: List[str], max_chars: int = 7000) -> List[List[str]]:
    chunks: List[List[str]] = []
    cur: List[str] = []
    total = 0
    for p in paragraphs:
        p_len = len(p) + 2
        if cur and total + p_len > max_chars:
            chunks.append(cur)
            cur = [p]
            total = p_len
        else:
            cur.append(p)
            total += p_len
    if cur:
        chunks.append(cur)
    return chunks


def build_system_prompt_ollama(title_en: str, glossary: Glossary) -> str:
    """Prompt optimizado específicamente para modelos Ollama (más estricto y claro)."""
    rules: List[str] = [
        "Eres un traductor profesional del inglés al español neutro, estándar para América. Tu única tarea es traducir fielmente el texto.",
        "",
        "=== REGLAS CRÍTICAS (NO VIOLAR) ===",
        "",
        "1. TRADUCE TODO: Debes traducir CADA palabra al español. NO dejes texto en inglés, francés u otros idiomas.",
        "   Si ves 'Chapter', tradúcelo a 'Capítulo'. Si ves 'Praise', es un nombre propio y NO significa 'Elogio'.",
        "",
        "2. NO INVENTES CONTENIDO: NO agregues títulos de capítulo que no existan en el original.",
        "   NO agregues 'Capítulo 1:', 'Capítulo 2:' etc. a menos que estén en el texto original.",
        "   Solo traduce el título que aparece al inicio del texto.",
        "",
        "3. NOMBRES PROPIOS: Los nombres de PERSONAS (Su Yu, Liu Hong, Bai Feng, Zheng Yunhui, etc.)",
        "   NO se traducen. Manténlos EXACTAMENTE como están en el original.",
        "   'Liu Hong' NO es 'Praise' ni 'Elogio'. 'Liu Hong' es un nombre propio y se mantiene 'Liu Hong'.",
        "",
        "4. TOKENS DE PROTECCIÓN (CRÍTICO): Los marcadores que empiezan con '<PROTECT_' y terminan con '>'",
        "   DEBEN mantenerse EXACTAMENTE igual, SIN CAMBIOS.",
        "   Ejemplos: '<PROTECT_GREAT_STRENGTH_1>' → '<PROTECT_GREAT_STRENGTH_1>' (sin cambios).",
        "   '<PROTECT_SKYSOAR_1>' → '<PROTECT_SKYSOAR_1>' (sin cambios).",
        "   NO traduzcas el contenido dentro de los corchetes. NO cambies 'PROTECT' por otra palabra.",
        "   NO cambies 'GREAT_STRENGTH' por 'DOMINIO_DE_CIELO' ni nada similar.",
        "   Si ves '<PROTECT_...>', cópialo EXACTAMENTE tal cual está.",
        "",
        "5. VARIANTE ESPAÑOL: Usa español neutro habitual en América. Evita 'vosotros', 'vale', 'coger' y giros peninsulares; usa 'ustedes', 'de acuerdo', 'tomar'.",
        "",
        "6. TRADUCCIÓN FIEL: Traduce palabra por palabra cuando sea necesario, pero mantén el sentido natural.",
        "   'willpower sea' → 'mar de voluntad' (no 'mar de voluntad de poder').",
        "   'Skysoar' → 'Skysoar' (es un nivel de cultivo, no se traduce).",
        "",
        "7. NO RESUMAS: Traduce TODO el contenido. No omitas párrafos, frases ni palabras.",
        "",
        "8. FORMATO: Mantén párrafos y líneas en blanco exactamente como en el original.",
        "   NO combines párrafos. NO agregues títulos intermedios.",
        "",
        "=== REGLAS DE TRADUCCIÓN ===",
        "",
        "- Títulos: 'Chapter N: ...' → 'Capítulo N: ...' (solo el título principal, no inventes subtítulos).",
        "- Diálogos: Mantén las comillas del original. Puedes usar comillas latinas (« ») o dobles (“ ”).",
        "- Números y fechas: NO los traduzcas. '15 de septiembre' está bien, pero 'September 15' → '15 de septiembre'.",
        "- Términos técnicos: Respeta el glosario. Si no está en el glosario, traduce de forma natural.",
        "",
        "=== EJEMPLOS DE ERRORES A EVITAR ===",
        "",
        "❌ MAL: 'Praise' → 'Elogio' (es un nombre propio, debe ser 'Liu Hong' o el nombre correcto).",
        "❌ MAL: Agregar 'Capítulo 1: ...' en medio del texto.",
        "❌ MAL: Dejar frases en inglés como 'No même soñar' (mezcla de idiomas).",
        "❌ MAL: 'willpower sea text' → 'texto de poder del mar' (debe ser 'texto de voluntad del mar').",
        "",
        "✅ BIEN: 'Liu Hong' → 'Liu Hong' (nombre propio, no se traduce).",
        "✅ BIEN: 'Chapter 230: ...' → 'Capítulo 230: ...' (solo al inicio).",
        "✅ BIEN: 'willpower sea' → 'mar de voluntad'.",
        "✅ BIEN: Todo el texto en español neutro (América), sin mezclar idiomas.",
    ]

    if glossary.never_translate:
        rules.append("")
        rules.append("Términos que NO se traducen (mantener exactamente): " + ", ".join(sorted(set(glossary.never_translate))))
    if glossary.translations:
        pairs = "; ".join([f"{k} => {v}" for k, v in glossary.translations.items()])
        rules.append("")
        rules.append("Glosario de traducciones forzadas: " + pairs)

    rules.append("")
    rules.append(f"Título del capítulo (referencia): {title_en}")
    return "\n".join(rules)


def _ollama_chat(
    base_url: str,
    model: str,
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.1,
    timeout: Optional[float] = 120.0,
) -> str:
    """Llama al endpoint /api/chat de Ollama."""
    url = urljoin(base_url.rstrip("/") + "/", "api/chat")
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
        },
    }
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    msg = (data or {}).get("message", {}) or {}
    content = msg.get("content", "")
    return (content or "").strip()


def _rewrite_to_spanish_ollama(
    base_url: str,
    model: str,
    text: str,
    *,
    temperature: float = 0.1,
    api_timeout: Optional[float] = 120.0,
) -> str:
    """Fallback: reescribe salida al español natural si el primer intento salió mal (p.ej., pseudo-latín)."""
    system = (
        "Eres un editor de español actual y natural (registro neutro habitual en América).\n"
        "Tu tarea es reescribir el texto dado al español correcto y natural.\n"
        "No añadas ni elimines información. Mantén formato de párrafos.\n"
        "Respeta tokens '<PROTECT_...>' sin modificarlos."
    )
    user = (
        "Reescribe el siguiente texto al español correcto y natural.\n"
        "Devuelve SOLO el texto reescrito, sin notas ni etiquetas.\n\n" + text
    )
    return _ollama_chat(
        base_url,
        model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        timeout=api_timeout,
    )


def translate_chunk_ollama(
    base_url: str,
    model: str,
    system_prompt: str,
    text: str,
    *,
    temperature: float = 0.1,  # Temperatura baja para mejor calidad
    retries: int = 3,
    backoff: float = 1.8,
    api_timeout: Optional[float] = 120.0,
    verbose: bool = False,
) -> str:
    """Traduce un chunk con Ollama usando prompts mejorados."""
    last_err: Optional[Exception] = None
    def _is_probably_spanish(s: str) -> bool:
        txt = (s or "").lower()
        # Palabras funcionales comunes del español
        stop = [
            " de ", " la ", " el ", " y ", " en ", " que ", " los ", " se ", " del ", " las ",
            " un ", " por ", " con ", " para ", " como ", " una ", " su ", " al ", " lo ",
        ]
        score = sum(1 for w in stop if w in txt)
        # Indicadores no deseados (formas arcaicas/pseudoacadémicas)
        bad = ["capitulum", "gradus", "secundum", "tertium", "ingraduum", "primus", "secundus"]
        bad_score = sum(1 for w in bad if w in txt)
        return score >= 2 and bad_score == 0

    for attempt in range(1, retries + 1):
        try:
            user_message = (
                "TRADUCE EL SIGUIENTE TEXTO AL ESPAÑOL NEUTRO (estándar en América).\n\n"
                "REGLAS ESTRICTAS:\n"
                "- Traduce TODO al español. NO dejes palabras en inglés, francés u otros idiomas.\n"
                "- Mantén registro neutro habitual en América: evita 'vosotros', 'vale', 'coger' (usa 'ustedes', 'de acuerdo', 'tomar').\n"
                "- NO agregues títulos de capítulo que no estén en el original.\n"
                "- Los nombres propios (Liu Hong, Su Yu, Bai Feng, etc.) NO se traducen. Manténlos igual.\n"
                "- Los tokens '<PROTECT_...>' deben mantenerse EXACTAMENTE igual, sin modificar.\n"
                "- Mantén el formato: párrafos y líneas en blanco como en el original.\n"
                "- Devuelve SOLO el texto traducido, sin notas ni explicaciones.\n\n"
                "TEXTO A TRADUCIR:\n"
                + text
            )
            # En intentos posteriores, refuerza evitar formas arcaicas/pseudoacadémicas explícitamente
            if attempt > 1:
                user_message = (
                    "Evita palabras arcaicas o pseudoacadémicas como 'Capitulum', 'Gradus', 'Secundum'.\n"
                    "Responde únicamente en español actual y natural.\n\n" + user_message
                )
            
            result = _ollama_chat(
                base_url,
                model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=temperature,
                timeout=api_timeout,
            )
            
            # Validación: detectar si hay mucho texto en inglés sin traducir
            if len(result) > 50:
                words = result.split()
                if words:
                    english_indicators = ["the", "a", "an", "is", "are", "was", "were", "to", "of", "and", "in", "on", "at", "for", "with", "from"]
                    english_count = len([w for w in words if w and w[0].isalpha() and w.lower() in english_indicators])
                    if english_count > len(words) * 0.15:  # Más del 15% son palabras comunes en inglés
                        if verbose:
                            print(f"[warn] Posible traducción incompleta detectada (intento {attempt}/{retries}), reintentando...", flush=True)
                        if attempt < retries:
                            time.sleep(backoff ** (attempt - 1))
                            continue
            # Validar idioma (español, no latín)
            if not _is_probably_spanish(result):
                if verbose:
                    print(f"[warn] Salida no parece español (intento {attempt}/{retries}), reintentando...", flush=True)
                if attempt < retries:
                    time.sleep(backoff ** (attempt - 1))
                    continue
            if not _is_probably_spanish(result):
                if verbose:
                    print(f"[warn] Salida no parece español (intento {attempt}/{retries}).", flush=True)
                if attempt < retries:
                    time.sleep(backoff ** (attempt - 1))
                    continue
                # Fallback: pedir reescritura a español natural
                fixed = _rewrite_to_spanish_ollama(
                    base_url, model, result, temperature=temperature, api_timeout=api_timeout
                )
                return fixed
            return result
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(backoff ** (attempt - 1))
            else:
                break
    raise RuntimeError(f"Fallo al traducir chunk (ollama): {last_err}")


async def translate_chunk_ollama_async(
    base_url: str,
    model: str,
    system_prompt: str,
    text: str,
    *,
    temperature: float = 0.1,
    retries: int = 3,
    backoff: float = 1.8,
    api_timeout: Optional[float] = 120.0,
    verbose: bool = False,
) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: translate_chunk_ollama(
            base_url, model, system_prompt, text,
            temperature=temperature, retries=retries, backoff=backoff,
            api_timeout=api_timeout, verbose=verbose
        ),
    )


def parse_json_block(s: str) -> Optional[dict]:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        return None


def extract_glossary_candidates_ollama(
    base_url: str,
    model: str,
    text: str,
    *,
    temperature: float = 0.0,
    api_timeout: Optional[float] = 60.0,
) -> Glossary:
    sys_prompt = (
        "Eres un extractor de términos para una novela."
        " A partir del texto en inglés, identifica:\n"
        " - never_translate: nombres propios de PERSONAS y LUGARES (solo el nombre base, p.ej., 'Nanyuan', 'Great Xia').\n"
        " - translations: términos de poderes/niveles/técnicas/estados/sistema con su traducción natural al español.\n"
        "Devuelve SOLO un JSON con las claves 'never_translate' (lista) y 'translations' (objeto)."
    )
    user_msg = (
        "Texto:\n" + text[:12000] + "\n\n"
        "Responde JSON estricto sin comentarios ni texto extra."
    )
    content = _ollama_chat(
        base_url,
        model,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ],
        temperature=temperature,
        timeout=api_timeout,
    )
    data = parse_json_block(content) or {}
    never = list(data.get("never_translate", []) or [])
    trans = dict(data.get("translations", {}) or {})
    return Glossary(never_translate=never, translations=trans, protect_tokens={}, restore_tokens={}, post_replace={})


async def translate_chapter_async_ollama(
    base_url: str,
    model: str,
    title_en: str,
    paragraphs: List[str],
    glossary: Glossary,
    *,
    chunk_chars: int = 3500,
    temperature: float = 0.1,
    api_timeout: Optional[float] = 120.0,
    verbose: bool = False,
    max_concurrent: int = 2,
) -> Tuple[str, List[str]]:
    import time as time_module
    start_time = time_module.time()

    system_prompt = build_system_prompt_ollama(title_en, glossary)
    title_src = protect_text(title_en, glossary)

    chunks = chunk_paragraphs(paragraphs, max_chars=chunk_chars)
    
    # Traducir título por separado (más control)
    if verbose:
        t0 = time_module.time()
        print(f"[time] Traduciendo título (ollama)...", flush=True)
    title_es = await translate_chunk_ollama_async(
        base_url, model, system_prompt, title_src, temperature=temperature, api_timeout=api_timeout, verbose=verbose
    )
    if verbose:
        print(f"[time] Título traducido en {time_module.time() - t0:.1f}s", flush=True)
    title_es = restore_text(title_es, glossary)
    title_es = apply_postprocess(title_es, glossary)
    title_es = clean_ollama_translation(title_es, title_en)
    translated_paragraphs = []

    # Traducir chunks en paralelo
    if chunks:
        if verbose:
            print(f"[time] Traduciendo {len(chunks)} chunks en paralelo (ollama, max {max_concurrent})...", flush=True)

        async def translate_single_chunk(i: int, chunk: List[str]) -> Tuple[int, List[str]]:
            text = "\n\n".join(chunk)
            text_src = protect_text(text, glossary)
            if verbose:
                t0 = time_module.time()
                print(f"[time] Iniciando chunk {i+1}/{len(chunks)} ({len(text)} chars)...", flush=True)
            out = await translate_chunk_ollama_async(
                base_url, model, system_prompt, text_src, temperature=temperature, api_timeout=api_timeout, verbose=verbose
            )
            if verbose:
                print(f"[time] Chunk {i+1} completado en {time_module.time() - t0:.1f}s", flush=True)
            out = restore_text(out, glossary)
            out = apply_postprocess(out, glossary)
            out = clean_ollama_translation(out, title_en, body_mode=True)
            out_pars = [p.strip() for p in out.split("\n\n") if p.strip()]
            return i, out_pars

        semaphore = asyncio.Semaphore(max_concurrent)

        async def translate_with_semaphore(i: int, chunk: List[str]) -> Tuple[int, List[str]]:
            async with semaphore:
                return await translate_single_chunk(i, chunk)

        tasks = [translate_with_semaphore(i, chunk) for i, chunk in enumerate(chunks)]
        results = await asyncio.gather(*tasks)
        results.sort(key=lambda x: x[0])
        for _, out_pars in results:
            translated_paragraphs.extend(out_pars)

    if verbose:
        total_time = time_module.time() - start_time
        print(f"[time] Capítulo completo traducido en {total_time:.1f}s ({total_time/60:.1f} min)", flush=True)

    return title_es, translated_paragraphs


def translate_chapter_ollama(
    base_url: str,
    model: str,
    title_en: str,
    paragraphs: List[str],
    glossary: Glossary,
    *,
    chunk_chars: int = 3500,
    temperature: float = 0.1,
    api_timeout: Optional[float] = 120.0,
    verbose: bool = False,
    max_concurrent: int = 2,
    use_async: bool = True,
) -> Tuple[str, List[str]]:
    if use_async:
        return asyncio.run(
            translate_chapter_async_ollama(
                base_url, model, title_en, paragraphs, glossary,
                chunk_chars=chunk_chars, temperature=temperature, api_timeout=api_timeout,
                verbose=verbose, max_concurrent=max_concurrent
            )
        )
    else:
        # Versión sync (más lenta pero más simple)
        import time as time_module
        start_time = time_module.time()
        system_prompt = build_system_prompt_ollama(title_en, glossary)
        title_src = protect_text(title_en, glossary)
        
        if verbose:
            t0 = time_module.time()
            print(f"[time] Traduciendo título (ollama sync)...", flush=True)
        title_es = translate_chunk_ollama(
            base_url, model, system_prompt, title_src, temperature=temperature, api_timeout=api_timeout, verbose=verbose
        )
        if verbose:
            print(f"[time] Título traducido en {time_module.time() - t0:.1f}s", flush=True)
        title_es = restore_text(title_es, glossary)
        title_es = apply_postprocess(title_es, glossary)
        title_es = clean_ollama_translation(title_es, title_en)
        
        chunks = chunk_paragraphs(paragraphs, max_chars=chunk_chars)
        translated_paragraphs = []
        for i, chunk in enumerate(chunks):
            text = "\n\n".join(chunk)
            text_src = protect_text(text, glossary)
            if verbose:
                t0 = time_module.time()
                print(f"[time] Traduciendo chunk {i+1}/{len(chunks)} ({len(text)} chars)...", flush=True)
            out = translate_chunk_ollama(
                base_url, model, system_prompt, text_src, temperature=temperature, api_timeout=api_timeout, verbose=verbose
            )
            if verbose:
                print(f"[time] Chunk {i+1} traducido en {time_module.time() - t0:.1f}s", flush=True)
            out = restore_text(out, glossary)
            out = apply_postprocess(out, glossary)
            out = clean_ollama_translation(out, title_en, body_mode=True)
            out_pars = [p.strip() for p in out.split("\n\n") if p.strip()]
            translated_paragraphs.extend(out_pars)
        
        if verbose:
            total_time = time_module.time() - start_time
            print(f"[time] Capítulo completo traducido en {total_time:.1f}s ({total_time/60:.1f} min)", flush=True)
        
        return title_es, translated_paragraphs


def write_chapter_es(dest_dir: Path, number: int, title_es: str, paragraphs_es: List[str]) -> str:
    fname = f"{str(number).zfill(4)}_es.txt"
    path = dest_dir / fname
    body = "\n\n".join(paragraphs_es)
    with path.open("w", encoding="utf-8") as f:
        f.write(title_es.strip() + "\n\n" + body + "\n")
    return fname


def load_index(index_path: Path) -> List[dict]:
    items: List[dict] = []
    if not index_path.exists():
        return items
    with index_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                pass
    return items


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Traducir capítulos EN->ES con Ollama (optimizado)")
    parser.add_argument("--input-dir", default="output/tribulation", help="Directorio con capítulos en inglés")
    parser.add_argument("--start", type=int, default=1, help="Capítulo inicial (incluido)")
    parser.add_argument("--end", type=int, default=0, help="Capítulo final (incluido). 0 = inferir de index.jsonl")
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL, help="Modelo Ollama a usar (por ej. llama3.2:3b)")
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"), help="URL base de Ollama")
    parser.add_argument("--temperature", type=float, default=0.1, help="Temperature de la generación (default: 0.1 para mejor calidad)")
    parser.add_argument("--chunk-chars", type=int, default=3500, help="Tamaño aproximado de chunk en caracteres (default: 3500 para modelos pequeños)")
    parser.add_argument("--max-concurrent", type=int, default=2, help="Máximo número de chunks a traducir en paralelo (default: 2)")
    parser.add_argument("--no-async", action="store_true", help="Desactivar paralelización async")
    parser.add_argument("--resume", action="store_true", help="Omitir capítulos ya traducidos")
    parser.add_argument("--glossary", default="config/translation_glossary.json", help="Ruta a glosario JSON opcional")
    parser.add_argument("--output-dir", default=None, help="Directorio de salida para los archivos traducidos")
    parser.add_argument("--auto-glossary", action="store_true", help="Detectar nombres/terminología y ampliar glosario")
    parser.add_argument("--persist-glossary", action="store_true", help="Guardar los cambios del glosario en el JSON")
    parser.add_argument("--extract-max-chars", type=int, default=8000, help="Máximo de caracteres a usar para extracción por capítulo")
    parser.add_argument("--verbose", action="store_true", help="Imprimir pasos detallados de progreso")
    parser.add_argument("--debug", action="store_true", help="Logs verbosos y trazas de error")
    parser.add_argument("--api-timeout", type=float, default=120.0, help="Timeout por request a la API (segundos)")

    args = parser.parse_args(argv)

    in_dir = Path(args.input_dir)
    if not in_dir.exists():
        print(f"[error] No existe input-dir: {in_dir}", file=sys.stderr)
        return 2

    idx = load_index(in_dir / "index.jsonl")
    if not idx:
        print("[warn] No se encontró index.jsonl o está vacío. Se listarán *_en.txt.", flush=True)
        files = sorted(in_dir.glob("*_en.txt"))
        numbers = [int(p.stem.split("_")[0]) for p in files if p.stem.split("_")[0].isdigit()]
        end_auto = max(numbers) if numbers else 0
    else:
        end_auto = max(item.get("number", 0) for item in idx)

    start = args.start
    end = args.end if args.end > 0 else end_auto
    if end < start:
        print(f"[error] Rango inválido: start={start} end={end}", file=sys.stderr)
        return 2

    out_dir = Path(args.output_dir) if args.output_dir else in_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.verbose:
        print(f"[out] Carpeta de salida: {out_dir}", flush=True)

    out_index_path = out_dir / "index_es.jsonl"
    out_index_file = out_index_path.open("a", encoding="utf-8")

    glossary_path = Path(args.glossary) if args.glossary else None
    glossary = Glossary.load(glossary_path)
    glossary.ensure_placeholders()

    if args.verbose:
        print(f"[cfg] Ollama URL: {args.ollama_url} | Modelo: {args.model}", flush=True)
        print(f"[cfg] chunk-chars: {args.chunk_chars} | temp: {args.temperature}", flush=True)
        print(f"[cfg] Auto-glossary: {args.auto_glossary} | Persist: {args.persist_glossary}", flush=True)
        par = 'async' if not args.no_async else 'sync'
        print(f"[cfg] Paralelización: {par} | max-concurrent: {args.max_concurrent}", flush=True)

    for n in range(start, end + 1):
        en_name = f"{str(n).zfill(4)}_en.txt"
        es_name = f"{str(n).zfill(4)}_es.txt"
        in_path = in_dir / en_name
        out_path = out_dir / es_name
        if args.resume and out_path.exists():
            print(f"[skip] {n} ya traducido ({es_name})", flush=True)
            continue
        if not in_path.exists():
            print(f"[miss] {n} no existe ({en_name})", file=sys.stderr)
            continue

        try:
            title_en, paragraphs = read_chapter(in_path)
            if args.verbose:
                print(f"[chap] {n}: leído '{en_name}' | párrafos: {len(paragraphs)}", flush=True)
            if args.auto_glossary:
                import time as time_module
                gloss_start = time_module.time()
                sample = (title_en + "\n\n" + "\n\n".join(paragraphs))[: args.extract_max_chars]
                if args.verbose:
                    print(f"[gloss] {n}: extrayendo candidatos de {len(sample)} chars...", flush=True)
                cand = extract_glossary_candidates_ollama(args.ollama_url, args.model, sample, api_timeout=args.api_timeout)
                if args.verbose:
                    gloss_time = time_module.time() - gloss_start
                    print(f"[gloss] {n}: never={len(cand.never_translate)} | trans={len(cand.translations)} (tardó {gloss_time:.1f}s)", flush=True)
                if cand.never_translate or cand.translations:
                    glossary.merge(cand)
                    if args.persist_glossary and glossary_path is not None:
                        glossary.save(glossary_path)
                        if args.verbose:
                            print(f"[gloss] {n}: glosario actualizado y persistido.", flush=True)
            title_es, paragraphs_es = translate_chapter_ollama(
                args.ollama_url,
                args.model,
                title_en,
                paragraphs,
                glossary,
                chunk_chars=args.chunk_chars,
                temperature=args.temperature,
                api_timeout=args.api_timeout,
                verbose=args.verbose,
                max_concurrent=args.max_concurrent,
                use_async=not args.no_async,
            )
            if args.verbose:
                print(f"[write] {n}: Título traducido: '{title_es[:80]}'...", flush=True)
                print(f"[write] {n}: Párrafos traducidos: {len(paragraphs_es)}", flush=True)
            saved = write_chapter_es(out_dir, n, title_es, paragraphs_es)
            rec = {
                "number": n,
                "title_en": title_en,
                "title_es": title_es,
                "file_en": en_name,
                "file_es": saved,
                "input_dir": str(in_dir),
                "output_dir": str(out_dir),
                "length_en": sum(len(p) for p in paragraphs),
                "length_es": sum(len(p) for p in paragraphs_es),
                "model": args.model,
                "provider": "ollama",
                "translated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            out_index_file.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out_index_file.flush()
            print(f"[ok] {n}: {saved}", flush=True)
        except Exception as e:
            print(f"[err] {n}: {e}", file=sys.stderr)
            if args.debug:
                traceback.print_exc()

    out_index_file.close()
    print("[done] Traducción completada.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
