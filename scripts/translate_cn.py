#!/usr/bin/env python3
"""
Translate CN raw chapters -> Spanish directly (CN -> ES).

Usage:
    python scripts/translate_cn.py --start 2 --end 10
    python scripts/translate_cn.py --start 2 --end 10 --adapter openai
    python scripts/translate_cn.py --start 2 --end 999 --output traduccion_cn --resume

Input:  data/cn_raws/cn_XXXX.txt
Output: <output_dir>/cn_XXXX_es.txt   (default: traduccion_cn/)

Name consistency strategy:
  1. A small hardcoded CN->ES glossary covers universal concepts (cultivation
     realms, power levels, races, world names) that are stable across ALL chapters.
  2. Per-chapter name glossaries are auto-extracted by matching CN text against
     the aligned EN reference using a fast LLM call, then cached as JSON.
  3. Extracted EN names are converted to Spanish via rule-based title translation
     (King->Rey, Marquis->Marques, etc.) with an LLM fallback for ambiguous cases.
  4. The merged glossary (concepts + extracted names) is injected into the
     translation prompt — no raw EN prose blob needed.
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path

import yaml

# polish module is in the same directory
from polish import regex_cleanup_chapter, polish_chapter, clean_title, fix_glossary_terms

# -- Root discovery ------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

# -- Master name glossary (from build_master_glossary.py) ----------------------
MASTER_NAMES_PATH = ROOT / "data" / "master_names.json"

def _load_master_names() -> dict[str, str]:
    """Load master_names.json as CN→ES dict. Returns empty dict if missing."""
    if not MASTER_NAMES_PATH.exists():
        return {}
    try:
        with MASTER_NAMES_PATH.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return {cn: data["es"] for cn, data in raw.items() if "es" in data}
    except (json.JSONDecodeError, OSError, KeyError):
        return {}

MASTER_CN_TO_ES: dict[str, str] = _load_master_names()

# -- Hardcoded CN->ES glossary (universal concepts only) -----------------------
# These are terms that are consistent across ALL chapters and need specific
# Spanish translations.  Character names, place names, and titles are NOT here;
# they are auto-extracted per chapter from the aligned EN reference.
CN_TO_ES_CONCEPTS: dict[str, str] = {
    # -- Cultivation realms (境) ------------------------------------------------
    "开元境": "Reino Kaiyuan",
    "千钧境": "Reino Mil Jun",
    "万石境": "Reino Diez Mil Piedras",
    "腾空境": "Reino Cielo Abierto",
    "凌云境": "Reino Rompe Nubes",
    "山海境": "Reino Montaña y Mar",
    "日月境": "Reino Sol y Luna",
    "永恒境": "Reino Eterno",
    # Realm short forms (without 境)
    "千钧": "Mil Jun",
    "万石": "Diez Mil Piedras",
    "腾空": "Cielo Abierto",
    "凌云": "Rompe Nubes",
    "山海": "Montaña y Mar",
    "日月": "Sol y Luna",
    "永恒": "Eterno",
    # -- Power levels & ranks ---------------------------------------------------
    "合道": "Daofuse",
    "天王": "Rey Celestial",
    "半王": "pseudo rey",
    "侯": "marqués",
    "规则之主": "Maestro de las Leyes",
    "传火者": "Portador de la Llama",
    "传火": "Portador de la Llama",
    # -- Realm / world terms ----------------------------------------------------
    "仙界": "Reino Inmortal",
    "魔界": "Reino Demonio",
    "神界": "Reino Divino",
    "人境": "Reino Humano",
    "命界": "Reino del Destino",
    "死灵界域": "Reino de la Muerte",
    "死灵": "espíritu de la muerte",
    "上界": "Reino Superior",
    "下界": "Reino Inferior",
    "万界": "Diez Mil Reinos",
    "诸天万界": "Diez Mil Reinos",
    "诸天": "los cielos",
    # -- Races ------------------------------------------------------------------
    "万族": "las miríadas de razas",
    "仙族": "raza inmortal",
    "魔族": "raza demonio",
    "神族": "raza divina",
    "人族": "raza humana",
    "太古巨人族": "raza de gigantes primordiales",
    "天马族": "raza del caballo celestial",
    "山羚族": "raza del antílope de montaña",
    "玄铠族": "raza de la armadura mística",
    "云虎族": "raza del tigre de las nubes",
    "飞天虎族": "raza del tigre celestial volador",
    "食铁兽": "panda de hierro",
    "五行族": "tribu de los cinco elementos",
    "噬魂族": "raza devoradora de almas",
    "蛮牛族": "raza del toro bárbaro",
    # -- Key concepts -----------------------------------------------------------
    "意志力": "voluntad",
    "元气": "qi de origen",
    "精血": "esencia de sangre",
    "功法": "técnica de cultivo",
    "神文": "carácter divino",
    "战技": "técnica de combate",
    "源兵": "arma de origen",
    "源技": "técnica de origen",
    "封界": "sellar el reino",
    "破界": "romper el reino",
    "气运": "fortuna",
    "人主": "señor humano",
    "人主印": "sello del señor humano",
    "潮汐": "marea",
    "议员令": "orden de asambleísta",
    "战兵": "arma de guerra",
    "时间长河": "Río del Tiempo",
    "基道": "Dao fundamental",
    "本道": "pen dao",
    "归墟之地": "Tierra del Retorno",
    "镇守": "guardián",
    "规则": "reglas",
    "惩罚": "castigo",
    "劫": "tribulación",
}


# -- EN title/rank -> ES translation (LLM-based) -------------------------------

_GLOSSARY_TRANSLATION_PROMPT = """\
You are a professional literary translator for a xianxia novel.
Translate the following English proper nouns (titles, names, organizations) into Spanish.

RULES:
1. **Structure consistency**: 
   - "Great [Name] King" -> "Gran Rey [Name]" (e.g., Great Zhou King -> Gran Rey Zhou)
   - "[Name] King" -> "Rey [Name]"
   - "[Name] Marquis" -> "Marqués [Name]" or "Marqués de [Name]" (use "de" if it sounds more natural, e.g. "Abyss Marquis" -> "Marqués del Abismo")
   - "Pseudo Emperor" -> "Pseudo Emperador"
   - "Royal Consort" -> "Consorte Real"
2. **Grammar**: Use natural Spanish phrasing. 
   - "Stable Army Marquis" -> "Marqués del Ejército Estable" (NOT "Estable Ejército Marqués")
   - "Heavenly Fate Marquis" -> "Marqués del Destino Celestial"
3. **Pinyin**: Keep pure pinyin names unchanged (e.g., "Su Yu" -> "Su Yu").
4. **General**: Return a JSON object mapping the English term to the Spanish translation.

=== INPUT TERMS ===
{terms}

=== OUTPUT FORMAT ===
Return ONLY valid JSON:
{{
  "English Term": "Spanish Translation",
  ...
}}
"""

async def translate_glossary_to_es(
    cn_to_en: dict[str, str],
    adapter,
    logger: logging.Logger,
) -> dict[str, str]:
    """
    Translate extracted English names/titles to Spanish using the LLM.
    This avoids hardcoded rules and allows for context-aware translations.
    """
    if not cn_to_en:
        return {}

    # Skip CN terms already in master glossary (no need to re-translate)
    cn_to_en_filtered = {
        cn: en for cn, en in cn_to_en.items()
        if cn not in MASTER_CN_TO_ES
    }
    if not cn_to_en_filtered:
        logger.info("  All names already in master glossary, skipping LLM.")
        return {cn: MASTER_CN_TO_ES[cn] for cn in cn_to_en if cn in MASTER_CN_TO_ES}

    unique_en_terms = sorted(list(set(cn_to_en_filtered.values())))
    
    if not unique_en_terms:
         return {}

    # Batch terms if too many (simple chunking)
    chunk_size = 50
    en_to_es: dict[str, str] = {}
    
    for i in range(0, len(unique_en_terms), chunk_size):
        chunk = unique_en_terms[i:i + chunk_size]
        terms_str = "\n".join(f"- {term}" for term in chunk)
        
        prompt = _GLOSSARY_TRANSLATION_PROMPT.format(terms=terms_str)
        
        try:
            raw_response = await adapter.translate_chunk(
                "You are a translator helper. Return JSON.",
                prompt,
                temperature=0.0,
            )
            
            # Clean JSON
            text = raw_response.strip()
            text = re.sub(r"^```json\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                text = match.group(0)
                
            batch_mapping = json.loads(text)
            if isinstance(batch_mapping, dict):
                 en_to_es.update(batch_mapping)
                 
        except Exception as e:
            logger.warning(f"  Error translating glossary chunk: {e}")
            # Fallback: identity mapping for failed terms
            for term in chunk:
                if term not in en_to_es:
                    en_to_es[term] = term

    # Map back CN -> ES (master glossary terms + LLM-translated terms)
    cn_to_es: dict[str, str] = {}
    for cn, en in cn_to_en.items():
        if cn in MASTER_CN_TO_ES:
            cn_to_es[cn] = MASTER_CN_TO_ES[cn]
        else:
            cn_to_es[cn] = en_to_es.get(en, en)

    master_count = sum(1 for cn in cn_to_en if cn in MASTER_CN_TO_ES)
    logger.info("  Translated %d terms (%d master, %d LLM).",
                len(cn_to_es), master_count, len(cn_to_es) - master_count)
    return cn_to_es



# -- Helpers -------------------------------------------------------------------

def load_settings() -> dict:
    path = ROOT / "config" / "settings.yaml"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_translation_glossary(settings: dict) -> dict:
    """Returns {'never_translate': [...], 'translations': {...}}"""
    rel_path = settings.get("glossary", {}).get(
        "translation_glossary", "config/translation_glossary.json"
    )
    path = ROOT / rel_path
    if not path.exists():
        return {"never_translate": [], "translations": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    root = data.get("glossary", data)
    return {
        "never_translate": list(root.get("never_translate", []) or []),
        "translations": dict(root.get("translations", {}) or {}),
    }


def load_alignment_map() -> dict[int, list[int]]:
    """Returns {cn_num: [en_num, ...]} (each list sorted)."""
    path = ROOT / "data" / "alignment_map.json"
    with path.open("r", encoding="utf-8") as f:
        raw: dict = json.load(f)  # {en_num_str: cn_num}
    result: dict[int, list[int]] = {}
    for en_str, cn_num in raw.items():
        result.setdefault(int(cn_num), []).append(int(en_str))
    for nums in result.values():
        nums.sort()
    return result


# -- Chapter parsing -----------------------------------------------------------

_CHAPTER_END = re.compile(r"[（(]本章完[）)]")

_SKIP_PATTERNS = [
    re.compile(r"^\d{4}-\d{2}-\d{2}"),         # date line
    re.compile(r"作者[：:]"),                    # author credit
    re.compile(r"^Chapter\s+\d+:"),              # file index header
    re.compile(r"^\d+\.第\d+章"),                # numbered repeat "1.第1章..."
    re.compile(r"^PS[：:：]", re.IGNORECASE),    # author postscript notes
    re.compile(r"^[Pp]\.[Ss][.．][：:]?"),       # P.S. variants
]

# Patterns stripped from chapter titles (author begging for subs/votes)
_TITLE_NOISE = re.compile(
    r"[（(]"
    r"(?:万更|求订阅|求月票|求推荐票|加更|爆更|求追读|感谢|谢谢|更新)"
    r"[^）)]*"
    r"[）)]",
)


def parse_cn_chapter(path: Path) -> tuple[str, list[str]]:
    """
    Parse a CN chapter file.
    Returns (title_cn, paragraphs) where paragraphs are clean body lines.

    Everything after (本章完) is discarded -- those are author's post-chapter
    notes inserted by the platform, not narrative content.
    """
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()

    title = ""
    paragraphs: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Stop at chapter-end marker (everything after is platform/author notes)
        if _CHAPTER_END.search(stripped):
            break
        # Lines with an ideographic space (U+3000) in the raw indentation are
        # platform/author notes inserted by the source website -- not narrative.
        if "\u3000" in line:
            continue
        # Skip known metadata/noise patterns
        if any(p.search(stripped) for p in _SKIP_PATTERNS):
            continue
        # Capture chapter title (strip author noise like 万更求订阅)
        if not title and re.match(r"第\d+章", stripped):
            title = _TITLE_NOISE.sub("", stripped).strip()
            continue
        paragraphs.append(stripped)

    return title, paragraphs


def read_en_chapters_full(en_dir: Path, en_nums: list[int]) -> list[str]:
    """Read all aligned EN chapters (full text, no truncation)."""
    texts: list[str] = []
    for en_num in en_nums:
        path = en_dir / f"{en_num:04d}_en.txt"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").strip()
        if text:
            texts.append(text)
    return texts


# -- Name glossary extraction --------------------------------------------------

GLOSSARY_CACHE_DIR = ROOT / "data" / "name_glossary_cache"

_EXTRACTION_PROMPT = """\
You are a bilingual Chinese-English expert for the xianxia novel "万族之劫" (Tribulation of Myriad Races).

Given the CHINESE chapter text and the corresponding ENGLISH chapter text(s) below, extract ALL proper nouns that appear in both:
- Character names (e.g. 苏宇 → Su Yu)
- Place names (e.g. 南元 → Nanyuan)
- Titles/ranks with names (e.g. 百战王 → Hundred Battle King)
- Organization names (e.g. 文明学府 → Civilization Academy)
- Unique item/technique names

Match each Chinese term to its English equivalent as used in the English text.
Do NOT include generic cultivation terms (realms, qi, etc.) -- only proper nouns.

Return ONLY valid JSON: {{"chinese_term": "english_name", ...}}
No markdown, no explanation, just the JSON object.

=== CHINESE TEXT ===
{cn_text}

=== ENGLISH TEXT ===
{en_text}
"""


async def extract_name_glossary(
    cn_text: str,
    en_texts: list[str],
    adapter,
    cache_path: Path,
    logger: logging.Logger,
) -> dict[str, str]:
    """
    Extract CN->EN proper noun mappings from aligned chapter texts.
    Results are cached as JSON per chapter for reuse.
    """
    # Return cached result if available
    if cache_path.exists():
        try:
            with cache_path.open("r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, dict):
                logger.info("  Name glossary loaded from cache: %s", cache_path.name)
                return cached
        except (json.JSONDecodeError, OSError):
            pass  # Re-extract if cache is corrupted

    if not en_texts:
        logger.info("  No EN reference — skipping name extraction.")
        return {}

    en_combined = "\n\n---\n\n".join(en_texts)

    # Truncate inputs to keep extraction call fast but comprehensive
    cn_trimmed = cn_text[:8000]
    en_trimmed = en_combined[:12000]

    prompt = _EXTRACTION_PROMPT.format(cn_text=cn_trimmed, en_text=en_trimmed)

    try:
        raw_response = await adapter.translate_chunk(
            "You are a bilingual extraction assistant. Return only valid JSON.",
            prompt,
            temperature=0.0,
        )
    except Exception as e:
        logger.warning("  Name extraction error: %s", e)
        return {}

    # Parse JSON defensively (LLM may wrap in ```json ... ```)
    text = raw_response.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # Try to find a JSON object in the response
    json_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if json_match:
        text = json_match.group(0)

    try:
        glossary = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("  Could not parse extraction JSON: %.100s...", text)
        return {}

    if not isinstance(glossary, dict):
        logger.warning("  Extraction response is not a dict.")
        return {}

    # Filter out non-string values
    glossary = {k: v for k, v in glossary.items() if isinstance(k, str) and isinstance(v, str)}

    # Save to cache
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False, indent=2)

    logger.info("  Extracted %d CN->EN names (saved to cache).", len(glossary))
    return glossary


# -- Glossary building --------------------------------------------------------

def build_merged_glossary(
    extracted_cn_to_es: dict[str, str],
) -> dict[str, str]:
    """
    Merge glossaries with priority: master > hardcoded concepts > extracted.
    Master glossary (from build_master_glossary.py) is the single source of truth.
    """
    result: dict[str, str] = {}
    # Start with extracted names (lowest priority)
    result.update(extracted_cn_to_es)
    # Overlay with hardcoded concepts (medium priority)
    result.update(CN_TO_ES_CONCEPTS)
    # Overlay with master glossary (highest priority — cross-chapter verified)
    result.update(MASTER_CN_TO_ES)
    return result


# -- Prompt building -----------------------------------------------------------

def build_system_prompt(cn_to_es: dict[str, str]) -> str:
    parts: list[str] = []

    # -- Core translation instructions --
    parts.append(
        "Eres un traductor literario profesional de novelas web xianxia/xuanhuan "
        "del CHINO MANDARIN al español neutro latinoamericano.\n\n"
        "=== TERMINOLOGIA OBLIGATORIA ===\n"
        "DEBES usar EXACTAMENTE los términos del glosario CN->ES que aparece abajo. "
        "NUNCA inventes traducciones alternativas. Ejemplos CRITICOS:\n"
        "- 合道 = \"Daofuse\" (NUNCA \"union del Dao\", \"fusion del Dao\", "
        "\"Dao unido\" ni variantes)\n"
        "- 规则之主 = \"Maestro de las Leyes\" (NUNCA \"señor de las reglas\")\n"
        "- 镇守 = \"guardian\" (NUNCA \"protector\" o \"vigilante\")\n"
        "- 人主 = \"señor humano\" (NUNCA \"señor de los humanos\")\n"
        "- 本道 = \"pen dao\" (NUNCA \"dao principal\", \"mi dao\" ni variantes)\n"
        "Si un término chino aparece en el glosario, USA ESA traduccion sin "
        "excepcion.\n\n"
        "=== REGLAS DE TRADUCCION ===\n"
        "1. Traduce directamente del chino al español. NUNCA copies texto en "
        "inglés ni en chino en la salida.\n"
        "2. ESTILO LITERARIO (CRITICO): Las novelas web chinas usan frases muy "
        "cortas y fragmentos telegraficos. NO copies esa estructura. "
        "REESTRUCTURA las frases en oraciones completas y fluidas, como haria "
        "un novelista profesional hispanohablante. Combina fragmentos en "
        "oraciones compuestas con conjunciones y subordinadas.\n"
        "3. Tono y Caracterización:\n"
        "   - Para personajes INTELECTUALES/ERUDITOS (ej. Wan Tiansheng): Usa un tono "
        "CLÍNICO, ANALÍTICO y SOFISTICADO. Evita el dramatismo emocional. Su voz "
        "debe sonar como la de un arquitecto de sistemas o un filósofo deconstruyendo "
        "la realidad. Prefieren la precisión técnica a la metáfora florida.\n"
        "   - 'Dao' y Cultivo: Trata estos temas como 'deuda técnica', 'acumulación "
        "de datos' o 'procesos de optimización'. Usa vocabulario elevado: 'acumulación', "
        "'fundamentos', 'exploración', 'pionerismo'.\n"
        "4. MODISMOS E IDIOMAS:\n"
        "   - NUNCA traduzcas modismos literalmente si suenan extraños (ej. NO 'entrar en tus ojos').\n"
        "   - ADAPTA el sentido a un registro culto. Ej: '¿He entrado en tus ojos?' -> "
        "'¿Soy digno de su atención?' o '¿Estoy ascendiendo a tu salón?'.\n"
        "5. DIALOGOS: Los dialogos deben sonar naturales en español. No traduzcas "
        "palabra por palabra. Adapta expresiones y muletillas chinas a "
        "equivalentes naturales en español. Ejemplo:\n"
        "  MAL: \"de repente extrañado dijo: Te conozco?\"\n"
        "  BIEN: \"pregunto con asombro: Te conozco?\"\n"
        "  MAL: \"durante mucho tiempo, dudo y dijo\"\n"
        "  BIEN: \"Tras un largo silencio, respondio con vacilacion\"\n"
        "6. Conserva el tono narrativo, la tension dramatica y los matices "
        "emocionales del original.\n"
        "7. Preserva los saltos de parrafo exactamente como en el original.\n"
        "8. NO agregues notas, aclaraciones ni comentarios que no estén en el "
        "original.\n"
        "9. Usa español neutro: evita 'vosotros', 'vale', 'coger'.\n"
        "10. Omite lineas que sean notas del autor (求订阅, 求月票, PS:, etc.).\n"
        "11. NOMBRES: pinyin se mantiene (Su Yu, Tiangu). Titulos y rangos se "
        "traducen al español segun el glosario.\n"
        "12. NUNCA dejes texto en chino sin traducir, incluyendo onomatopeyas "
        "(噗嗤→resopló, 嗤→chasqueó, 嘭→¡Bam!, 哼→¡Hmph!, 呵呵→je je, "
        "嘶→siseó, 咔嚓→¡Crack!) y adverbios/adjetivos (幽幽→con voz etérea, "
        "淡淡→con calma, 缓缓→lentamente). Si no conoces la onomatopeya, "
        "tradúcela por su efecto sonoro en español.\n"
        "13. VARIA los verbos de dialogo: NO uses 'dijo con voz grave' ni "
        "'dijo con voz profunda' mas de 2 veces por capitulo. Alterna con: "
        "respondio, declaro, sentencio, murmuro, exclamo, replico, pronuncio, "
        "intervino, señalo, etc.\n"
        "14. EVITA muletillas repetitivas: 'En ese momento' maximo 2 veces "
        "por capitulo. Usa: 'Justo entonces', 'En aquel instante', 'Al punto', "
        "'En ese preciso momento', etc.\n"
        "15. PRIORIDAD LÓGICA: Ante la ambigüedad, prioriza la coherencia del sistema de poder. "
        "No lo traduzcas como tecnología moderna, sino como una filosofía con reglas estrictas. "
        "La 'magia' tiene leyes, pero siguen siendo leyes de un mundo fantástico, "
        "no de un centro de datos.\n\n"
        "=== EJEMPLO DE CALIDAD ===\n"
        "ORIGINAL (Wan Tiansheng): \"Was I able to enter your eyes?\"\n"
        "TRADUCCION (Estilo Arquitecto): \"Se puede decir que ahora eres un emperador. "
        "¿Estoy ascendiendo a tu salón?\" (Nótese el tono retórico y elevado).\n\n"
        "NARRACION MAL (calco del chino):\n"
        "\"Solo 6 fuerzas de la union del Dao, y todavia se esconden a muerte. "
        "6, si fuera antes, Su Yu exclamaria. Ahora... Hoy mate a 6! "
        "Demasiado poco!\"\n\n"
        "NARRACION BIEN (español literario):\n"
        "\"Solo quedaban seis Daofuse, todos ocultos y a la defensiva. En otro "
        "momento, Su Yu se habria sorprendido de que aun quedaran tantos. Pero "
        "ahora la perspectiva era otra: ese mismo dia habia matado a seis. "
        "Le parecian demasiado pocos.\"\n\n"
        "DIALOGO MAL:\n"
        "\"Zhao Chuan rapidamente dijo: Informando a su señoria, todos venimos "
        "del campamento del Marques Ejercito Estable!\"\n\n"
        "DIALOGO BIEN:\n"
        "\"Zhao Chuan se apresuro a responder: Mi señor, todos pertenecemos al "
        "campamento del Marques Ejercito Estable.\"\n\n"
        "Observa: se eliminan exclamaciones innecesarias, se reestructuran "
        "fragmentos telegraficos, y se usan los términos del glosario (Daofuse, "
        "no 'union del Dao').\n\n"
        "ONOMATOPEYAS MAL:\n"
        "\"噗嗤 Su Yu se rio.\"\n\n"
        "ONOMATOPEYAS BIEN:\n"
        "\"Su Yu resopló una carcajada.\"\n\n"
        "CALCO DE CADENCIA MAL:\n"
        "\"Abrió los ojos. Miró a su alrededor. Se levantó. Caminó hacia "
        "la puerta. La abrió.\"\n\n"
        "CADENCIA BIEN:\n"
        "\"Abrió los ojos y, tras observar a su alrededor, se levantó y "
        "caminó hasta la puerta para abrirla.\"\n\n"
        "PARRAFO-CALCO MAL:\n"
        "\"Sin palabras.\"\n"
        "(como párrafo independiente)\n\n"
        "PARRAFO-CALCO BIEN:\n"
        "\"Se quedó sin palabras.\"\n"
        "(integrado como oración completa)"
    )

    # -- CN->ES glossary (merged: auto-extracted names + hardcoded concepts) --
    if cn_to_es:
        rows = "\n".join(f"  {cn} -> {es}" for cn, es in cn_to_es.items())
        parts.append(
            "\n\nGLOSARIO CN->ES (MAXIMA PRIORIDAD -- usa estos términos siempre):\n"
            + rows
        )

    return "\n".join(parts)


_USER_TEMPLATE = (
    "Traduce el siguiente fragmento del CHINO al ESPAÑOL. "
    "Reestructura las frases para español literario fluido -- NO calques "
    "la gramatica china. Devuelve SOLO el texto traducido.\n\n{text}"
)


# -- Chunking ------------------------------------------------------------------

def chunk_paragraphs(paragraphs: list[str], max_chars: int) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for p in paragraphs:
        if current_len + len(p) > max_chars and current:
            chunks.append(current)
            current = [p]
            current_len = len(p)
        else:
            current.append(p)
            current_len += len(p)
    if current:
        chunks.append(current)
    return chunks


# -- Translation ---------------------------------------------------------------

async def translate_chapter(
    adapter,
    cn_num: int,
    cn_path: Path,
    en_dir: Path,
    alignment_map: dict[int, list[int]],
    output_dir: Path,
    chunk_chars: int,
    max_concurrent: int,
    temperature: float,
    logger: logging.Logger,
    *,
    do_polish: bool = True,
    polish_only: bool = False,
    force: bool = False,
) -> None:
    out_path = output_dir / f"cn_{cn_num:04d}_es.txt"

    # Forbidden terms to inject into the polish prompt
    _forbidden = {
        "fusión del Dao": "Daofuse",
        "unión del Dao": "Daofuse",
        "Dao fusionado": "Daofuse",
        "se Daofuse": "alcanzó el Daofuse",
        "Lord": "Señor",
        "Rumble": "¡Retumbó!",
        "señor de las reglas": "Maestro de las Leyes",
        "Rey del Cielo": "Rey Celestial",
    }

    # --polish-only: re-polish an already-translated file
    if polish_only:
        if not out_path.exists():
            logger.warning("Chapter CN-%04d has no existing translation — skipping polish-only.", cn_num)
            return
        existing = out_path.read_text(encoding="utf-8")
        parts = existing.split("\n\n")
        title_es = parts[0] if parts else ""
        title_es = clean_title(title_es)
        translated = [p.strip() for p in parts[1:] if p.strip()]
        # Apply regex cleanup (pre-LLM)
        translated = regex_cleanup_chapter(translated)
        # Apply LLM polish
        if do_polish:
            translated = await polish_chapter(
                translated, adapter, logger, forbidden_terms=_forbidden,
            )
            # Final regex pass to catch anything the LLM reintroduced
            translated = regex_cleanup_chapter(translated)
        lines = [title_es, ""] + translated
        out_path.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
        logger.info("  Re-pulido: %s", out_path.relative_to(ROOT))
        return

    if out_path.exists() and not force:
        logger.info("Chapter CN-%04d already translated — skipping (use --force to re-translate).", cn_num)
        return

    title_cn, paragraphs = parse_cn_chapter(cn_path)
    if not paragraphs:
        logger.warning("Chapter CN-%04d is empty — skipping.", cn_num)
        return

    # 1. Load aligned EN chapters (full text, no truncation)
    en_nums = alignment_map.get(cn_num, [])
    en_texts = read_en_chapters_full(en_dir, en_nums) if en_nums else []

    # 2. Extract name glossary (fast LLM call, cached)
    cache_path = GLOSSARY_CACHE_DIR / f"cn_{cn_num:04d}.json"
    cn_full_text = "\n".join(paragraphs)
    extracted_cn_to_en = await extract_name_glossary(
        cn_text=cn_full_text,
        en_texts=en_texts,
        adapter=adapter,
        cache_path=cache_path,
        logger=logger,
    )

    # 3. Convert EN names -> ES (LLM-based)
    # extracted_cn_to_es = en_names_to_es(extracted_cn_to_en) # OLD
    extracted_cn_to_es = await translate_glossary_to_es(
        extracted_cn_to_en,
        adapter,
        logger
    )

    # 4. Merge: extracted names + hardcoded concepts + master glossary
    merged_glossary = build_merged_glossary(extracted_cn_to_es)

    # 5. Build system prompt with merged glossary (no raw EN blob)
    system_prompt = build_system_prompt(merged_glossary)

    logger.info(
        "Chapter CN-%04d: %d paragraphs, %d names extracted, %d master, EN=%s",
        cn_num,
        len(paragraphs),
        len(extracted_cn_to_en),
        len(MASTER_CN_TO_ES),
        en_nums[0] if en_nums else "none",
    )

    # Translate title
    title_instruction = (
        f"Traduce este titulo del chino al español "
        f"(formato 'Capitulo N: ...'): {title_cn or f'第{cn_num}章'}"
    )
    title_es = (
        await adapter.translate_chunk(
            system_prompt,
            title_instruction,
            temperature=temperature,
        )
    ).strip()

    # Chunk body and translate concurrently
    chunks = chunk_paragraphs(paragraphs, chunk_chars)
    logger.info("  %d chunks to translate...", len(chunks))

    semaphore = asyncio.Semaphore(max_concurrent)

    async def do_chunk(idx: int, chunk_pars: list[str]) -> tuple[int, list[str]]:
        text = "\n\n".join(chunk_pars)
        async with semaphore:
            raw = await adapter.translate_chunk(
                system_prompt,
                _USER_TEMPLATE.format(text=text),
                temperature=temperature,
            )
        out_pars = [p.strip() for p in raw.split("\n\n") if p.strip()]
        logger.info("  Chunk %d/%d done.", idx + 1, len(chunks))
        return idx, out_pars

    results = await asyncio.gather(*[do_chunk(i, c) for i, c in enumerate(chunks)])
    results.sort(key=lambda x: x[0])

    translated: list[str] = []
    for _, pars in results:
        translated.extend(pars)

    # Post-processing: regex cleanup (pre-LLM)
    translated = regex_cleanup_chapter(translated)

    # Post-processing: LLM polish (optional)
    if do_polish:
        translated = await polish_chapter(
            translated, adapter, logger, forbidden_terms=_forbidden,
        )
        # Final regex pass to catch anything the LLM reintroduced
        translated = regex_cleanup_chapter(translated)

    # Write output
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = [title_es, ""] + translated
    out_path.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    logger.info("  Saved: %s", out_path.relative_to(ROOT))


# -- CLI -----------------------------------------------------------------------

async def main_async(args: argparse.Namespace) -> None:
    from utils.logger import setup_logger, LOGGER_NAME
    setup_logger(verbose=True)
    logger = logging.getLogger(LOGGER_NAME)

    # Load .env (same convention as main.py)
    from utils.file_manager import load_env_file
    load_env_file(ROOT / ".env")

    settings = load_settings()
    alignment_map = load_alignment_map()

    cn_dir = ROOT / "data" / "cn_raws"
    en_dir = ROOT / settings.get("output", {}).get("default_input_dir", "output/tribulation")
    output_dir = ROOT / args.output

    # Resolve adapter
    adapter_cfg = settings.get("adapter", {})
    adapter_name = args.adapter or adapter_cfg.get("active", "gemini")
    from adapters import get_adapter
    adapter = get_adapter(adapter_name, adapter_cfg)
    logger.info("Adapter: %s  model: %s", adapter_name, adapter.model_name)

    # Collect CN chapters in range
    def cn_num(p: Path) -> int:
        m = re.match(r"cn_0*(\d+)", p.stem)
        return int(m.group(1)) if m else -1

    cn_chapters = sorted(
        [
            (cn_num(p), p)
            for p in cn_dir.glob("cn_*.txt")
            if args.start <= cn_num(p) <= args.end
        ],
        key=lambda t: t[0],
    )

    if not cn_chapters:
        logger.error("No CN chapters found in range %d-%d.", args.start, args.end)
        return

    logger.info(
        "Translating %d CN chapters (%d-%d) -> %s",
        len(cn_chapters),
        cn_chapters[0][0],
        cn_chapters[-1][0],
        output_dir,
    )

    trans_cfg = settings.get("translation", {})
    chunk_chars = args.chunk_chars or trans_cfg.get("chunk_chars", 2500)
    max_concurrent = trans_cfg.get("max_concurrent", 2)
    temperature = trans_cfg.get("temperature", 0.2)

    for cn_num_val, cn_path in cn_chapters:
        await translate_chapter(
            adapter=adapter,
            cn_num=cn_num_val,
            cn_path=cn_path,
            en_dir=en_dir,
            alignment_map=alignment_map,
            output_dir=output_dir,
            chunk_chars=chunk_chars,
            max_concurrent=max_concurrent,
            temperature=temperature,
            logger=logger,
            do_polish=args.polish,
            polish_only=args.polish_only,
            force=args.force,
        )

    logger.info("CN->ES translation complete.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Translate CN chapters (data/cn_raws/) to Spanish",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--start", type=int, default=2,
        help="Primer numero de capitulo CN a traducir"
    )
    parser.add_argument(
        "--end", type=int, default=999,
        help="Ultimo numero de capitulo CN a traducir"
    )
    parser.add_argument(
        "--adapter", choices=["gemini", "openai"], default=None,
        help="Adaptador LLM (por defecto: el activo en settings.yaml)"
    )
    parser.add_argument(
        "--output", default="traduccion_cn",
        help="Directorio de salida (relativo a la raiz del proyecto)"
    )
    parser.add_argument(
        "--chunk-chars", type=int, default=None,
        help="Tamano maximo de cada chunk en caracteres chinos (por defecto: 2500)"
    )
    parser.add_argument(
        "--polish", action="store_true", default=True,
        help="Activar pulido LLM post-traduccion (default: activado)"
    )
    parser.add_argument(
        "--no-polish", dest="polish", action="store_false",
        help="Desactivar pulido LLM (solo aplica limpieza regex)"
    )
    parser.add_argument(
        "--polish-only", action="store_true", default=False,
        help="Solo re-pulir traducciones existentes sin re-traducir"
    )
    parser.add_argument(
        "--force", action="store_true", default=False,
        help="Re-traducir capitulos aunque ya exista traduccion"
    )
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
