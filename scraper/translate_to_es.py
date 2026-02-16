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

try:
    from openai import OpenAI, AsyncOpenAI  # type: ignore
except Exception:
    OpenAI = None
    AsyncOpenAI = None  # type: ignore


DEFAULT_MODEL = os.environ.get("OPENAI_TRANSLATE_MODEL", "gpt-4o-mini")
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_TRANSLATE_MODEL", "llama3.2:3b")


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


def protect_text(text: str, glossary: Glossary) -> str:
    items = sorted(glossary.protect_tokens.items(), key=lambda kv: len(kv[0]), reverse=True)
    for term, ph in items:
        if not term:
            continue
        pattern = r"\b" + re.escape(term) + r"\b"
        text = re.sub(pattern, ph, text)
    return text


def restore_text(text: str, glossary: Glossary) -> str:
    # 1) Reemplazo exacto (más eficiente)
    items = sorted(glossary.restore_tokens.items(), key=lambda kv: len(kv[0]), reverse=True)
    for ph, term in items:
        text = text.replace(ph, term)

    # 2) Reemplazo tolerante a espacios dentro del marcador: < PROTECT_... >
    def _repl(m: re.Match) -> str:
        tok = m.group(0)
        inner = tok[1:-1]  # quitar < >
        inner_norm = re.sub(r"\s+", "", inner)
        canonical = f"<{inner_norm}>"
        return glossary.restore_tokens.get(canonical, tok)

    text = re.sub(r"<\s*PROTECT_[A-Z0-9_]+\s*>", _repl, text)
    return text


def apply_postprocess(text: str, glossary: Glossary) -> str:
    for pat, repl in glossary.post_replace.items():
        text = re.sub(pat, repl, text)
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


def build_system_prompt(title_en: str, glossary: Glossary) -> str:
    rules: List[str] = [
        "Eres un traductor literario del inglés al español neutro, estándar para América.",
        (
            "Traduce TODO el contenido al español. No copies frases en inglés ni dejes palabras en inglés"
            " salvo los tokens de protección exactos."
        ),
        "Traduce con fidelidad, sin resumir ni explicar; conserva el tono, ritmo y matices narrativos del texto original.",
        "Preserva párrafos y líneas en blanco exactamente como en el original (no combines ni dividas).",
        "No agregues notas, aclaraciones ni comentarios meta.",
        "Traduce los títulos de capítulo al formato 'Capítulo N: …' cuando el original use 'Chapter N: …'.",
        "Usa español neutro habitual en América: evita 'vosotros', 'vale', 'coger' (usa 'ustedes', 'de acuerdo', 'tomar').",
        (
            "No traduzcas nombres propios de PERSONAS, LUGARES específicos, ni términos únicos sin traducción natural."
            " Pero SÍ traduce nombres de reinos, países, sectas, clanes u organizaciones"
            " cuando su traducción literal al español sea semánticamente correcta"
            " (por ejemplo: 'Great Xia Cultural Research Academy' → 'Academia de Investigación Cultural de la Gran Xia')."
        ),
        (
            "Traduce términos de poderes, habilidades, niveles de cultivo, técnicas, estados, armas genéricas y conceptos del sistema"
            " (p.ej. 'Source Opening', 'Soaring', 'Mountainsea', 'Secret Realm'),"
            " respetando las traducciones exactas definidas en el glosario."
        ),
        "El glosario tiene prioridad sobre todas las reglas: si un término aparece allí, usa esa traducción textual exacta.",
        "Respeta las mayúsculas y minúsculas de los nombres propios no traducidos.",
        "Usa puntuación y tildes propias del español (¿?, ¡!, comas, acentos, etc.).",
        (
            "Conserva el formato de diálogos; si el original usa comillas, mantenlas."
            " Puedes adaptar a comillas latinas (« ») o dobles (“ ”), pero sé consistente por capítulo."
        ),
        (
            "Muy importante: NO modifiques, traduzcas ni reordenes tokens de marcador con el patrón '<PROTECT_...>'."
            " Déjalos idénticos en la salida, sin espacios extra dentro de los corchetes; luego se restauran al texto correcto."
        ),
        "Mantén los números y fechas sin alterar (no los traduzcas ni los conviertas)."
    ]


    if glossary.never_translate:
        rules.append(
            "Lista de términos que NO se traducen (mantener exactamente): "
            + ", ".join(sorted(set(glossary.never_translate)))
        )
    if glossary.translations:
        pairs = "; ".join([f"{k} => {v}" for k, v in glossary.translations.items()])
        rules.append("Glosario de traducciones forzadas: " + pairs)

    rules.append(f"Título del capítulo (referencia): {title_en}")
    return "\n".join(rules)


def load_env_file(env_path: Optional[Path]) -> None:
    if not env_path:
        return
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                os.environ[k] = v
    except Exception:
        # Silencioso: si hay errores de parsing, no interrumpir ejecución
        pass


def ensure_client() -> "OpenAI":
    if OpenAI is None:
        raise RuntimeError(
            "El paquete 'openai' no está instalado. Ejecuta: pip install -r requirements.txt"
        )
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "Falta OPENAI_API_KEY en el entorno. Exporta tu clave o usa --env-file."
        )
    return OpenAI()


def translate_chunk(
    client: "OpenAI", model: str, system_prompt: str, text: str, *, temperature: float = 0.2, retries: int = 3, backoff: float = 1.8, api_timeout: Optional[float] = 120.0
) -> str:
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            "Traduce fielmente el siguiente fragmento. Responde EN ESPAÑOL. Devuelve SOLO el texto traducido, sin notas ni etiquetas.\n\n"
                            + text
                        ),
                    },
                ],
                timeout=api_timeout,
            )
            content = resp.choices[0].message.content or ""
            return content.strip()
        except Exception as e:  # pragma: no cover
            last_err = e
            if attempt < retries:
                time.sleep(backoff ** (attempt - 1))
            else:
                break
    raise RuntimeError(f"Fallo al traducir chunk: {last_err}")


async def translate_chunk_async(
    client: "AsyncOpenAI", model: str, system_prompt: str, text: str, *, temperature: float = 0.2, retries: int = 3, backoff: float = 1.8, api_timeout: Optional[float] = 120.0
) -> str:
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = await client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            "Traduce fielmente el siguiente fragmento. Responde EN ESPAÑOL. Devuelve SOLO el texto traducido, sin notas ni etiquetas.\n\n"
                            + text
                        ),
                    },
                ],
                timeout=api_timeout,
            )
            content = resp.choices[0].message.content or ""
            return content.strip()
        except Exception as e:  # pragma: no cover
            last_err = e
            if attempt < retries:
                await asyncio.sleep(backoff ** (attempt - 1))
            else:
                break
    raise RuntimeError(f"Fallo al traducir chunk: {last_err}")


def parse_json_block(s: str) -> Optional[dict]:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        return None


def extract_glossary_candidates(client: "OpenAI", model: str, text: str, *, temperature: float = 0.0, api_timeout: Optional[float] = 60.0) -> Glossary:
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
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ],
        timeout=api_timeout,
    )
    content = resp.choices[0].message.content or "{}"
    data = parse_json_block(content) or {}
    never = list(data.get("never_translate", []) or [])
    trans = dict(data.get("translations", {}) or {})
    return Glossary(never_translate=never, translations=trans, protect_tokens={}, restore_tokens={}, post_replace={})


# ==========================
#   Backend: Ollama (HTTP)
# ==========================

def _ollama_chat(
    base_url: str,
    model: str,
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.2,
    timeout: Optional[float] = 120.0,
) -> str:
    """Llama al endpoint /api/chat de Ollama (stream=False) y devuelve el contenido del assistant.

    Estructura esperada de respuesta:
    { "message": { "content": "..." }, ... }
    """
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


def translate_chunk_ollama(
    base_url: str,
    model: str,
    system_prompt: str,
    text: str,
    *,
    temperature: float = 0.2,
    retries: int = 3,
    backoff: float = 1.8,
    api_timeout: Optional[float] = 120.0,
) -> str:
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            return _ollama_chat(
                base_url,
                model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            "Traduce fielmente el siguiente fragmento. Responde EN ESPAÑOL. Devuelve SOLO el texto traducido, sin notas ni etiquetas.\n\n"
                            + text
                        ),
                    },
                ],
                temperature=temperature,
                timeout=api_timeout,
            )
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
    temperature: float = 0.2,
    retries: int = 3,
    backoff: float = 1.8,
    api_timeout: Optional[float] = 120.0,
) -> str:
    # Ejecuta la versión sync en un hilo para poder usarla con asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: translate_chunk_ollama(
            base_url,
            model,
            system_prompt,
            text,
            temperature=temperature,
            retries=retries,
            backoff=backoff,
            api_timeout=api_timeout,
        ),
    )


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
    chunk_chars: int = 7000,
    temperature: float = 0.2,
    api_timeout: Optional[float] = 120.0,
    verbose: bool = False,
    max_concurrent: int = 3,
) -> Tuple[str, List[str]]:
    import time as time_module
    start_time = time_module.time()

    system_prompt = build_system_prompt(title_en, glossary)
    title_src = protect_text(title_en, glossary)

    chunks = chunk_paragraphs(paragraphs, max_chars=chunk_chars)
    if chunks and len(title_src) + 200 < chunk_chars // 2:
        first_chunk_text = "\n\n".join(chunks[0])
        combined = title_src + "\n\n" + first_chunk_text
        combined_src = protect_text(combined, glossary)
        if verbose:
            t0 = time_module.time()
            print(f"[time] Traduciendo título+primer_chunk (ollama) ({len(combined)} chars)...", flush=True)
        out = await translate_chunk_ollama_async(
            base_url, model, system_prompt, combined_src, temperature=temperature, api_timeout=api_timeout
        )
        if verbose:
            print(f"[time] Título+primer_chunk traducido en {time_module.time() - t0:.1f}s", flush=True)
        out = restore_text(out, glossary)
        out = apply_postprocess(out, glossary)
        out_pars = [p.strip() for p in out.split("\n\n") if p.strip()]
        title_es = out_pars[0] if out_pars else title_en
        translated_paragraphs = out_pars[1:] if len(out_pars) > 1 else []
        chunks = chunks[1:]
    else:
        if verbose:
            t0 = time_module.time()
            print(f"[time] Traduciendo título (ollama)...", flush=True)
        title_es = await translate_chunk_ollama_async(
            base_url, model, system_prompt, title_src, temperature=temperature, api_timeout=api_timeout
        )
        if verbose:
            print(f"[time] Título traducido en {time_module.time() - t0:.1f}s", flush=True)
        title_es = restore_text(title_es, glossary)
        title_es = apply_postprocess(title_es, glossary)
        translated_paragraphs = []

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
                base_url, model, system_prompt, text_src, temperature=temperature, api_timeout=api_timeout
            )
            if verbose:
                print(f"[time] Chunk {i+1} completado en {time_module.time() - t0:.1f}s", flush=True)
            out = restore_text(out, glossary)
            out = apply_postprocess(out, glossary)
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
    chunk_chars: int = 7000,
    temperature: float = 0.2,
    delay: float = 0.0,
    api_timeout: Optional[float] = 120.0,
    verbose: bool = False,
    max_concurrent: int = 3,
    use_async: bool = True,
) -> Tuple[str, List[str]]:
    if use_async:
        return asyncio.run(
            translate_chapter_async_ollama(
                base_url,
                model,
                title_en,
                paragraphs,
                glossary,
                chunk_chars=chunk_chars,
                temperature=temperature,
                api_timeout=api_timeout,
                verbose=verbose,
                max_concurrent=max_concurrent,
            )
        )
    else:
        import time as time_module
        start_time = time_module.time()

        system_prompt = build_system_prompt(title_en, glossary)
        title_src = protect_text(title_en, glossary)

        chunks = chunk_paragraphs(paragraphs, max_chars=chunk_chars)
        if chunks and len(title_src) + 200 < chunk_chars // 2:
            first_chunk_text = "\n\n".join(chunks[0])
            combined = title_src + "\n\n" + first_chunk_text
            combined_src = protect_text(combined, glossary)
            if verbose:
                t0 = time_module.time()
                print(f"[time] Traduciendo título+primer_chunk (ollama) ({len(combined)} chars)...", flush=True)
            out = translate_chunk_ollama(
                base_url, model, system_prompt, combined_src, temperature=temperature, api_timeout=api_timeout
            )
            if verbose:
                print(f"[time] Título+primer_chunk traducido en {time_module.time() - t0:.1f}s", flush=True)
            out = restore_text(out, glossary)
            out = apply_postprocess(out, glossary)
            out_pars = [p.strip() for p in out.split("\n\n") if p.strip()]
            title_es = out_pars[0] if out_pars else title_en
            translated_paragraphs = out_pars[1:] if len(out_pars) > 1 else []
            chunks = chunks[1:]
        else:
            if verbose:
                t0 = time_module.time()
                print(f"[time] Traduciendo título (ollama)...", flush=True)
            title_es = translate_chunk_ollama(
                base_url, model, system_prompt, title_src, temperature=temperature, api_timeout=api_timeout
            )
            if verbose:
                print(f"[time] Título traducido en {time_module.time() - t0:.1f}s", flush=True)
            title_es = restore_text(title_es, glossary)
            title_es = apply_postprocess(title_es, glossary)
            translated_paragraphs = []

        for i, chunk in enumerate(chunks):
            text = "\n\n".join(chunk)
            text_src = protect_text(text, glossary)
            if verbose:
                t0 = time_module.time()
                print(f"[time] Traduciendo chunk {i+1}/{len(chunks)} ({len(text)} chars)...", flush=True)
            out = translate_chunk_ollama(
                base_url, model, system_prompt, text_src, temperature=temperature, api_timeout=api_timeout
            )
            if verbose:
                print(f"[time] Chunk {i+1} traducido en {time_module.time() - t0:.1f}s", flush=True)
            out = restore_text(out, glossary)
            out = apply_postprocess(out, glossary)
            out_pars = [p.strip() for p in out.split("\n\n") if p.strip()]
            translated_paragraphs.extend(out_pars)
            if delay > 0:
                time.sleep(delay)

        if verbose:
            total_time = time_module.time() - start_time
            print(f"[time] Capítulo completo traducido en {total_time:.1f}s ({total_time/60:.1f} min)", flush=True)

        return title_es, translated_paragraphs


async def translate_chapter_async(
    async_client: "AsyncOpenAI",
    model: str,
    title_en: str,
    paragraphs: List[str],
    glossary: Glossary,
    *,
    chunk_chars: int = 7000,
    temperature: float = 0.2,
    api_timeout: Optional[float] = 120.0,
    verbose: bool = False,
    max_concurrent: int = 3,
) -> Tuple[str, List[str]]:
    import time as time_module
    start_time = time_module.time()
    
    system_prompt = build_system_prompt(title_en, glossary)
    title_src = protect_text(title_en, glossary)
    
    # Optimización: combinar título con primer chunk si es pequeño
    chunks = chunk_paragraphs(paragraphs, max_chars=chunk_chars)
    if chunks and len(title_src) + 200 < chunk_chars // 2:
        # Combinar título con primer chunk
        first_chunk_text = "\n\n".join(chunks[0])
        combined = title_src + "\n\n" + first_chunk_text
        combined_src = protect_text(combined, glossary)
        if verbose:
            t0 = time_module.time()
            print(f"[time] Traduciendo título+primer_chunk ({len(combined)} chars)...", flush=True)
        out = await translate_chunk_async(
            async_client, model, system_prompt, combined_src, temperature=temperature, api_timeout=api_timeout
        )
        if verbose:
            print(f"[time] Título+primer_chunk traducido en {time_module.time() - t0:.1f}s", flush=True)
        out = restore_text(out, glossary)
        out = apply_postprocess(out, glossary)
        out_pars = [p.strip() for p in out.split("\n\n") if p.strip()]
        title_es = out_pars[0] if out_pars else title_en
        translated_paragraphs = out_pars[1:] if len(out_pars) > 1 else []
        chunks = chunks[1:]  # Resto de chunks
    else:
        # Método original: traducir título por separado
        if verbose:
            t0 = time_module.time()
            print(f"[time] Traduciendo título...", flush=True)
        title_es = await translate_chunk_async(
            async_client, model, system_prompt, title_src, temperature=temperature, api_timeout=api_timeout
        )
        if verbose:
            print(f"[time] Título traducido en {time_module.time() - t0:.1f}s", flush=True)
        title_es = restore_text(title_es, glossary)
        title_es = apply_postprocess(title_es, glossary)
        translated_paragraphs = []

    # Traducir chunks restantes en paralelo
    if chunks:
        if verbose:
            print(f"[time] Traduciendo {len(chunks)} chunks en paralelo (max {max_concurrent} concurrentes)...", flush=True)
        
        async def translate_single_chunk(i: int, chunk: List[str]) -> Tuple[int, List[str]]:
            text = "\n\n".join(chunk)
            text_src = protect_text(text, glossary)
            if verbose:
                t0 = time_module.time()
                print(f"[time] Iniciando chunk {i+1}/{len(chunks)} ({len(text)} chars)...", flush=True)
            out = await translate_chunk_async(
                async_client, model, system_prompt, text_src, temperature=temperature, api_timeout=api_timeout
            )
            if verbose:
                print(f"[time] Chunk {i+1} completado en {time_module.time() - t0:.1f}s", flush=True)
            out = restore_text(out, glossary)
            out = apply_postprocess(out, glossary)
            out_pars = [p.strip() for p in out.split("\n\n") if p.strip()]
            return i, out_pars
        
        # Crear semáforo para limitar concurrencia
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def translate_with_semaphore(i: int, chunk: List[str]) -> Tuple[int, List[str]]:
            async with semaphore:
                return await translate_single_chunk(i, chunk)
        
        # Ejecutar todos los chunks en paralelo
        tasks = [translate_with_semaphore(i, chunk) for i, chunk in enumerate(chunks)]
        results = await asyncio.gather(*tasks)
        
        # Ordenar resultados por índice para mantener el orden
        results.sort(key=lambda x: x[0])
        for _, out_pars in results:
            translated_paragraphs.extend(out_pars)
    
    if verbose:
        total_time = time_module.time() - start_time
        print(f"[time] Capítulo completo traducido en {total_time:.1f}s ({total_time/60:.1f} min)", flush=True)
    
    return title_es, translated_paragraphs


def translate_chapter(
    client: "OpenAI",
    model: str,
    title_en: str,
    paragraphs: List[str],
    glossary: Glossary,
    *,
    chunk_chars: int = 7000,
    temperature: float = 0.2,
    delay: float = 0.0,
    api_timeout: Optional[float] = 120.0,
    verbose: bool = False,
    max_concurrent: int = 3,
    use_async: bool = True,
) -> Tuple[str, List[str]]:
    """Wrapper que usa async si está disponible, sino usa sync"""
    if use_async and AsyncOpenAI is not None:
        # Crear cliente async
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY no encontrada")
        async_client = AsyncOpenAI(api_key=api_key)
        
        async def run_with_cleanup():
            try:
                return await translate_chapter_async(
                    async_client, model, title_en, paragraphs, glossary,
                    chunk_chars=chunk_chars, temperature=temperature, api_timeout=api_timeout,
                    verbose=verbose, max_concurrent=max_concurrent
                )
            finally:
                # Cerrar el cliente antes de que el event loop termine
                await async_client.close()
        
        # Ejecutar versión async
        return asyncio.run(run_with_cleanup())
    else:
        # Fallback a versión sync original
        import time as time_module
        start_time = time_module.time()
        
        system_prompt = build_system_prompt(title_en, glossary)
        title_src = protect_text(title_en, glossary)
        
        chunks = chunk_paragraphs(paragraphs, max_chars=chunk_chars)
        if chunks and len(title_src) + 200 < chunk_chars // 2:
            first_chunk_text = "\n\n".join(chunks[0])
            combined = title_src + "\n\n" + first_chunk_text
            combined_src = protect_text(combined, glossary)
            if verbose:
                t0 = time_module.time()
                print(f"[time] Traduciendo título+primer_chunk ({len(combined)} chars)...", flush=True)
            out = translate_chunk(
                client, model, system_prompt, combined_src, temperature=temperature, api_timeout=api_timeout
            )
            if verbose:
                print(f"[time] Título+primer_chunk traducido en {time_module.time() - t0:.1f}s", flush=True)
            out = restore_text(out, glossary)
            out = apply_postprocess(out, glossary)
            out_pars = [p.strip() for p in out.split("\n\n") if p.strip()]
            title_es = out_pars[0] if out_pars else title_en
            translated_paragraphs = out_pars[1:] if len(out_pars) > 1 else []
            chunks = chunks[1:]
        else:
            if verbose:
                t0 = time_module.time()
                print(f"[time] Traduciendo título...", flush=True)
            title_es = translate_chunk(
                client, model, system_prompt, title_src, temperature=temperature, api_timeout=api_timeout
            )
            if verbose:
                print(f"[time] Título traducido en {time_module.time() - t0:.1f}s", flush=True)
            title_es = restore_text(title_es, glossary)
            title_es = apply_postprocess(title_es, glossary)
            translated_paragraphs = []

        for i, chunk in enumerate(chunks):
            text = "\n\n".join(chunk)
            text_src = protect_text(text, glossary)
            if verbose:
                t0 = time_module.time()
                print(f"[time] Traduciendo chunk {i+1}/{len(chunks)} ({len(text)} chars)...", flush=True)
            out = translate_chunk(
                client, model, system_prompt, text_src, temperature=temperature, api_timeout=api_timeout
            )
            if verbose:
                print(f"[time] Chunk {i+1} traducido en {time_module.time() - t0:.1f}s", flush=True)
            out = restore_text(out, glossary)
            out = apply_postprocess(out, glossary)
            out_pars = [p.strip() for p in out.split("\n\n") if p.strip()]
            translated_paragraphs.extend(out_pars)
            if delay > 0:
                time.sleep(delay)
        
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
    parser = argparse.ArgumentParser(description="Traducir capítulos EN->ES respetando nombres y glosario (OpenAI u Ollama)")
    parser.add_argument("--input-dir", default="output/tribulation", help="Directorio con capítulos en inglés")
    parser.add_argument("--start", type=int, default=1, help="Capítulo inicial (incluido)")
    parser.add_argument("--end", type=int, default=0, help="Capítulo final (incluido). 0 = inferir de index.jsonl")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Modelo a usar. OpenAI: gpt-4o-mini | Ollama: llama3.2:3b, mistral, etc.")
    parser.add_argument("--provider", choices=["openai", "ollama"], default=os.environ.get("TRANSLATE_PROVIDER", "openai"), help="Proveedor: openai u ollama")
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"), help="URL base de Ollama (http://localhost:11434)")
    parser.add_argument("--temperature", type=float, default=0.2, help="Temperature de la generación")
    parser.add_argument("--chunk-chars", type=int, default=12000, help="Tamaño aproximado de chunk en caracteres")
    parser.add_argument("--max-concurrent", type=int, default=3, help="Máximo número de chunks a traducir en paralelo")
    parser.add_argument("--no-async", action="store_true", help="Desactivar paralelización async (usar modo secuencial)")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay entre llamadas (s)")
    parser.add_argument("--resume", action="store_true", help="Omitir capítulos ya traducidos")
    parser.add_argument("--glossary", default="config/translation_glossary.json", help="Ruta a glosario JSON opcional")
    parser.add_argument("--output-dir", default=None, help="Directorio de salida para los archivos traducidos (por defecto, el mismo que input-dir)")
    parser.add_argument("--auto-glossary", action="store_true", help="Detectar nombres/terminología y ampliar glosario sobre la marcha")
    parser.add_argument("--persist-glossary", action="store_true", help="Guardar los cambios del glosario en el JSON tras cada capítulo")
    parser.add_argument("--extract-max-chars", type=int, default=8000, help="Máximo de caracteres a usar para extracción por capítulo")
    parser.add_argument("--env-file", default=".env", help="Ruta a archivo .env con OPENAI_API_KEY (opcional para OpenAI)")
    parser.add_argument("--verbose", action="store_true", help="Imprimir pasos detallados de progreso")
    parser.add_argument("--debug", action="store_true", help="Logs verbosos y trazas de error")
    parser.add_argument("--api-timeout", type=float, default=60.0, help="Timeout por request a la API (segundos)")

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

    # Cargar .env si existe
    env_path = Path(args.env_file) if args.env_file else None
    load_env_file(env_path)
    if args.verbose:
        print(f"[env] Cargado .env desde: {env_path} (existe={env_path.exists() if env_path else False})", flush=True)
        if args.provider == "openai":
            has_key = bool(os.environ.get("OPENAI_API_KEY"))
            print(f"[env] OPENAI_API_KEY presente: {has_key}", flush=True)

    # Ajustar modelo por defecto si el proveedor es Ollama y el modelo sigue siendo el default de OpenAI
    if args.provider == "ollama" and args.model == DEFAULT_MODEL:
        args.model = DEFAULT_OLLAMA_MODEL

    if args.verbose:
        print(f"[cfg] Proveedor: {args.provider} | Modelo: {args.model}", flush=True)
        if args.provider == "ollama":
            print(f"[cfg] Ollama URL: {args.ollama_url}", flush=True)
        print(f"[cfg] chunk-chars: {args.chunk_chars} | temp: {args.temperature}", flush=True)
        print(f"[cfg] Auto-glossary: {args.auto_glossary} | Persist: {args.persist_glossary}", flush=True)
        if args.provider == "openai":
            par = 'async' if (not args.no_async and AsyncOpenAI is not None) else 'sync'
        else:
            par = 'async' if not args.no_async else 'sync'
        print(f"[cfg] Paralelización: {par} | max-concurrent: {args.max_concurrent}", flush=True)

    client: Optional["OpenAI"] = None
    if args.provider == "openai":
        client = ensure_client()

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
                if args.provider == "openai":
                    if client is None:
                        raise RuntimeError("Cliente OpenAI no inicializado")
                    cand = extract_glossary_candidates(client, args.model, sample, api_timeout=args.api_timeout)
                else:
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
            if args.provider == "openai":
                if client is None:
                    raise RuntimeError("Cliente OpenAI no inicializado")
                title_es, paragraphs_es = translate_chapter(
                    client,
                    args.model,
                    title_en,
                    paragraphs,
                    glossary,
                    chunk_chars=args.chunk_chars,
                    temperature=args.temperature,
                    delay=args.delay,
                    api_timeout=args.api_timeout,
                    verbose=args.verbose,
                    max_concurrent=args.max_concurrent,
                    use_async=not args.no_async,
                )
            else:
                title_es, paragraphs_es = translate_chapter_ollama(
                    args.ollama_url,
                    args.model,
                    title_en,
                    paragraphs,
                    glossary,
                    chunk_chars=args.chunk_chars,
                    temperature=args.temperature,
                    delay=args.delay,
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
                "provider": args.provider,
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
