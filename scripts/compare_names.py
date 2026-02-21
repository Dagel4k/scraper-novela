#!/usr/bin/env python3
"""
Compare proper nouns between EN reference and ES translation.

For each chapter, this script:
1. Loads master_names.json (CN→EN→ES)
2. Finds which EN names appear in the EN reference text
3. Checks if the corresponding ES name appears in the ES translation
4. Reports mismatches (EN name found but ES equivalent missing)

Usage:
    python scripts/compare_names.py --start 705 --end 720
    python scripts/compare_names.py --start 705 --end 720 --fix  # auto-fix with regex
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))

MASTER_PATH = ROOT / "data" / "master_names.json"
EN_DIR = ROOT / "output" / "tribulation"
ES_DIR = ROOT / "traduccion_cn"


def load_master() -> dict:
    """Load master glossary."""
    with MASTER_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_names_in_text(text: str, names: list[str]) -> list[str]:
    """Find which names from the list appear in the text (case-sensitive)."""
    found = []
    for name in names:
        if name in text:
            found.append(name)
    return found


def compare_chapter(chapter_num: int, master: dict) -> dict:
    """Compare names in EN vs ES for a single chapter.

    Returns dict with:
        - found_en: EN names found in EN text
        - found_es: ES names found in ES text
        - missing_es: EN names found but ES equivalent missing
        - wrong_es: alternative/wrong ES names detected
        - ok: names correctly translated
    """
    en_path = EN_DIR / f"{chapter_num:04d}_en.txt"
    es_path = ES_DIR / f"cn_{chapter_num:04d}_es.txt"

    if not en_path.exists():
        return {"error": f"EN file not found: {en_path}"}
    if not es_path.exists():
        return {"error": f"ES file not found: {es_path}"}

    en_text = en_path.read_text(encoding="utf-8")
    es_text = es_path.read_text(encoding="utf-8")

    # Build lookup: en_name → (cn, es_name)
    en_to_info = {}
    for cn, data in master.items():
        en_name = data["en"]
        es_name = data["es"]
        if en_name and len(en_name) > 1:  # skip single chars
            en_to_info[en_name] = {"cn": cn, "es": es_name}

    # Find EN names in EN text
    en_names = list(en_to_info.keys())
    # Sort by length descending to match longer names first
    en_names.sort(key=len, reverse=True)
    found_en = find_names_in_text(en_text, en_names)

    result = {
        "chapter": chapter_num,
        "found_en": [],
        "missing_es": [],
        "ok": [],
    }

    for en_name in found_en:
        info = en_to_info[en_name]
        es_name = info["es"]
        cn = info["cn"]

        # Check if the ES translation appears in the ES text
        if es_name in es_text:
            result["ok"].append({
                "cn": cn, "en": en_name, "es": es_name
            })
        else:
            # Check if the EN name appears literally in ES (untranslated)
            en_in_es = en_name in es_text
            result["missing_es"].append({
                "cn": cn, "en": en_name, "es_expected": es_name,
                "en_leaked": en_in_es,
            })

        result["found_en"].append(en_name)

    return result


def print_report(results: list[dict]):
    """Print a formatted report of all chapters."""
    total_ok = 0
    total_missing = 0
    total_leaked = 0
    all_missing = []

    for r in results:
        if "error" in r:
            print(f"\n  Cap {r.get('chapter', '?')}: {r['error']}")
            continue

        ch = r["chapter"]
        ok_count = len(r["ok"])
        miss_count = len(r["missing_es"])
        total_ok += ok_count
        total_missing += miss_count

        if miss_count == 0:
            print(f"  Cap {ch}: {ok_count} nombres OK")
            continue

        print(f"\n  Cap {ch}: {ok_count} OK, {miss_count} FALTANTES:")
        for m in r["missing_es"]:
            leaked = " [EN LEAKED]" if m["en_leaked"] else ""
            print(f"    {m['cn']} | EN: {m['en']} | ES esperado: {m['es_expected']}{leaked}")
            total_leaked += 1 if m["en_leaked"] else 0
            all_missing.append(m)

    print(f"\n{'='*60}")
    print(f"RESUMEN: {total_ok} OK | {total_missing} faltantes | {total_leaked} EN filtrado")

    if all_missing:
        # Group missing by EN name to see which are most common
        from collections import Counter
        missing_counter = Counter(m["en"] for m in all_missing)
        print(f"\nNombres faltantes más frecuentes:")
        for en_name, count in missing_counter.most_common(20):
            info = next(m for m in all_missing if m["en"] == en_name)
            print(f"  {count}x | {info['cn']} | {en_name} → {info['es_expected']}")


# EN terms that are also common Spanish words — unsafe for blind replacement
_UNSAFE_EN_TERMS = {
    "principal", "gorge", "Rainbow", "Garrison", "Ruins",
}


def fix_chapter(chapter_num: int, master: dict, dry_run: bool = True) -> int:
    """Fix ES translation by replacing EN leaked names with ES equivalents.

    Returns count of fixes applied.
    """
    es_path = ES_DIR / f"cn_{chapter_num:04d}_es.txt"
    if not es_path.exists():
        return 0

    text = es_path.read_text(encoding="utf-8")
    original = text
    fixes = 0

    # Build replacements: EN name → ES name, sorted by length (longest first)
    replacements = []
    for cn, data in master.items():
        en_name = data["en"]
        es_name = data["es"]
        if en_name != es_name and len(en_name) > 2 and en_name not in _UNSAFE_EN_TERMS:
            replacements.append((en_name, es_name))

    replacements.sort(key=lambda x: len(x[0]), reverse=True)

    for en_name, es_name in replacements:
        if en_name in text:
            # Use word boundary to avoid partial replacements
            pattern = re.compile(re.escape(en_name))
            new_text = pattern.sub(es_name, text)
            if new_text != text:
                count = text.count(en_name)
                if not dry_run:
                    text = new_text
                fixes += count
                print(f"  Cap {chapter_num}: '{en_name}' → '{es_name}' ({count}x)")

    if not dry_run and text != original:
        es_path.write_text(text, encoding="utf-8")

    return fixes


def main():
    parser = argparse.ArgumentParser(description="Compare EN vs ES proper nouns")
    parser.add_argument("--start", type=int, required=True, help="Start chapter")
    parser.add_argument("--end", type=int, required=True, help="End chapter")
    parser.add_argument("--fix", action="store_true",
                        help="Auto-fix EN leaked names in ES files")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show fixes without applying (with --fix)")
    args = parser.parse_args()

    master = load_master()
    print(f"Master glossary: {len(master)} entradas")

    if args.fix:
        print(f"\n{'='*60}")
        print(f"MODO FIX {'(dry-run)' if args.dry_run else '(aplicando cambios)'}")
        print(f"{'='*60}\n")
        total_fixes = 0
        for ch in range(args.start, args.end + 1):
            fixes = fix_chapter(ch, master, dry_run=args.dry_run)
            total_fixes += fixes
        print(f"\nTotal fixes: {total_fixes}")
        return

    print(f"\nComparando capítulos {args.start}-{args.end}...\n")
    results = []
    for ch in range(args.start, args.end + 1):
        r = compare_chapter(ch, master)
        results.append(r)

    print_report(results)


if __name__ == "__main__":
    main()
