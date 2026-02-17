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

# -- Root discovery ------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))

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
    "归墟之地": "Tierra del Retorno",
    "镇守": "guardián",
}

# -- EN title/rank -> ES translation rules (for en_names_to_es) ----------------
_EN_TO_ES_TITLES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bKing\b"), "Rey"),
    (re.compile(r"\bQueen\b"), "Reina"),
    (re.compile(r"\bEmperor\b"), "Emperador"),
    (re.compile(r"\bEmpress\b"), "Emperatriz"),
    (re.compile(r"\bPseudo Emperor\b"), "Pseudo Emperador"),
    (re.compile(r"\bMarquis\b"), "Marqués"),
    (re.compile(r"\bMarquise\b"), "Marquesa"),
    (re.compile(r"\bGeneral\b"), "General"),
    (re.compile(r"\bPrince\b"), "Príncipe"),
    (re.compile(r"\bPrincess\b"), "Princesa"),
    (re.compile(r"\bDuke\b"), "Duque"),
    (re.compile(r"\bLord\b"), "Señor"),
    (re.compile(r"\bLady\b"), "Señora"),
    (re.compile(r"\bSaint\b"), "Santo"),
    (re.compile(r"\bElder\b"), "Anciano"),
    (re.compile(r"\bMaster\b"), "Maestro"),
    (re.compile(r"\bConsort\b"), "Consorte"),
    (re.compile(r"\bGreat\b"), "Gran"),
    (re.compile(r"\bHeavenly\b"), "Celestial"),
    (re.compile(r"\bImmortal\b"), "Inmortal"),
    (re.compile(r"\bDivine\b"), "Divino"),
    (re.compile(r"\bMartial\b"), "Marcial"),
    (re.compile(r"\bBattle\b"), "Batalla"),
    (re.compile(r"\bHundred\b"), "Cien"),
    (re.compile(r"\bCulture\b"), "Cultura"),
    (re.compile(r"\bJade\b"), "Jade"),
    (re.compile(r"\bSoldier\b"), "Soldado"),
    (re.compile(r"\bTalisman\b"), "Talismán"),
    (re.compile(r"\bShadow\b"), "Sombra"),
    (re.compile(r"\bAbyss\b"), "Abismo"),
    (re.compile(r"\bDragon\b"), "Dragón"),
    (re.compile(r"\bPhoenix\b"), "Fénix"),
    (re.compile(r"\bFate\b"), "Destino"),
    (re.compile(r"\bNether\b"), "Inframundo"),
    (re.compile(r"\bFallen\b"), "Caída"),
    (re.compile(r"\bStar\b"), "Estrella"),
    (re.compile(r"\bCloud\b"), "Nube"),
    (re.compile(r"\bWater\b"), "Agua"),
    (re.compile(r"\bFire\b"), "Fuego"),
    (re.compile(r"\bOrigin\b"), "Origen"),
    (re.compile(r"\bBlood\b"), "Sangre"),
    (re.compile(r"\bCelestial\b"), "Celestial"),
    (re.compile(r"\bMansion\b"), "Mansión"),
    (re.compile(r"\bAcademy\b"), "Academia"),
    (re.compile(r"\bCity\b"), "Ciudad"),
    (re.compile(r"\bMountain\b"), "Montaña"),
    (re.compile(r"\bRiver\b"), "Río"),
    (re.compile(r"\bSea\b"), "Mar"),
    (re.compile(r"\bRealm\b"), "Reino"),
    (re.compile(r"\bDomain\b"), "Dominio"),
    (re.compile(r"\bArmy\b"), "Ejército"),
    (re.compile(r"\bOffice\b"), "Oficina"),
    (re.compile(r"\bCenter\b"), "Centro"),
    (re.compile(r"\bResearch\b"), "Investigación"),
    (re.compile(r"\bBattlefield\b"), "Campo de Batalla"),
    (re.compile(r"\bPeace\b"), "Paz"),
    (re.compile(r"\bSouth\b"), "Sur"),
    (re.compile(r"\bNorth\b"), "Norte"),
    (re.compile(r"\bEast\b"), "Este"),
    (re.compile(r"\bWest\b"), "Oeste"),
    (re.compile(r"\bPrimordial\b"), "Primordial"),
    (re.compile(r"\bGiant\b"), "Gigante"),
    (re.compile(r"\bMystic\b"), "Místico"),
    (re.compile(r"\bArmor\b"), "Armadura"),
    (re.compile(r"\bHuman\b"), "Humano"),
    (re.compile(r"\bApe\b"), "Simio"),
    (re.compile(r"\bGolden Crow\b"), "Cuervo Dorado"),
    (re.compile(r"\bSuppression\b"), "Supresión"),
    (re.compile(r"\bOverseer\b"), "Supervisor"),
    (re.compile(r"\bStable\b"), "Estable"),
    (re.compile(r"\bSevering\b"), "Corta"),
]

# Known pinyin-only names that should stay as-is (no title translation needed).
# These are common character names where every word is pinyin.
_PINYIN_PATTERN = re.compile(
    r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*$"
)


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
                logger.info("  Glosario de nombres cargado de cache: %s", cache_path.name)
                return cached
        except (json.JSONDecodeError, OSError):
            pass  # Re-extract if cache is corrupted

    if not en_texts:
        logger.info("  Sin referencia EN -- sin extracción de nombres.")
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
        logger.warning("  Error en extracción de nombres: %s", e)
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
        logger.warning("  No se pudo parsear JSON de extracción: %.100s...", text)
        return {}

    if not isinstance(glossary, dict):
        logger.warning("  Respuesta de extracción no es un dict.")
        return {}

    # Filter out non-string values
    glossary = {k: v for k, v in glossary.items() if isinstance(k, str) and isinstance(v, str)}

    # Save to cache
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False, indent=2)

    logger.info("  Extraídos %d nombres CN->EN (guardados en cache).", len(glossary))
    return glossary


# -- EN names -> ES conversion ------------------------------------------------

def _is_pure_pinyin(name: str) -> bool:
    """Check if a name is purely pinyin (all capitalized words, no English titles)."""
    # Known English words that appear in titles -- if any word matches, it's not pure pinyin
    _TITLE_WORDS = {
        "king", "queen", "emperor", "empress", "marquis", "marquise", "general",
        "prince", "princess", "duke", "lord", "lady", "saint", "elder", "master",
        "consort", "great", "heavenly", "immortal", "divine", "martial", "battle",
        "hundred", "culture", "jade", "soldier", "talisman", "shadow", "abyss",
        "dragon", "phoenix", "fate", "nether", "fallen", "star", "cloud", "water",
        "fire", "origin", "blood", "celestial", "mansion", "academy", "city",
        "mountain", "river", "sea", "realm", "domain", "army", "office", "center",
        "research", "battlefield", "peace", "south", "north", "east", "west",
        "primordial", "giant", "mystic", "armor", "human", "ape", "golden", "crow",
        "suppression", "overseer", "stable", "severing", "pseudo", "old", "fat",
        "grand", "dream", "spirit", "monster", "heaven", "hole", "range", "floating",
        "sacred", "ancient", "four", "brave", "clan",
    }
    words = name.split()
    if not words:
        return False
    return all(w.lower() not in _TITLE_WORDS for w in words)


def en_names_to_es(cn_to_en: dict[str, str]) -> dict[str, str]:
    """
    Convert extracted CN->EN name mappings to CN->ES.

    Rules:
    - Pure pinyin names stay as-is (Su Yu, Liu Wenyan)
    - Descriptive titles/ranks get their English components translated to Spanish
      using the rule-based _EN_TO_ES_TITLES patterns
    """
    cn_to_es: dict[str, str] = {}

    for cn, en in cn_to_en.items():
        if _is_pure_pinyin(en):
            cn_to_es[cn] = en
        else:
            # Apply title translation rules
            es = en
            for pattern, replacement in _EN_TO_ES_TITLES:
                es = pattern.sub(replacement, es)
            cn_to_es[cn] = es

    return cn_to_es


# -- Glossary building --------------------------------------------------------

def build_merged_glossary(
    extracted_cn_to_es: dict[str, str],
) -> dict[str, str]:
    """
    Merge extracted per-chapter names with the hardcoded concept glossary.
    Hardcoded concepts take priority (they are manually curated).
    """
    result: dict[str, str] = {}
    # Start with extracted names (lower priority)
    result.update(extracted_cn_to_es)
    # Overlay with hardcoded concepts (higher priority)
    result.update(CN_TO_ES_CONCEPTS)
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
        "3. DIALOGOS: Los dialogos deben sonar naturales en español. No traduzcas "
        "palabra por palabra. Adapta expresiones y muletillas chinas a "
        "equivalentes naturales en español. Ejemplo:\n"
        "  MAL: \"de repente extrañado dijo: Te conozco?\"\n"
        "  BIEN: \"pregunto con asombro: Te conozco?\"\n"
        "  MAL: \"durante mucho tiempo, dudo y dijo\"\n"
        "  BIEN: \"Tras un largo silencio, respondio con vacilacion\"\n"
        "4. Conserva el tono narrativo, la tension dramatica y los matices "
        "emocionales del original.\n"
        "5. Preserva los saltos de parrafo exactamente como en el original.\n"
        "6. NO agregues notas, aclaraciones ni comentarios que no estén en el "
        "original.\n"
        "7. Usa español neutro: evita 'vosotros', 'vale', 'coger'.\n"
        "8. Omite lineas que sean notas del autor (求订阅, 求月票, PS:, etc.).\n"
        "9. NOMBRES: pinyin se mantiene (Su Yu, Tiangu). Titulos y rangos se "
        "traducen al español segun el glosario.\n\n"
        "=== EJEMPLO DE CALIDAD ===\n"
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
        "no 'union del Dao')."
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
) -> None:
    out_path = output_dir / f"cn_{cn_num:04d}_es.txt"
    if out_path.exists():
        logger.info("Cap CN-%04d ya traducido -- omitiendo.", cn_num)
        return

    title_cn, paragraphs = parse_cn_chapter(cn_path)
    if not paragraphs:
        logger.warning("Cap CN-%04d vacio -- omitiendo.", cn_num)
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

    # 3. Convert EN names -> ES (rule-based)
    extracted_cn_to_es = en_names_to_es(extracted_cn_to_en)

    # 4. Merge: extracted names + hardcoded concepts
    merged_glossary = build_merged_glossary(extracted_cn_to_es)

    # 5. Build system prompt with merged glossary (no raw EN blob)
    system_prompt = build_system_prompt(merged_glossary)

    logger.info(
        "Cap CN-%04d: %d parrafos, %d nombres extraidos, EN=%s",
        cn_num,
        len(paragraphs),
        len(extracted_cn_to_en),
        en_nums[0] if en_nums else "ninguna",
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
    logger.info("  %d chunks para traducir...", len(chunks))

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
        logger.info("  Chunk %d/%d completado.", idx + 1, len(chunks))
        return idx, out_pars

    results = await asyncio.gather(*[do_chunk(i, c) for i, c in enumerate(chunks)])
    results.sort(key=lambda x: x[0])

    translated: list[str] = []
    for _, pars in results:
        translated.extend(pars)

    # Write output
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = [title_es, ""] + translated
    out_path.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    logger.info("  Guardado: %s", out_path.relative_to(ROOT))


# -- CLI -----------------------------------------------------------------------

async def main_async(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger("translate_cn")

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
    logger.info("Adaptador: %s  modelo: %s", adapter_name, adapter.model_name)

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
        logger.error("No se encontraron capitulos CN en %d-%d.", args.start, args.end)
        return

    logger.info(
        "Traduciendo %d capitulos CN (%d-%d) -> %s",
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
        )

    logger.info("Traduccion CN->ES completada.")


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
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
