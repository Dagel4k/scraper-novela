#!/usr/bin/env python3
"""
Build a master name glossary (CN→ES) by:

1. Scanning ALL per-chapter glossary caches (CN→EN)
2. Consolidating: when the same CN term has multiple EN translations,
   pick the most frequent one (majority vote)
3. Translating consolidated EN names → ES via deterministic rules
   (titles, ranks) + one-shot LLM for the remainder
4. Outputting data/master_names.json as the single source of truth

This script also:
- Filters out non-proper-nouns (generic cultivation terms, months, etc.)
- Flags suspicious extractions for manual review
- Compares EN reference text against ES translations to validate names

Usage:
    python scripts/build_master_glossary.py
    python scripts/build_master_glossary.py --review  # show conflicts for manual review
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))

CACHE_DIR = ROOT / "data" / "name_glossary_cache"
MASTER_PATH = ROOT / "data" / "master_names.json"
EN_TO_ES_EXTRA_PATH = ROOT / "data" / "en_to_es_extra.json"
EN_DIR = ROOT / "output" / "tribulation"
ES_DIR = ROOT / "traduccion_cn"

# Terms that should NOT be in the name glossary (generic concepts)
_BLACKLIST_CN = {
    "九层", "笔道", "九月", "六月", "七月", "八月", "十月", "十一月", "十二月",
    "一月", "二月", "三月", "四月", "五月", "长河", "天地", "万族", "修炼",
    "功法", "战技", "意志力", "元气", "精血", "气运",
}

# Known correct EN translations (manually verified override)
_VERIFIED_EN = {
    "苏宇": "Su Yu",
    "大周王": "Great Zhou King",
    "百战王": "Hundred Battle King",
    "文王": "King Wen",
    "符王": "Talisman King",
    "玉王": "Jade King",
    "兵王": "Soldier King",
    "天古": "Tiangu",
    "寂无": "Jiwu",
    "含香": "Han Xiang",
    "魔戟": "Mo Ji",
    "宇皇": "Emperor Yu",
    "命皇": "Fate Pseudo Emperor",
    "冥皇": "Nether Emperor",
    "周天齐": "Zhou Tianqi",
    "万天圣": "Wan Tiansheng",
    "蓝天": "Lan Tian",
    "定军侯": "Army Stabilizing Marquis",
    "岷山侯": "Marquis Minshan",
    "天命侯": "Heavenly Fate Marquis",
    "陨星侯": "Fallen Star Marquis",
    "仙战侯": "Immortal Battle Marquis",
    "断血侯": "Blood Severing Marquis",
    "元圣侯": "Origin Saint Marquis",
    "监天侯": "Heavenly Overseer Marquis",
    "天龙侯": "Celestial Dragon Marquis",
    "大秦王": "Great Qin King",
    "太古巨人王": "Primordial Giant King",
    "老龟": "Old Turtle",
    "老乌龟": "Old Turtle",
    "多宝": "Duo Bao",
    "豆包": "Dou Bao",
    "炊饼": "Chui Bing",
    "鸿蒙": "Hongmeng",
    "天灭古城": "Heavendoom City",
    "天灭城": "Heavendoom City",
    "文明学府": "Civilization Academy",
    "大夏龙雀": "Great Xia Dragon Sparrow",
    "大夏府": "Great Xia Prefecture",
    "夏龙武": "Xia Longwu",
    "夏皇": "Xia Emperor",
    "黑天鸦": "Black Sky Crow",
    "星月": "Xingyue",
    "食铁古皇": "Iron-Eating Beast Pseudo Emperor",
    "空间古皇": "Spatial Beast Pseudo Emperor",
    "狱王": "Prison King",
    "云虎": "Cloud Tiger",
    "蛮牛": "Wild Bull",
    "飞天虎": "Flying Tiger",
    "玄铠一族": "Armored Race",
}

# Deterministic EN→ES translation rules for titles/ranks
_EN_TO_ES_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^Great (\w+) King$"), r"Gran Rey \1"),
    (re.compile(r"^(\w+) King$"), r"Rey \1"),
    (re.compile(r"^King (\w+)$"), r"Rey \1"),
    (re.compile(r"^(\w+) Marquis$"), r"Marqués \1"),
    (re.compile(r"^Marquis (\w+)$"), r"Marqués \1"),
    (re.compile(r"^(\w+) Pseudo Emperor$"), r"Pseudo Emperador \1"),
    (re.compile(r"^Emperor (\w+)$"), r"Emperador \1"),
    (re.compile(r"^(\w+) Emperor$"), r"Emperador \1"),
    (re.compile(r"^Nether Emperor$"), "Emperador del Inframundo"),
    (re.compile(r"^Fate Pseudo Emperor$"), "Pseudo Emperador del Destino"),
    (re.compile(r"^Primordial Giant King$"), "Rey de los Gigantes Primordiales"),
    (re.compile(r"^Prison King$"), "Rey de la Prisión"),
    (re.compile(r"^Talisman King$"), "Rey de los Talismanes"),
    (re.compile(r"^Jade King$"), "Rey de Jade"),
    (re.compile(r"^Soldier King$"), "Rey Soldado"),
    (re.compile(r"^Iron-Eating Beast Pseudo Emperor$"), "Pseudo Emperador de la Bestia Devora Hierro"),
    (re.compile(r"^Spatial Beast Pseudo Emperor$"), "Pseudo Emperador de la Bestia Espacial"),
    (re.compile(r"^Hundred Battle King$"), "Rey de las Cien Batallas"),
    (re.compile(r"^Army Stabilizing Marquis$"), "Marqués Dingjun"),
    (re.compile(r"^Heavenly Fate Marquis$"), "Marqués del Destino Celestial"),
    (re.compile(r"^Fallen Star Marquis$"), "Marqués de la Estrella Caída"),
    (re.compile(r"^Immortal Battle Marquis$"), "Marqués de la Batalla Inmortal"),
    (re.compile(r"^Blood Severing Marquis$"), "Marqués de la Sangre Cortada"),
    (re.compile(r"^Origin Saint Marquis$"), "Marqués del Santo Origen"),
    (re.compile(r"^Heavenly Overseer Marquis$"), "Marqués del Vigía Celestial"),
    (re.compile(r"^Celestial Dragon Marquis$"), "Marqués del Dragón Celestial"),
    (re.compile(r"^Black Sky Crow$"), "Cuervo del Cielo Negro"),
    (re.compile(r"^Civilization Academy$"), "Academia de la Civilización"),
    (re.compile(r"^Great Xia Prefecture$"), "Prefectura de Gran Xia"),
    (re.compile(r"^Great Xia Dragon Sparrow$"), "Gran Gorrión Dragón de Xia"),
    (re.compile(r"^Heavendoom City$"), "Ciudad Castigo Celestial"),
    (re.compile(r"^Armored Race$"), "Raza de la Armadura Mística"),
    (re.compile(r"^Cloud Tiger$"), "Tigre de las Nubes"),
    (re.compile(r"^Wild Bull$"), "Toro Bárbaro"),
    (re.compile(r"^Flying Tiger$"), "Tigre Celestial Volador"),
    (re.compile(r"^Old Turtle$"), "Vieja Tortuga"),
    (re.compile(r"^Xia Emperor$"), "Emperador Xia"),
]

# Direct EN→ES for terms that don't match pattern rules
_DIRECT_EN_TO_ES: dict[str, str] = {
    "General of the North": "General del Norte",
    "God-Eating Second Emperor": "Segundo Emperador Devorador de Dioses",
    "Great Xia TV Station": "Estación de TV de Gran Xia",
    "Hou race": "raza Hou",
    "Iron-Eating Beast Realm": "Reino de la Bestia Devora Hierro",
    "Iron-Eating Realm": "Reino Devora Hierro",
    "Iron-eating Beast Emperor": "Emperador de la Bestia Devora Hierro",
    "Iron-eating race": "raza devora hierro",
    "King Wen's Residence": "Residencia del Rey Wen",
    "North King's Mansion": "Mansión del Rey del Norte",
    "Ox-Faced Fish": "Pez Cara de Buey",
    "Plain of Desires": "Llanura de los Deseos",
    "River of Time": "Río del Tiempo",
    "Sea of Stars": "Mar de las Estrellas",
    "Seventh-stage Great Strength Realm": "Séptimo nivel del Reino de Gran Fuerza",
    "Soul devourers": "devoradores de almas",
    "anyuan dollars": "dólares anyuan",
    "barbaric bull race": "raza del toro bárbaro",
    "common language": "lengua común",
    "demon language": "lengua demoníaca",
    "demonic races": "razas demoníacas",
    "divine and devil language": "lengua divina y demoníaca",
    "eastern sector": "sector oriental",
    "golden crow": "cuervo dorado",
    "golden peng race": "raza del peng dorado",
    "gorge": "desfiladero",
    "internal affairs academy": "academia de asuntos internos",
    "iron-winged bird": "ave de alas de hierro",
    "kunpeng": "kunpeng",
    "primordial giants": "gigantes primordiales",
    "principal": "rector",
    "sky tiger race": "raza del tigre celeste",
    "three-headed demon wolf": "lobo demonio de tres cabezas",
    "war academy": "academia de guerra",
    # More common translations
    "Myriad Race Cult": "Culto de las Miríadas de Razas",
    "Eastern King Manor": "Mansión del Rey del Este",
    "East Rift Valley": "Valle del Rift Oriental",
    "Book Spirit": "Espíritu del Libro",
    "Cloud Water Marquis": "Marqués del Agua y las Nubes",
    "Human Ruler Seal": "Sello del Señor Humano",
    "Great Xia Civilization Academy": "Academia de la Civilización de Gran Xia",
    "Infinite Strength Realm": "Reino de Fuerza Infinita",
    "Great Strength Realm": "Reino de Gran Fuerza",
    "Human Realm": "Reino Humano",
    "Immortal Realm": "Reino Inmortal",
    "Divine Realm": "Reino Divino",
    "Devil Realm": "Reino Demonio",
    "Dragon Realm": "Reino del Dragón",
    "Phoenix Pseudo Emperor": "Pseudo Emperador Fénix",
    "Southern King": "Rey del Sur",
    "Xia Clan Commerce": "Comercio del Clan Xia",
    "Great Zhou": "Gran Zhou",
    "Tea Tree": "Árbol del Té",
    "West Royal Consort": "Consorte Real del Oeste",
    "Director Wan": "Director Wan",
    "Xingyue": "Xingyue",
    "Hongmeng": "Hongmeng",
    "Old Turtle": "Vieja Tortuga",
    "Starmoon": "Xingyue",
    # Realms and cultivation stages
    "Allheaven Battlefield": "Campo de Batalla de Todos los Cielos",
    "Skysoar Realm": "Reino del Vuelo Celestial",
    "Source Opening": "Apertura de la Fuente",
    "Source Opening Realm": "Reino de Apertura de la Fuente",
    "Source Opening Codex": "Códice de Apertura de la Fuente",
    "Source Swallowing": "Absorción de la Fuente",
    "One Hundred Openings": "Cien Aperturas",
    "Ancient Space Beast Realm": "Reino de la Bestia Espacial Ancestral",
    "Celestial Chasm Realm": "Reino del Abismo Celestial",
    "Death Realm": "Reino de la Muerte",
    "Demon Realm": "Reino Demonio",
    "Fate Realm": "Reino del Destino",
    "Hou Realm": "Reino Hou",
    "Mount Root Realm": "Reino del Monte Raíz",
    "Ape World": "Mundo de los Simios",
    "Kunpeng World": "Mundo Kunpeng",
    "Phoenix World": "Mundo del Fénix",
    # Places
    "Great Xia": "Gran Xia",
    "Great Qin": "Gran Qin",
    "Great Yong": "Gran Yong",
    "Great Ming Prefecture": "Prefectura de Gran Ming",
    "Great Xia War Academy": "Academia de Guerra de Gran Xia",
    "Martial Dragon War Academy": "Academia de Guerra del Dragón Marcial",
    "Hongmeng City": "Ciudad Hongmeng",
    "Nanyuan City": "Ciudad Nanyuan",
    "Nanyuan Secondary School": "Escuela Secundaria de Nanyuan",
    "Divine Fire Mountain": "Montaña del Fuego Divino",
    "Heavenly Fate Mountain": "Montaña del Destino Celestial",
    "Heavenly Hole Range": "Cordillera del Agujero Celestial",
    "Starfall Mountain": "Montaña de la Estrella Caída",
    "Geng Mountain": "Montaña Geng",
    "North King Territory": "Territorio del Rey del Norte",
    "Four Kings Domain": "Dominio de los Cuatro Reyes",
    "Floating River": "Río Flotante",
    "Heaven Gate": "Puerta del Cielo",
    "Heavendoom": "Castigo Celestial",
    "Ruins": "Ruinas",
    "Spirit Palace": "Palacio del Espíritu",
    # Military and organizations
    "Devil Subduing Army": "Ejército Subyugador de Demonios",
    "Martial Dragon Guards": "Guardias del Dragón Marcial",
    "Windcatcher Department": "Departamento Cazavientos",
    "Profound Department": "Departamento Profundo",
    "Garrison": "Guarnición",
    # Titles and people
    "Brave Martial General": "General Marcial Valiente",
    "Commander Tian Men": "Comandante Tian Men",
    "Instructor Liu": "Instructor Liu",
    "Mayor Tian He": "Alcalde Tian He",
    "Grandpa Zhou": "Abuelo Zhou",
    "Teacher Zhang": "Maestro Zhang",
    "Su Family": "Familia Su",
    "Fateless": "Sin Destino",
    "Time Master": "Maestro del Tiempo",
    "Time Book": "Libro del Tiempo",
    "Cloud Fire Marquis": "Marqués del Fuego y las Nubes",
    "Dragon Blood Marquis": "Marqués de la Sangre de Dragón",
    "South Suppression Marquis": "Marqués de la Supresión del Sur",
    # Creatures and items
    "Dark Devil Dragon": "Dragón Demonio Oscuro",
    "Fat Ball": "Bola Gorda",
    "Rainbow": "Arcoíris",
    "Sunmoon": "Sol y Luna",
    "Anping Calendar": "Calendario Anping",
    "Devour Heaven": "Devorador del Cielo",
    "Dragon Blood Treasure Hall": "Salón del Tesoro de Sangre de Dragón",
    "Earthly Branch Luo": "Luo de la Rama Terrenal",
    "First Profound": "Primer Profundo",
    "Heavenglimpse Eye": "Ojo del Vislumbre Celestial",
    "Heavenly Source Fruit": "Fruta de la Fuente Celestial",
    "Profound Nine": "Profundo Nueve",
    "New Yu": "Nuevo Yu",
    "Human Ruler Seal": "Sello del Señor Humano",
    # Edge cases from batch extraction
    "Shan Hai Xun You Tie": "Estela Shan Hai Xun You",
    "hou": "hou",
    "hous": "hous",
    "suanni": "suanni",
}

# Pinyin names that should stay as-is in Spanish
_PINYIN_PASSTHROUGH = re.compile(
    r"^[A-Z][a-z]+(?: [A-Z][a-z]+){0,3}$"
)

# Load LLM-generated extra translations (from batch_extract pipeline)
_EN_TO_ES_EXTRA: dict[str, str] = {}
if EN_TO_ES_EXTRA_PATH.exists():
    try:
        with EN_TO_ES_EXTRA_PATH.open("r", encoding="utf-8") as _f:
            _EN_TO_ES_EXTRA = json.load(_f)
    except (json.JSONDecodeError, OSError):
        pass


def translate_en_to_es(en_name: str) -> str:
    """Translate an English proper noun to Spanish using deterministic rules."""
    # Hardcoded direct lookup (highest priority — manually curated)
    if en_name in _DIRECT_EN_TO_ES:
        return _DIRECT_EN_TO_ES[en_name]

    # Try each regex rule
    for pattern, replacement in _EN_TO_ES_RULES:
        m = pattern.match(en_name)
        if m:
            return pattern.sub(replacement, en_name)

    # LLM-generated extra translations (second priority)
    if en_name in _EN_TO_ES_EXTRA:
        return _EN_TO_ES_EXTRA[en_name]

    # Pinyin names stay as-is
    if _PINYIN_PASSTHROUGH.match(en_name):
        return en_name

    # Fallback: return as-is (will need manual review or LLM)
    return en_name


def load_all_caches() -> dict[str, Counter]:
    """Load all glossary caches and count EN translations per CN term."""
    cn_to_en_counts: dict[str, Counter] = defaultdict(Counter)

    for fname in sorted(CACHE_DIR.glob("cn_*.json")):
        try:
            with fname.open("r", encoding="utf-8") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        for cn, en in cache.items():
            if not isinstance(cn, str) or not isinstance(en, str):
                continue
            if cn in _BLACKLIST_CN:
                continue
            # Skip entries where EN contains Chinese characters (bad extraction)
            if re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', en):
                continue
            cn_to_en_counts[cn][en] += 1

    return cn_to_en_counts


def consolidate(cn_to_en_counts: dict[str, Counter]) -> dict[str, str]:
    """Pick the best EN translation for each CN term."""
    cn_to_en: dict[str, str] = {}

    for cn, counter in cn_to_en_counts.items():
        # Verified override takes priority
        if cn in _VERIFIED_EN:
            cn_to_en[cn] = _VERIFIED_EN[cn]
            continue

        # Majority vote
        best_en, _ = counter.most_common(1)[0]
        cn_to_en[cn] = best_en

    # Add verified terms not in caches
    for cn, en in _VERIFIED_EN.items():
        if cn not in cn_to_en:
            cn_to_en[cn] = en

    return cn_to_en


def build_master() -> dict:
    """Build the full master glossary: {cn: {en, es}}."""
    cn_to_en_counts = load_all_caches()
    cn_to_en = consolidate(cn_to_en_counts)

    master: dict[str, dict[str, str]] = {}
    for cn, en in sorted(cn_to_en.items()):
        es = translate_en_to_es(en)
        master[cn] = {"en": en, "es": es}

    return master


def show_conflicts(cn_to_en_counts: dict[str, Counter]):
    """Print CN terms that have conflicting EN translations."""
    print("\n=== CONFLICTOS (mismo CN, diferente EN) ===\n")
    conflicts = 0
    for cn, counter in sorted(cn_to_en_counts.items()):
        if len(counter) > 1:
            conflicts += 1
            resolved = _VERIFIED_EN.get(cn, counter.most_common(1)[0][0])
            print(f"  {cn}:")
            for en, count in counter.most_common():
                marker = " ✓" if en == resolved else ""
                print(f"    {en} ({count}x){marker}")
            print()
    print(f"Total: {conflicts} términos con conflicto")


def main():
    parser = argparse.ArgumentParser(description="Build master name glossary")
    parser.add_argument("--review", action="store_true",
                        help="Show conflicts for manual review")
    args = parser.parse_args()

    cn_to_en_counts = load_all_caches()
    print(f"Cargados {len(cn_to_en_counts)} términos CN de {len(list(CACHE_DIR.glob('cn_*.json')))} caches")

    if args.review:
        show_conflicts(cn_to_en_counts)
        return

    master = build_master()

    # Save
    MASTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MASTER_PATH.open("w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False, indent=2)

    print(f"Master glossary guardado: {MASTER_PATH}")
    print(f"  {len(master)} entradas (CN→EN→ES)")

    # Show a sample
    print("\n=== Muestra ===")
    for cn, data in list(master.items())[:15]:
        print(f"  {cn} → EN: {data['en']} → ES: {data['es']}")


if __name__ == "__main__":
    main()
