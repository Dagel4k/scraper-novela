#!/usr/bin/env python3
"""
QA script to check consistency of proper nouns between EN source and ES translation.
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path
from collections import defaultdict

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("check_consistency")

# Paths
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "traduccion_cn"
EN_DIR = ROOT / "output" / "tribulation" # Adjust if needed

MASTER_NAMES_PATH = DATA_DIR / "master_names.json"
ALIGNMENT_MAP_PATH = DATA_DIR / "alignment_map.json"

def load_json(path: Path):
    if not path.exists():
        logger.error(f"File not found: {path}")
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading {path}: {e}")
        return {}

def load_master_glossary_en_to_es():
    """
    Inverts the master glossary to map EN terms to a set of valid ES translations.
    Returns: dict { "English Term": {"Spanish Term 1", "Spanish Term 2"} }
    """
    data = load_json(MASTER_NAMES_PATH)
    en_to_es = defaultdict(set)
    
    for cn_term, entries in data.items():
        if isinstance(entries, dict):
            en = entries.get("en")
            es = entries.get("es")
            if en and es:
                en_to_es[en.strip()].add(es.strip())
    
    return en_to_es

def load_alignment_map():
    """
    Returns dict mapping CN chapter number (int) to list of EN chapter filenames (str).
    Logic copied/adapted from debug_alignment.py reverse mapping.
    """
    data = load_json(ALIGNMENT_MAP_PATH)
    
    # Check structure (list or dict)
    # Based on previous debugging, it's a dict of "EN_CHAPTER_STR": CN_CHAPTER_INT
    # or "mapping": ...
    
    mapping = defaultdict(list)
    
    if isinstance(data, dict):
        if "mapping" in data and isinstance(data["mapping"], list):
            # Format: {"mapping": [{"cn": 700, "en_range": [1831, 1833]}, ...]}
            # Wait, debug_alignment.py showed it was EN->CN dict in one version 
            # and verify output showed: CN 700 -> EN ['1831', '1832', '1833']
            # Let's support the EN->CN dict structure which seems to be the active one.
             pass 
        
        # Assume dict: { "1831": 700, "1832": 700, ... }
        for k, v in data.items():
            # Skip metadata keys if any
            if not isinstance(v, (int, str)): 
                continue
            try:
                cn_num = int(v)
                mapping[cn_num].append(str(k))
            except ValueError:
                pass

    return mapping

def read_file(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Error reading {path}: {e}")
        return ""


def check_chapter_consistency(cn_num: int, en_chapters: list, en_to_es_map: dict):
    """
    Checks a single CN chapter for consistency.
    """
    # 1. Read ES text
    es_path = OUTPUT_DIR / f"cn_{cn_num:04d}_es.txt"
    if not es_path.exists():
        logger.warning(f"⚠️  [MISSING] ES translation for Chapter {cn_num} not found at {es_path}")
        return

    es_text = read_file(es_path)
    
    # 2. Read EN texts
    en_text_combined = ""
    found_en_files = 0
    for en_stem in en_chapters:
        # Try known patterns
        candidates = [
            EN_DIR / f"{en_stem}_en.txt",
            EN_DIR / f"chapter-{en_stem}.txt"
        ]
        # Also try as int aligned
        try:
             align_int = int(en_stem)
             candidates.append(EN_DIR / f"{align_int:04d}_en.txt")
        except:
             pass

        file_found = False
        for p in candidates:
            if p.exists():
                en_text_combined += read_file(p) + "\n"
                file_found = True
                found_en_files += 1
                break
        
        if not file_found:
             logger.debug(f"   [DEBUG] EN source {en_stem} not found (tried {candidates})")

    if not en_text_combined:
        logger.warning(f"⚠️  [MISSING] No EN source files found for CN {cn_num} (looked for {en_chapters})")
        return

    # 3. Check consistency
    issues = []
    found_count = 0
    checked_count = 0

    # Optimization: iterate glossary
    for en_term, valid_es_terms in en_to_es_map.items():
        if len(en_term) < 4: # Skip very short terms
            continue
            
        # Check if EN term is in EN text (Case Sensitive)
        if en_term in en_text_combined:
            checked_count += 1
            # EN term found. Now check if ANY valid ES term is in ES text.
            is_present_in_es = False
            for es_term in valid_es_terms:
                if es_term in es_text:
                    is_present_in_es = True
                    break
            
            if not is_present_in_es:
                issues.append({
                    "en": en_term,
                    "expected": list(valid_es_terms)
                })
            else:
                found_count += 1

    if issues:
        logger.info(f"🔴 Consistency Issues for Chapter {cn_num}:")
        # Sort issues by EN term length (as shorter might be subsets of longer)
        issues.sort(key=lambda x: len(x["en"]), reverse=True)
        
        # Limit output to top 10 to avoid flooding console
        for i, issue in enumerate(issues[:10]):
            logger.info(f"   - Found EN '{issue['en']}' but MISSING ES {issue['expected']}")
        if len(issues) > 10:
            logger.info(f"   ... and {len(issues) - 10} more issues.")
    else:
        logger.info(f"🟢 Chapter {cn_num}: OK ({found_count}/{checked_count} terms matches)")

def main():
    parser = argparse.ArgumentParser(description="Check translation consistency.")
    parser.add_argument("--start", type=int, required=True, help="Start CN chapter number")
    parser.add_argument("--end", type=int, required=True, help="End CN chapter number")
    args = parser.parse_args()

    # logger.info("Loading glossary and alignment map...")
    en_to_es = load_master_glossary_en_to_es()
    alignment = load_alignment_map()
    
    logger.info(f"Loaded Master Glossary: {len(en_to_es)} unique EN terms.")
    
    for cn_num in range(args.start, args.end + 1):
        en_chapters = alignment.get(cn_num)
        if not en_chapters:
            # Maybe it's mapped via string key in alignment map directly?
            # We handled that in load_alignment_map by ensuring int keys
            logger.warning(f"⚠️  No EN alignment found for CN {cn_num}")
            continue
            
        check_chapter_consistency(cn_num, en_chapters, en_to_es)

if __name__ == "__main__":
    main()
