#!/usr/bin/env python3
"""
Post-translation polish layer for CN→ES translations.

Two layers:
  1. Regex cleanup — free, instant: strips leaked Chinese chars, fixes
     ellipsis-only paragraphs, rotates repetitive phrases, fixes glossary
     violations, removes leaked English, integrates single-word calco
     paragraphs, cleans author metadata from titles.
  2. LLM polish  — optional second pass (ES→ES) to naturalise Spanish,
     with glossary injected so the LLM doesn't reintroduce forbidden terms.
"""

import logging
import re
from typing import Optional

# ---------------------------------------------------------------------------
# Layer 1: Regex cleanup
# ---------------------------------------------------------------------------

# CJK Unified Ideographs + Extension A (covers 99 % of novel text)
_CHINESE_CHAR = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")

# Paragraphs that are nothing but dots / ellipsis
_ELLIPSIS_ONLY = re.compile(r"^[.…·。]{3,}$")

# ---------------------------------------------------------------------------
# Glossary enforcement (forbidden → correct)
# ---------------------------------------------------------------------------

_GLOSSARY_FIXES: list[tuple[re.Pattern, str]] = [
    # "fusión/unión/fusion del Dao" variants → Daofuse
    (re.compile(r"(?:la\s+)?fusi[oó]n\s+del\s+Dao", re.IGNORECASE), "Daofuse"),
    (re.compile(r"(?:la\s+)?uni[oó]n\s+del\s+Dao", re.IGNORECASE), "Daofuse"),
    (re.compile(r"Dao\s+fusionado", re.IGNORECASE), "Daofuse"),
    (re.compile(r"Dao\s+unido", re.IGNORECASE), "Daofuse"),
    # "se había Daofuse" / "se Daofuse" → "había alcanzado el Daofuse" etc.
    (re.compile(r"se\s+hab[ií]a\s+Daofuse", re.IGNORECASE), "había alcanzado el Daofuse"),
    (re.compile(r"se\s+Daofuse", re.IGNORECASE), "alcanzó el Daofuse"),
    (re.compile(r"uno\s+se\s+Daofuse", re.IGNORECASE), "uno alcanzara el Daofuse"),
    # Leaked English titles
    (re.compile(r"\bLord\b"), "Señor"),
    (re.compile(r"\bRumble\b", re.IGNORECASE), "¡Retumbó!"),
    (re.compile(r"\bBoom\b", re.IGNORECASE), "¡Bum!"),
    # "señor de las reglas" → "Maestro de las Leyes"
    (re.compile(r"[Ss]eñor\s+de\s+las\s+reglas"), "Maestro de las Leyes"),
    # "Rey del Cielo" → "Rey Celestial"  (only when standalone title, not part of compound)
    (re.compile(r"Rey\s+del\s+Cielo\b"), "Rey Celestial"),
]

# ---------------------------------------------------------------------------
# Muletilla rotation — comprehensive list including LLM-generated variants
# ---------------------------------------------------------------------------

_PHRASE_VARIANTS: dict[str, list[str]] = {
    # -- Temporal fillers (the #1 problem) --
    "en ese momento": [
        "Justo entonces",
        "En aquel instante",
        "Al punto",
        "En ese preciso instante",
        "Fue entonces cuando",
        "Acto seguido",
    ],
    "en aquel momento": [
        "Justo entonces",
        "En ese instante",
        "Al punto",
        "Fue entonces cuando",
        "Acto seguido",
    ],
    "en ese instante": [
        "Justo entonces",
        "En aquel momento",
        "Al punto",
        "Fue entonces cuando",
    ],
    "en aquel instante": [
        "Justo entonces",
        "En ese momento",
        "Fue entonces cuando",
        "Al punto",
    ],
    # -- Dialogue verbs --
    "dijo con voz grave": [
        "respondió con gravedad",
        "declaró en tono sombrío",
        "sentenció con seriedad",
    ],
    "dijo con voz profunda": [
        "respondió con firmeza",
        "pronunció con solemnidad",
        "declaró con gravedad",
    ],
    # -- Facial expressions --
    "sonrió levemente": [
        "esbozó una leve sonrisa",
        "dejó asomar una sonrisa",
        "sonrió apenas",
    ],
    "asintió levemente": [
        "asintió con un leve gesto",
        "inclinó la cabeza ligeramente",
        "hizo un breve gesto de asentimiento",
    ],
    "frunció el ceño": [
        "arrugó el entrecejo",
        "torció el gesto",
        "endureció la expresión",
        "apretó los labios",
    ],
    # -- Stock reactions --
    "no pudo evitar": [
        "no logró contenerse y",
        "fue incapaz de evitar",
        "le resultó imposible no",
    ],
    "sin decir una palabra": [
        "sin mediar palabra",
        "en silencio",
        "sin pronunciar palabra",
    ],
    "sacudió la cabeza": [
        "negó con la cabeza",
        "meneó la cabeza",
        "hizo un gesto de negación",
    ],
    "abrió mucho los ojos": [
        "sus ojos se abrieron de par en par",
        "abrió los ojos como platos",
        "sus pupilas se dilataron",
    ],
    "se quedó sin palabras": [
        "enmudeció",
        "no supo qué decir",
        "se quedó mudo",
    ],
    "tomó una respiración profunda": [
        "respiró hondo",
        "inhaló profundamente",
        "tomó aire",
    ],
    "respiró profundamente": [
        "respiró hondo",
        "inhaló con fuerza",
        "tomó una bocanada de aire",
    ],
}

# Accent-insensitive pattern building
_ACCENT_MAP = {
    "á": "[aá]", "é": "[eé]", "í": "[ií]", "ó": "[oó]", "ú": "[uú]",
    "a": "[aá]", "e": "[eé]", "i": "[ií]", "o": "[oó]", "u": "[uú]",
}

def _accent_insensitive_pattern(text: str) -> str:
    """Build a regex pattern that matches with or without Spanish accents."""
    escaped = re.escape(text)
    result = []
    i = 0
    while i < len(escaped):
        # re.escape may insert backslashes — get the actual char
        if escaped[i] == "\\" and i + 1 < len(escaped):
            result.append(escaped[i:i+2])
            i += 2
        else:
            ch = escaped[i]
            result.append(_ACCENT_MAP.get(ch, ch))
            i += 1
    return "".join(result)


# Pre-compile patterns (case-insensitive, accent-insensitive)
_COMPILED_PHRASES: list[tuple[re.Pattern, str, list[str]]] = []
for _key, _variants in _PHRASE_VARIANTS.items():
    _pat = re.compile(_accent_insensitive_pattern(_key), re.IGNORECASE)
    _COMPILED_PHRASES.append((_pat, _key, _variants))

# ---------------------------------------------------------------------------
# Single-word calco paragraphs — patterns to integrate into surrounding text
# ---------------------------------------------------------------------------

# Paragraphs that are just a single exclamation/word (calco from Chinese)
# These should be merged into the preceding paragraph.
_SINGLE_WORD_CALCO = re.compile(
    r"^[¡!¿?]*"
    r"(?:Muerte|Matad|Maldici[oó]n|Imposible|Silencio|Ira|Vulgar|"
    r"Bastardo|Monstruo|Poderoso|Suprimir|Curaci[oó]n|Venganza|"
    r"Familiares|Retumbando|Puf|Bum|Boom|Crack|Rumble|"
    r"Sin palabras|De acuerdo|Interesante|Incre[ií]ble|"
    r"No|Desviaci[oó]n|Escuchadle|Mierda|Cobarde|Ret[ií]rate|"
    r"Deteneos|Cuidado|Peligro|Atacad|Huid|Avanzad|"
    r"Excelente|Perfecto|Ridículo|Rid[ií]culo|Absurdo|"
    r"Asombroso|Extraordinario|Formidable)"
    r"[.!?¡¿]*$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Author metadata in titles
# ---------------------------------------------------------------------------

_TITLE_AUTHOR_NOISE = re.compile(
    r"\s*[（(]"
    r"[^）)]*?"
    r"(?:actualizaci[oó]n|actualizar|update|hoy|mañana|solo habr[aá])"
    r"[^）)]*?"
    r"[）)]",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def clean_leaked_chinese(text: str) -> str:
    """Remove stray Chinese characters that leaked through translation."""
    return _CHINESE_CHAR.sub("", text)


def clean_ellipsis_paragraphs(text: str) -> str:
    """Remove paragraphs that are nothing but ellipsis dots."""
    lines = text.split("\n\n")
    cleaned = [ln for ln in lines if not _ELLIPSIS_ONLY.match(ln.strip())]
    return "\n\n".join(cleaned)


def fix_glossary_terms(text: str) -> str:
    """Fix known glossary violations via regex substitution."""
    for pattern, replacement in _GLOSSARY_FIXES:
        text = pattern.sub(replacement, text)
    return text


def vary_repetitive_phrases(text: str) -> str:
    """Rotate repetitive stock phrases with alternatives.

    Each phrase key is allowed to appear once as-is; subsequent occurrences
    cycle through the variant list.
    """
    counters: dict[str, int] = {}

    for pattern, key, variants in _COMPILED_PHRASES:
        matches = list(pattern.finditer(text))
        if len(matches) <= 1:
            continue

        # Replace from last to first so indices stay valid
        for match in reversed(matches):
            count = counters.get(key, 0)
            counters[key] = count + 1

            if count == 0:
                continue  # Keep first occurrence

            replacement = variants[(count - 1) % len(variants)]

            # Preserve capitalisation
            original = match.group(0)
            if original[0].isupper() and replacement[0].islower():
                replacement = replacement[0].upper() + replacement[1:]
            elif original[0].islower() and replacement[0].isupper():
                replacement = replacement[0].lower() + replacement[1:]

            text = text[: match.start()] + replacement + text[match.end() :]

    return text


def integrate_single_word_paragraphs(text: str) -> str:
    """Merge single-word calco paragraphs into the preceding paragraph.

    A paragraph like "¡Muerte!" becomes appended to the previous paragraph
    as " —¡Muerte!" to integrate it into the narrative flow.
    """
    paragraphs = text.split("\n\n")
    if len(paragraphs) <= 1:
        return text

    result: list[str] = []
    for p in paragraphs:
        stripped = p.strip()
        if _SINGLE_WORD_CALCO.match(stripped) and result:
            # Ensure it starts with ¡ or ¿ if it has ! or ?
            if stripped.endswith("!") and not stripped.startswith("¡"):
                stripped = "¡" + stripped
            if stripped.endswith("?") and not stripped.startswith("¿"):
                stripped = "¿" + stripped
            # Append to previous paragraph
            result[-1] = result[-1].rstrip() + " —" + stripped
        else:
            result.append(p)

    return "\n\n".join(result)


def clean_title(title: str) -> str:
    """Remove author metadata noise from translated chapter titles."""
    return _TITLE_AUTHOR_NOISE.sub("", title).strip()


def regex_cleanup(text: str) -> str:
    """Apply per-paragraph regex cleanups (safe to call on individual paragraphs)."""
    text = clean_leaked_chinese(text)
    text = clean_ellipsis_paragraphs(text)
    text = fix_glossary_terms(text)
    # Collapse double spaces that may result from Chinese removal
    text = re.sub(r"  +", " ", text)
    # Remove empty lines that result from cleanup
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def regex_cleanup_chapter(paragraphs: list[str]) -> list[str]:
    """Apply full-chapter regex cleanups that need cross-paragraph context.

    This handles: per-paragraph cleanup + phrase rotation + single-word
    paragraph integration. Must be called on the FULL list of paragraphs
    so repetition detection works across the whole chapter.
    """
    # Step 1: per-paragraph cleanup
    cleaned = [regex_cleanup(p) for p in paragraphs if p.strip()]

    # Step 2: join, apply chapter-wide patterns, split back
    full_text = "\n\n".join(cleaned)
    full_text = vary_repetitive_phrases(full_text)
    full_text = integrate_single_word_paragraphs(full_text)
    full_text = re.sub(r"\n{3,}", "\n\n", full_text)

    return [p.strip() for p in full_text.split("\n\n") if p.strip()]


# ---------------------------------------------------------------------------
# Layer 2: LLM polish (ES → ES)
# ---------------------------------------------------------------------------

_POLISH_SYSTEM_PROMPT_BASE = """\
Eres un editor literario profesional de novelas en español.

Tu tarea es PULIR el siguiente texto ya traducido. NO cambies el contenido,
la trama ni los nombres propios. Solo mejora la fluidez y naturalidad del español.

REGLAS:
1. VARÍA las construcciones repetitivas:
   - Si "en ese momento" o variantes temporales aparecen más de 2 veces,
     REEMPLAZA los excedentes por alternativas naturales: "justo entonces",
     "fue entonces cuando", "al punto", "acto seguido", o ELIMINA la
     muletilla si la oración funciona sin ella.
   - Si "dijo con voz grave/profunda" se repite, usa: "respondió con gravedad",
     "declaró en tono sombrío", "sentenció", "pronunció con solemnidad", etc.
   - Si "[Nombre] sonrió/frunció el ceño" se repite, varía la expresión.
2. REESTRUCTURA oraciones que suenen a calco del chino:
   - Combina frases telegráficas cortas en oraciones compuestas.
   - Usa subordinadas, gerundios y participios para dar fluidez.
   - Convierte secuencias de "[Verbo], [Verbo], [Verbo]" en prosa fluida.
3. INTEGRA párrafos de una sola palabra/exclamación en la narración:
   - "¡Muerte!" como párrafo suelto → intégralo: "rugió pidiendo muerte" o
     "El grito de guerra resonó."
   - "¡Boom!" / "¡Puf!" → intégralo como descripción: "Una explosión retumbó"
     o "Un estallido sacudió el aire."
   - "Sin palabras." → "Se quedó sin palabras."
   - "De acuerdo." → intégralo como respuesta en el diálogo.
   - NUNCA dejes exclamaciones sueltas como párrafo independiente.
4. TÉRMINOS PROHIBIDOS — NUNCA uses estas expresiones:
   - NUNCA "fusión del Dao" ni "unión del Dao" → usa "Daofuse"
   - NUNCA "Lord" → usa "Señor" o el título español apropiado
   - NUNCA "Rumble" ni onomatopeyas en inglés → tradúcelas al español
   - NUNCA "Daofuse" como verbo ("se Daofuse") → "alcanzó el Daofuse"
   - NUNCA "señor de las reglas" → "Maestro de las Leyes"
{extra_forbidden}5. NO cambies: nombres propios en pinyin (Su Yu, Tiangu, etc.),
   términos del glosario (Daofuse, Rey Celestial, Maestro de las Leyes,
   Portador de la Llama, etc.), el sentido del texto, ni los saltos de
   párrafo principales.
6. NO agregues contenido nuevo ni elimines información.
7. Si un fragmento ya está bien escrito, déjalo intacto.
8. MÁXIMO 2 ocurrencias de "en ese momento" (y variantes) en todo el texto.
   Si hay más, REEMPLAZA o ELIMINA las sobrantes.

Devuelve SOLO el texto pulido, sin explicaciones ni comentarios."""


def _build_polish_prompt(forbidden_terms: Optional[dict[str, str]] = None) -> str:
    """Build the polish system prompt, optionally injecting extra forbidden terms."""
    extra = ""
    if forbidden_terms:
        lines = []
        for bad, good in forbidden_terms.items():
            lines.append(f'   - NUNCA "{bad}" → usa "{good}"')
        extra = "\n".join(lines) + "\n"
    return _POLISH_SYSTEM_PROMPT_BASE.format(extra_forbidden=extra)


async def polish_chapter(
    paragraphs: list[str],
    adapter,
    logger: logging.Logger,
    *,
    chunk_chars: int = 6000,
    temperature: float = 0.3,
    forbidden_terms: Optional[dict[str, str]] = None,
) -> list[str]:
    """Send already-translated paragraphs through a polish LLM pass.

    Processes the chapter in large chunks (~*chunk_chars*) so the LLM has
    enough context to detect repetitions and restructure calcos.

    *forbidden_terms*: optional dict of {bad_term: correct_term} to inject
    into the system prompt so the LLM avoids reintroducing them.
    """
    if not paragraphs:
        return paragraphs

    system_prompt = _build_polish_prompt(forbidden_terms)

    # Build chunks of ~chunk_chars
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for p in paragraphs:
        if current_len + len(p) > chunk_chars and current:
            chunks.append("\n\n".join(current))
            current = [p]
            current_len = len(p)
        else:
            current.append(p)
            current_len += len(p)
    if current:
        chunks.append("\n\n".join(current))

    logger.info("  Puliendo %d chunks (ES→ES)…", len(chunks))

    polished_paragraphs: list[str] = []

    for idx, chunk_text in enumerate(chunks):
        user_msg = (
            "Pule el siguiente texto traducido. Devuelve SOLO el texto pulido:\n\n"
            + chunk_text
        )
        try:
            raw = await adapter.translate_chunk(
                system_prompt,
                user_msg,
                temperature=temperature,
            )
            result_pars = [p.strip() for p in raw.split("\n\n") if p.strip()]
            polished_paragraphs.extend(result_pars)
            logger.info("  Polish chunk %d/%d completado.", idx + 1, len(chunks))
        except Exception as e:
            logger.warning("  Error puliendo chunk %d: %s — usando original.", idx + 1, e)
            polished_paragraphs.extend(
                [p.strip() for p in chunk_text.split("\n\n") if p.strip()]
            )

    return polished_paragraphs
