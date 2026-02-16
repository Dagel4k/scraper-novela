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
    from openai import OpenAI, AsyncOpenAI
except Exception:
    OpenAI = None
    AsyncOpenAI = None

DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_TRANSLATE_MODEL", "qwen2.5:7b")
DEFAULT_GPT_MODEL = os.environ.get("OPENAI_REFINE_MODEL", "gpt-4o-mini")


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

    def ensure_placeholders(self) -> None:
        # Asegura placeholders para la lista never_translate
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
                # También registra una clave canónica sin índice para facilitar restauraciones
                base_key = f"<PROTECT_{slugify(term)}>"
                self.restore_tokens.setdefault(base_key, term)
        # Asegura que cualquier token de restauración existente tenga su forma base sin índice
        existing = list(self.restore_tokens.items())
        for ph, term in existing:
            m = re.match(r"^<PROTECT_([A-Z0-9_]+?)_\d+>$", ph)
            if m:
                base_key = f"<PROTECT_{m.group(1)}>"
                self.restore_tokens.setdefault(base_key, term)


@dataclass
class IngestGlossary:
    replace: Dict[str, str]

    @staticmethod
    def load(path: Optional[Path]) -> "IngestGlossary":
        if path is None or not path.exists():
            return IngestGlossary({})
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return IngestGlossary(replace=dict(data.get("replace", {}) or {}))


def slugify(s: str) -> str:
    s2 = re.sub(r"[^A-Za-z0-9]+", "_", s.strip())
    return re.sub(r"_+", "_", s2).strip("_").upper()[:40]


def apply_ingest_replacements(text: str, ingest_glossary: IngestGlossary) -> str:
    items = sorted(ingest_glossary.replace.items(), key=lambda kv: len(kv[0]), reverse=True)
    for original, replacement in items:
        if original:
            pattern = r"\b" + re.escape(original) + r"\b"
            text = re.sub(pattern, replacement, text)
    return text


def protect_text(text: str, glossary: Glossary) -> str:
    items = sorted(glossary.protect_tokens.items(), key=lambda kv: len(kv[0]), reverse=True)
    for term, ph in items:
        if not term:
            continue
        pattern = r"\b" + re.escape(term) + r"\b"
        text = re.sub(pattern, ph, text)
    return text


def restore_text(text: str, glossary: Glossary) -> str:
    import unicodedata

    def _strip_accents(s: str) -> str:
        try:
            return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
        except Exception:
            return s
    # Tabla de equivalencias comunes ES->EN para componentes dentro de placeholders
    ES_TO_EN_SEGMENT = {
        # títulos y roles
        "VIEJO": "OLD",
        "ANCIANO": "ELDER",
        "ABUELO": "GRANDPA",
        "TIO": "UNCLE",
        "TIO_MARTIAL": "MARTIAL_UNCLE",
        "MAESTRO": "TEACHER",
        "DIRECTOR": "DIRECTOR",
        "ADMINISTRADOR": "ADMINISTRATOR",
        "GERENTE": "MANAGER",
        "JEFE": "HEAD",
        "DECANO": "DEAN",
        "PRINCIPAL": "PRINCIPAL",
        "HERMANO": "BROTHER",
        "HERMANA": "SISTER",
        "CONDE": "MARQUIS",
        "MARQUES": "MARQUIS",
        "REY": "KING",
        "REINA": "QUEEN",
        "GRAN": "GREAT",
        "GRANDES": "GREAT",
        "FAMILIA": "FAMILY",
        "CLAN": "CLAN",
        # términos de niveles y reinos
        "REINO": "REALM",
        "REINOS": "REALMS",
        "INVENCIBLE": "INVINCIBLE",
        "MONTANASEAS": "MOUNTAINSEAS",
        "MONTANAMAR": "MOUNTAINSEA",
        "CIELOS": "HEAVENS",
        "SOLLUNA": "SUNMOON",
        "ROMPENUBE": "CLOUDBREACH",
        "BUSQUEDA_CONOCIMIENTO": "KNOWLEDGE_SEEKING",
        # facciones, academias
        "FACCION": "FACTION",
        "CARACTER": "CHARACTER",
        "CARACTERES": "CHARACTERS",
        "MULTIPLE": "MULTIPLE",
        "ACADEMIA": "ACADEMY",
        "INVESTIGACION": "RESEARCH",
        "CULTURAL": "CULTURAL",
        "GUERRA": "WAR",
        # armas/tecnicas frecuentes
        "ESPADA": "SWORD",
        "MATA_DRAGONES": "DRAGON_SLAYING",
        "ARTE": "ART",
        "TECNOLOGIA": "TECHNIQUE",
        "TECNOLOGIA_CULTURAL": "CULTURAL_WEAPON",
        "GRADO_CELESTIAL": "HEAVEN_GRADE",
        "GRADO_TERRenal": "EARTH_GRADE",
        "GRADO_PROFUNDO": "PROFOUND_GRADE",
        "GRADO_AMARILLO": "YELLOW_GRADE",
        # lugares
        "REINO_HUMANO": "HUMAN_REALM",
        "GRAN_XIA": "GREAT_XIA",
        "GRAN_ZHOU": "GREAT_ZHOU",
    }

    def _es_to_en_slug(slug: str) -> str:
        parts = [p for p in slug.split("_") if p]
        mapped = []
        for p in parts:
            key = p.upper()
            # normaliza vocales acentuadas eliminadas por slugify (aprox)
            key = (
                key.replace("Á", "A").replace("É", "E").replace("Í", "I").replace("Ó", "O").replace("Ú", "U")
                .replace("Ñ", "N")
            )
            mapped.append(ES_TO_EN_SEGMENT.get(key, key))
        return "_".join(mapped)

    # Precalcular sets de segmentos para claves de restore_tokens
    key_sets = []  # list[(key, term, set_of_segments)]
    for k, term in glossary.restore_tokens.items():
        m = re.match(r"^<PROTECT_([A-Z0-9_]+?)(?:_\d+)?>$", k)
        if not m:
            continue
        base = m.group(1)
        segs = set([s for s in base.split("_") if s])
        key_sets.append((k, term, segs))

    # 1) Reemplazo directo exacto (por si el modelo respetó los tokens)
    items = sorted(glossary.restore_tokens.items(), key=lambda kv: len(kv[0]), reverse=True)
    for ph, term in items:
        text = text.replace(ph, term)

    # 2) Normaliza variantes con espacios y mayúsc/minúsc, p.ej. "< PROTECT_XIA_YUWEN_1 >"
    def _repl_angle(m: re.Match) -> str:
        tok = m.group(0)
        name = m.group("name") or ""
        # Normaliza el cuerpo a nuestro placeholder canónico
        slug = slugify(name)
        # Intenta con y sin índice
        candidates = [f"<PROTECT_{slug}>"]
        if re.search(r"_\d+\b", slug):
            candidates.insert(0, f"<PROTECT_{slug}>")
        # Fallback: buscar cualquier clave que empiece por el slug
        for key in candidates:
            if key in glossary.restore_tokens:
                return glossary.restore_tokens[key]
        for key in glossary.restore_tokens.keys():
            if key.startswith(f"<PROTECT_{slug}"):
                return glossary.restore_tokens[key]
        return tok

    # Acepta variantes como <PROTECT_...>, <PROTEGER ...>, con guiones/barras/espacios; case-insensitive
    # Permite cualquier contenido hasta '>' para abarcar acentos/Unicode
    angle_pat = re.compile(r"<\s*(?:PROTECT|PROTEGER)\s*[_:\-\s]*?(?P<name>[^>]+?)\s*>", re.IGNORECASE)
    def _repl_angle(m: re.Match) -> str:
        tok = m.group(0)
        name = m.group("name") or ""
        raw = slugify(_strip_accents(name))
        candidates = []
        # 1) tal cual
        candidates.append(f"<PROTECT_{raw}>")
        # 2) versión ES->EN aproximada
        approx = _es_to_en_slug(raw)
        if approx != raw:
            candidates.append(f"<PROTECT_{approx}>")
        # 3) Si parece llevar índice, considera forma sin índice y con índice
        base = re.sub(r"_\d+\b", "", raw)
        if base and base != raw:
            candidates.append(f"<PROTECT_{base}>")
        # Busca en restore_tokens: exacto o por prefijo
        for key in candidates:
            if key in glossary.restore_tokens:
                return glossary.restore_tokens[key]
        for key in glossary.restore_tokens.keys():
            for pref in candidates:
                if key.startswith(pref.rstrip(">")):
                    return glossary.restore_tokens[key]
        # Coincidencia difusa por segmentos (ignorando orden)
        for candidate in [raw, approx, base]:
            if not candidate:
                continue
            base_slug = re.sub(r"_\d+\b", "", candidate)
            segs = set([s for s in base_slug.split("_") if s])
            best = None
            best_score = 0.0
            for k, term, ksegs in key_sets:
                inter = len(segs & ksegs)
                denom = max(len(segs), len(ksegs)) or 1
                score = inter / denom
                if score > best_score:
                    best_score = score
                    best = (k, term)
            if best and best_score >= 0.66:  # requiere al menos 2/3 de solapamiento
                return best[1]
        return tok
    text = angle_pat.sub(_repl_angle, text)

    # 3) También corrige casos sin corchetes: PROTECT_XIA_YUWEN_1 o PROTEGER XIA YUWEN 1
    def _repl_bare(m: re.Match) -> str:
        tok = m.group(0)
        name = m.group("name") or ""
        raw = slugify(_strip_accents(name))
        candidates = [f"<PROTECT_{raw}>"]
        approx = _es_to_en_slug(raw)
        if approx != raw:
            candidates.append(f"<PROTECT_{approx}>")
        base = re.sub(r"_\d+\b", "", raw)
        if base and base != raw:
            candidates.append(f"<PROTECT_{base}>")
        for key in candidates:
            if key in glossary.restore_tokens:
                return glossary.restore_tokens[key]
        for key in glossary.restore_tokens.keys():
            for pref in candidates:
                if key.startswith(pref.rstrip(">")):
                    return glossary.restore_tokens[key]
        # Fuzzy por segmentos
        for candidate in [raw, approx, base]:
            if not candidate:
                continue
            base_slug = re.sub(r"_\d+\b", "", candidate)
            segs = set([s for s in base_slug.split("_") if s])
            best = None
            best_score = 0.0
            for k, term, ksegs in key_sets:
                inter = len(segs & ksegs)
                denom = max(len(segs), len(ksegs)) or 1
                score = inter / denom
                if score > best_score:
                    best_score = score
                    best = (k, term)
            if best and best_score >= 0.66:
                return best[1]
        return tok

    bare_pat = re.compile(r"\b(?:PROTECT|PROTEGER)\s*[_:\-\s]*?(?P<name>[^>\n]*?_\d+)\b", re.IGNORECASE)
    text = bare_pat.sub(_repl_bare, text)

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


def build_stage1_prompt(title_en: str, glossary: Glossary) -> str:
    rules = [
        "Eres un traductor profesional del inglés al español neutro, estándar para América.",
        "Tu objetivo es producir un BORRADOR fiel, no un texto literario pulido.",
        "",
        "Reglas estrictas:",
        "- Traduce TODO el contenido al español. No dejes frases en inglés.",
        "- No resumas, no expliques, no añadas ni elimines información.",
        "- Mantén el orden de las frases y la estructura lo más cercana posible al original.",
        "- Preserva párrafos y líneas en blanco exactamente como en el texto de entrada.",
        "- Respeta la puntuación y el formato de los diálogos (comillas, guiones, saltos de línea).",
        "- No hagas comentarios, notas, ni texto meta.",
        "",
        "Variante de español:",
        "- Usa español neutro habitual en América. Evita regionalismos de España (p. ej., 'vosotros', 'vale', 'coger').",
        "- Prefiere 'ustedes' en lugar de 'vosotros' y formulaciones neutras comunes en la región.",
        "",
        "Glosario:",
        "- El glosario tiene prioridad absoluta sobre cualquier otra regla.",
        "- Si un término aparece en el glosario, usa EXACTAMENTE la traducción indicada.",
        "",
        "Marcadores de protección:",
        "- Existen tokens especiales como '<PROTECT_...>'.",
        "- NUNCA traduzcas, modifiques, reordenes ni elimines esos tokens.",
        "- Deben aparecer en la salida exactamente igual que en la entrada.",
        "",
        "Nombres propios:",
        "- No traduzcas nombres propios de personas ni lugares específicos.",
        "- Traduce términos de poderes, habilidades, técnicas, niveles, estados y conceptos del sistema, salvo que estén en la lista de 'never_translate'.",
        "",
        "No intentes mejorar el estilo. Concéntrate en la exactitud y consistencia.",
        f"Título del capítulo (referencia, sin necesidad de respetar formato): {title_en}",
    ]
    if glossary.never_translate:
        rules.append("")
        rules.append("Términos que NO se traducen: " + ", ".join(sorted(set(glossary.never_translate))))
    if glossary.translations:
        pairs = "; ".join([f"{k} => {v}" for k, v in glossary.translations.items()])
        rules.append("")
        rules.append("Glosario de traducciones forzadas: " + pairs)
    return "\n".join(rules)


def build_stage2_prompt(glossary: Glossary) -> str:
    rules = [
        "Eres un editor profesional de textos en español neutro, estándar para América.",
        "Recibes un borrador de traducción que YA está en español.",
        "Tu tarea es refinar y pulir el texto SIN cambiar el contenido.",
        "",
        "Objetivo:",
        "- Mejorar fluidez, naturalidad y legibilidad del español.",
        "- Corregir errores gramaticales, de ortografía y puntuación.",
        "- Mantener el tono narrativo original.",
        "",
        "Variante de español:",
        "- Mantén registro neutro habitual en América. Evita 'vosotros' y giros peninsulares como 'vale', 'coger' cuando no correspondan.",
        "- Prefiere segunda persona plural como 'ustedes' cuando sea necesario.",
        "",
        "Prohibido:",
        "- No traduzcas desde otro idioma: asume que el español es la única referencia.",
        "- No añadas información nueva, explicaciones ni descripciones extra.",
        "- No elimines frases ni resumas el contenido.",
        "- No cambies el orden de las escenas ni de las frases.",
        "",
        "Estructura:",
        "- Conserva los párrafos y líneas en blanco exactamente como están.",
        "- Mantén el formato de diálogos (guiones, comillas, saltos de línea).",
        "",
        "Glosario:",
        "- Existe un glosario de términos que debes respetar.",
        "- Si un término aparece en el texto con una forma que difiere del 'target' del glosario, ajústalo a la forma EXACTA del glosario.",
        "",
        "Tokens protegidos:",
        "- No modifiques, traduzcas ni elimines tokens con el patrón '<PROTECT_...>'.",
        "- Deben salir idénticos.",
    ]
    if glossary.never_translate:
        rules.append("")
        rules.append("Términos que NO se modifican: " + ", ".join(sorted(set(glossary.never_translate))))
    if glossary.translations:
        pairs = "; ".join([f"{k} => {v}" for k, v in glossary.translations.items()])
        rules.append("")
        rules.append("Glosario de traducciones forzadas: " + pairs)
    return "\n".join(rules)


def _ollama_chat(
    base_url: str,
    model: str,
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.2,
    timeout: Optional[float] = 300.0,
) -> str:
    url = urljoin(base_url.rstrip("/") + "/", "api/chat")
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    connect_timeout = 10.0
    read_timeout = timeout if timeout else 300.0
    resp = requests.post(url, json=payload, timeout=(connect_timeout, read_timeout))
    resp.raise_for_status()
    data = resp.json()
    msg = (data or {}).get("message", {}) or {}
    return (msg.get("content", "") or "").strip()


async def translate_chunk_ollama_async(
    base_url: str,
    model: str,
    system_prompt: str,
    text: str,
    *,
    temperature: float = 0.2,
    retries: int = 3,
    backoff: float = 1.8,
    api_timeout: Optional[float] = 300.0,
) -> str:
    loop = asyncio.get_running_loop()
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            return await loop.run_in_executor(
                None,
                lambda: _ollama_chat(
                    base_url,
                    model,
                    [
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": "Traduce fielmente el siguiente fragmento. Responde ÚNICAMENTE en español actual y natural. Devuelve SOLO el texto traducido, sin notas ni etiquetas.\n\n" + text,
                        },
                    ],
                    temperature=temperature,
                    timeout=api_timeout,
                ),
            )
        except Exception as e:
            last_err = e
            error_msg = str(e)
            if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                if attempt < retries:
                    wait_time = backoff ** (attempt - 1)
                    print(f"[warn] Timeout en intento {attempt}/{retries}, reintentando en {wait_time:.1f}s...", flush=True)
                    await asyncio.sleep(wait_time)
                else:
                    raise RuntimeError(f"Timeout después de {retries} intentos. Ollama está tardando más de {api_timeout}s. Considera aumentar --api-timeout o reducir --chunk-chars.")
            elif attempt < retries:
                wait_time = backoff ** (attempt - 1)
                print(f"[warn] Error en intento {attempt}/{retries}: {error_msg[:100]}, reintentando en {wait_time:.1f}s...", flush=True)
                await asyncio.sleep(wait_time)
            else:
                break
    raise RuntimeError(f"Fallo al traducir chunk (ollama) después de {retries} intentos: {last_err}")


async def refine_chunk_gpt_async(
    client: "AsyncOpenAI",
    model: str,
    system_prompt: str,
    text: str,
    *,
    temperature: float = 0.3,
    retries: int = 3,
    backoff: float = 1.8,
    api_timeout: Optional[float] = 120.0,
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
                        "content": "Refina y mejora el siguiente texto en español. Mantén un registro neutro (América), actual y natural. Devuelve SOLO el texto refinado, sin notas ni etiquetas.\n\n" + text,
                    },
                ],
                timeout=api_timeout,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            last_err = e
            if attempt < retries:
                await asyncio.sleep(backoff ** (attempt - 1))
            else:
                break
    raise RuntimeError(f"Fallo al refinar chunk (gpt): {last_err}")


async def stage1_translate_async(
    base_url: str,
    model: str,
    title_en: str,
    paragraphs: List[str],
    glossary: Glossary,
    ingest_glossary: IngestGlossary,
    *,
    chunk_chars: int = 5000,
    temperature: float = 0.2,
    api_timeout: Optional[float] = 120.0,
    verbose: bool = False,
    max_concurrent: int = 2,
) -> Tuple[str, List[str]]:
    start_time = time.time()
    system_prompt = build_stage1_prompt(title_en, glossary)
    
    title_src = apply_ingest_replacements(title_en, ingest_glossary)
    title_src = protect_text(title_src, glossary)
    
    chunks = chunk_paragraphs(paragraphs, max_chars=chunk_chars)
    
    if verbose:
        t0 = time.time()
        print(f"[stage1] Traduciendo título (ollama)...", flush=True)
    title_draft = await translate_chunk_ollama_async(
        base_url, model, system_prompt, title_src, temperature=temperature, api_timeout=api_timeout
    )
    if verbose:
        print(f"[stage1] Título traducido en {time.time() - t0:.1f}s", flush=True)
    title_draft = restore_text(title_draft, glossary)
    title_draft = apply_postprocess(title_draft, glossary)
    
    translated_paragraphs = []
    
    if chunks:
        if verbose:
            print(f"[stage1] Traduciendo {len(chunks)} chunks en paralelo (ollama, max {max_concurrent})...", flush=True)
        
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def translate_single_chunk(i: int, chunk: List[str]) -> Tuple[int, List[str]]:
            text = "\n\n".join(chunk)
            text_src = apply_ingest_replacements(text, ingest_glossary)
            text_src = protect_text(text_src, glossary)
            if verbose:
                t0 = time.time()
                print(f"[stage1] Iniciando chunk {i+1}/{len(chunks)} ({len(text)} chars)...", flush=True)
            out = await translate_chunk_ollama_async(
                base_url, model, system_prompt, text_src, temperature=temperature, api_timeout=api_timeout
            )
            if verbose:
                print(f"[stage1] Chunk {i+1} completado en {time.time() - t0:.1f}s", flush=True)
            out = restore_text(out, glossary)
            out = apply_postprocess(out, glossary)
            out_pars = [p.strip() for p in out.split("\n\n") if p.strip()]
            return i, out_pars
        
        async def translate_with_semaphore(i: int, chunk: List[str]) -> Tuple[int, List[str]]:
            async with semaphore:
                return await translate_single_chunk(i, chunk)
        
        tasks = [translate_with_semaphore(i, chunk) for i, chunk in enumerate(chunks)]
        results = await asyncio.gather(*tasks)
        results.sort(key=lambda x: x[0])
        for _, out_pars in results:
            translated_paragraphs.extend(out_pars)
    
    if verbose:
        total_time = time.time() - start_time
        print(f"[stage1] Capítulo completo traducido en {total_time:.1f}s ({total_time/60:.1f} min)", flush=True)
    
    return title_draft, translated_paragraphs


async def stage2_refine_async(
    client: "AsyncOpenAI",
    model: str,
    title_draft: str,
    paragraphs_draft: List[str],
    glossary: Glossary,
    *,
    chunk_chars: int = 5000,
    temperature: float = 0.3,
    api_timeout: Optional[float] = 120.0,
    verbose: bool = False,
    max_concurrent: int = 2,
) -> Tuple[str, List[str]]:
    start_time = time.time()
    system_prompt = build_stage2_prompt(glossary)
    
    title_src = protect_text(title_draft, glossary)
    
    chunks = chunk_paragraphs(paragraphs_draft, max_chars=chunk_chars)
    
    if verbose:
        t0 = time.time()
        print(f"[stage2] Refinando título (gpt)...", flush=True)
    title_refined = await refine_chunk_gpt_async(
        client, model, system_prompt, title_src, temperature=temperature, api_timeout=api_timeout
    )
    if verbose:
        print(f"[stage2] Título refinado en {time.time() - t0:.1f}s", flush=True)
    title_refined = restore_text(title_refined, glossary)
    title_refined = apply_postprocess(title_refined, glossary)
    
    refined_paragraphs = []
    
    if chunks:
        if verbose:
            print(f"[stage2] Refinando {len(chunks)} chunks en paralelo (gpt, max {max_concurrent})...", flush=True)
        
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def refine_single_chunk(i: int, chunk: List[str]) -> Tuple[int, List[str]]:
            text = "\n\n".join(chunk)
            text_src = protect_text(text, glossary)
            if verbose:
                t0 = time.time()
                print(f"[stage2] Iniciando chunk {i+1}/{len(chunks)} ({len(text)} chars)...", flush=True)
            out = await refine_chunk_gpt_async(
                client, model, system_prompt, text_src, temperature=temperature, api_timeout=api_timeout
            )
            if verbose:
                print(f"[stage2] Chunk {i+1} completado en {time.time() - t0:.1f}s", flush=True)
            out = restore_text(out, glossary)
            out = apply_postprocess(out, glossary)
            out_pars = [p.strip() for p in out.split("\n\n") if p.strip()]
            return i, out_pars
        
        async def refine_with_semaphore(i: int, chunk: List[str]) -> Tuple[int, List[str]]:
            async with semaphore:
                return await refine_single_chunk(i, chunk)
        
        tasks = [refine_with_semaphore(i, chunk) for i, chunk in enumerate(chunks)]
        results = await asyncio.gather(*tasks)
        results.sort(key=lambda x: x[0])
        for _, out_pars in results:
            refined_paragraphs.extend(out_pars)
    
    if verbose:
        total_time = time.time() - start_time
        print(f"[stage2] Capítulo completo refinado en {total_time:.1f}s ({total_time/60:.1f} min)", flush=True)
    
    return title_refined, refined_paragraphs


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


def load_env_file(env_path: Optional[Path]) -> None:
    if not env_path or not env_path.exists():
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
        pass


def ensure_gpt_client() -> "AsyncOpenAI":
    if AsyncOpenAI is None:
        raise RuntimeError("El paquete 'openai' no está instalado. Ejecuta: pip install -r requirements.txt")
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Falta OPENAI_API_KEY en el entorno. Exporta tu clave o usa --env-file.")
    return AsyncOpenAI()


async def translate_chapter_hybrid_async(
    base_url: str,
    ollama_model: str,
    gpt_client: "AsyncOpenAI",
    gpt_model: str,
    title_en: str,
    paragraphs: List[str],
    glossary: Glossary,
    ingest_glossary: IngestGlossary,
    *,
    chunk_chars: int = 5000,
    ollama_temp: float = 0.2,
    gpt_temp: float = 0.3,
    api_timeout: Optional[float] = 120.0,
    verbose: bool = False,
    max_concurrent: int = 2,
) -> Tuple[str, List[str]]:
    title_draft, paragraphs_draft = await stage1_translate_async(
        base_url,
        ollama_model,
        title_en,
        paragraphs,
        glossary,
        ingest_glossary,
        chunk_chars=chunk_chars,
        temperature=ollama_temp,
        api_timeout=api_timeout,
        verbose=verbose,
        max_concurrent=max_concurrent,
    )
    
    title_refined, paragraphs_refined = await stage2_refine_async(
        gpt_client,
        gpt_model,
        title_draft,
        paragraphs_draft,
        glossary,
        chunk_chars=chunk_chars,
        temperature=gpt_temp,
        api_timeout=api_timeout,
        verbose=verbose,
        max_concurrent=max_concurrent,
    )
    
    return title_refined, paragraphs_refined


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Pipeline híbrido: Ollama (borrador) + GPT (refinamiento)")
    parser.add_argument("--input-dir", default="output/tribulation", help="Directorio con capítulos en inglés")
    parser.add_argument("--start", type=int, default=1, help="Capítulo inicial (incluido)")
    parser.add_argument("--end", type=int, default=0, help="Capítulo final (incluido). 0 = inferir de index.jsonl")
    parser.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL, help="Modelo Ollama para Stage 1")
    parser.add_argument("--gpt-model", default=DEFAULT_GPT_MODEL, help="Modelo GPT para Stage 2")
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"), help="URL base de Ollama")
    parser.add_argument("--ollama-temp", type=float, default=0.2, help="Temperature para Ollama (Stage 1)")
    parser.add_argument("--gpt-temp", type=float, default=0.3, help="Temperature para GPT (Stage 2)")
    parser.add_argument("--chunk-chars", type=int, default=5000, help="Tamaño aproximado de chunk en caracteres (default: 5000 para M4)")
    parser.add_argument("--max-concurrent", type=int, default=2, help="Máximo número de chunks a procesar en paralelo (default: 2 para M4)")
    parser.add_argument("--max-concurrent-chapters", type=int, default=1, help="Máximo número de capítulos a procesar en paralelo (default: 1 para evitar saturar Ollama)")
    parser.add_argument("--resume", action="store_true", help="Omitir capítulos ya traducidos")
    parser.add_argument("--glossary", default="config/translation_glossary.json", help="Ruta a glosario de traducción JSON")
    parser.add_argument("--ingest-glossary", default="config/ingest_glossary.json", help="Ruta a glosario de ingest JSON")
    parser.add_argument("--output-dir", default="traduccion", help="Directorio de salida para los archivos traducidos (default: traduccion)")
    parser.add_argument("--env-file", default=".env", help="Ruta a archivo .env con OPENAI_API_KEY")
    parser.add_argument("--verbose", action="store_true", help="Imprimir pasos detallados de progreso")
    parser.add_argument("--debug", action="store_true", help="Logs verbosos y trazas de error")
    parser.add_argument("--api-timeout", type=float, default=300.0, help="Timeout por request a la API (segundos, default: 300 para Ollama)")
    parser.add_argument("--skip-stage1", action="store_true", help="Omitir Stage 1 (usar archivos *_draft_es.txt existentes)")
    parser.add_argument("--skip-stage2", action="store_true", help="Omitir Stage 2 (solo ejecutar Stage 1)")

    args = parser.parse_args(argv)

    in_dir = Path(args.input_dir)
    if not in_dir.exists():
        print(f"[error] No existe input-dir: {in_dir}", file=sys.stderr)
        return 2

    idx = load_index(in_dir / "index.jsonl")
    if not idx:
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

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.verbose:
        print(f"[out] Carpeta de salida: {out_dir}", flush=True)

    out_index_path = out_dir / "index_es.jsonl"
    out_index_file = out_index_path.open("a", encoding="utf-8")

    glossary_path = Path(args.glossary) if args.glossary else None
    glossary = Glossary.load(glossary_path)
    glossary.ensure_placeholders()

    ingest_glossary_path = Path(args.ingest_glossary) if args.ingest_glossary else None
    ingest_glossary = IngestGlossary.load(ingest_glossary_path)

    env_path = Path(args.env_file) if args.env_file else None
    load_env_file(env_path)
    if args.verbose:
        print(f"[env] Cargado .env desde: {env_path} (existe={env_path.exists() if env_path else False})", flush=True)

    gpt_client: Optional["AsyncOpenAI"] = None
    if not args.skip_stage2:
        gpt_client = ensure_gpt_client()

    if args.verbose:
        print(f"[cfg] Ollama URL: {args.ollama_url} | Modelo: {args.ollama_model}", flush=True)
        if not args.skip_stage2:
            print(f"[cfg] GPT Modelo: {args.gpt_model}", flush=True)
        print(f"[cfg] chunk-chars: {args.chunk_chars} | ollama-temp: {args.ollama_temp} | gpt-temp: {args.gpt_temp}", flush=True)
        print(f"[cfg] max-concurrent (chunks): {args.max_concurrent} | max-concurrent-chapters: {args.max_concurrent_chapters}", flush=True)
        print(f"[cfg] skip-stage1: {args.skip_stage1} | skip-stage2: {args.skip_stage2}", flush=True)

    async def process_chapter(n: int) -> None:
        es_name = f"{str(n).zfill(4)}_es.txt"
        draft_name = f"{str(n).zfill(4)}_draft_es.txt"
        in_path = in_dir / f"{str(n).zfill(4)}_en.txt"
        draft_path = out_dir / draft_name
        out_path = out_dir / es_name
        
        if args.resume and out_path.exists():
            print(f"[skip] {n} ya traducido ({es_name})", flush=True)
            return
        
        if not in_path.exists():
            print(f"[miss] {n} no existe ({in_path.name})", file=sys.stderr)
            return

        try:
            title_en, paragraphs = read_chapter(in_path)
            if args.verbose:
                print(f"[chap] {n}: leído '{in_path.name}' | párrafos: {len(paragraphs)}", flush=True)

            if args.skip_stage1:
                if not draft_path.exists():
                    print(f"[error] {n}: No existe borrador ({draft_name}) y --skip-stage1 está activo", file=sys.stderr)
                    return
                title_draft, paragraphs_draft = read_chapter(draft_path)
                if args.verbose:
                    print(f"[stage1] {n}: Usando borrador existente ({draft_name})", flush=True)
            else:
                title_draft, paragraphs_draft = await stage1_translate_async(
                    args.ollama_url,
                    args.ollama_model,
                    title_en,
                    paragraphs,
                    glossary,
                    ingest_glossary,
                    chunk_chars=args.chunk_chars,
                    temperature=args.ollama_temp,
                    api_timeout=args.api_timeout,
                    verbose=args.verbose,
                    max_concurrent=args.max_concurrent,
                )
                if args.skip_stage2:
                    write_chapter_es(out_dir, n, title_draft, paragraphs_draft)
                    print(f"[ok] {n}: {es_name} (solo Stage 1)", flush=True)
                    return
                body_draft = "\n\n".join(paragraphs_draft)
                with draft_path.open("w", encoding="utf-8") as f:
                    f.write(title_draft.strip() + "\n\n" + body_draft + "\n")
                if args.verbose:
                    print(f"[stage1] {n}: Borrador guardado como {draft_name}", flush=True)

            if args.skip_stage2:
                return

            if gpt_client is None:
                raise RuntimeError("Cliente GPT no inicializado")

            title_refined, paragraphs_refined = await stage2_refine_async(
                gpt_client,
                args.gpt_model,
                title_draft,
                paragraphs_draft,
                glossary,
                chunk_chars=args.chunk_chars,
                temperature=args.gpt_temp,
                api_timeout=args.api_timeout,
                verbose=args.verbose,
                max_concurrent=args.max_concurrent,
            )

            if args.verbose:
                print(f"[write] {n}: Título refinado: '{title_refined[:80]}'...", flush=True)
                print(f"[write] {n}: Párrafos refinados: {len(paragraphs_refined)}", flush=True)

            saved = write_chapter_es(out_dir, n, title_refined, paragraphs_refined)
            rec = {
                "number": n,
                "title_en": title_en,
                "title_es": title_refined,
                "file_en": in_path.name,
                "file_es": saved,
                "input_dir": str(in_dir),
                "output_dir": str(out_dir),
                "length_en": sum(len(p) for p in paragraphs),
                "length_es": sum(len(p) for p in paragraphs_refined),
                "ollama_model": args.ollama_model,
                "gpt_model": args.gpt_model,
                "translated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            out_index_file.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out_index_file.flush()
            print(f"[ok] {n}: {es_name}", flush=True)
        except Exception as e:
            print(f"[err] {n}: {e}", file=sys.stderr)
            if args.debug:
                traceback.print_exc()

    async def run_all():
        chapter_semaphore = asyncio.Semaphore(args.max_concurrent_chapters)
        
        async def process_with_semaphore(n: int):
            async with chapter_semaphore:
                await process_chapter(n)
        
        tasks = [process_with_semaphore(n) for n in range(start, end + 1)]
        await asyncio.gather(*tasks)

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        print("\n[interrupted] Proceso cancelado por el usuario", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"[fatal] {e}", file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        return 1

    out_index_file.close()
    print("[done] Traducción completada.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
