#!/usr/bin/env python3
"""
Batch-extract CN→EN name glossaries for all chapters with EN alignment.

This populates data/name_glossary_cache/ with extracted glossaries,
which are then used by build_master_glossary.py to create the master
glossary with much higher confidence (more votes per term).

Usage:
    python scripts/batch_extract_glossary.py
    python scripts/batch_extract_glossary.py --start 100 --end 200
    python scripts/batch_extract_glossary.py --max-concurrent 5
    python scripts/batch_extract_glossary.py --force  # re-extract even if cached
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from utils.file_manager import load_env_file
load_env_file(ROOT / ".env")

CN_DIR = ROOT / "data" / "cn_raws"
EN_DIR = ROOT / "output" / "tribulation"
CACHE_DIR = ROOT / "data" / "name_glossary_cache"
ALIGNMENT_PATH = ROOT / "data" / "alignment_map.json"
SETTINGS_PATH = ROOT / "settings.yaml"

# Reuse extraction logic from translate_cn
from translate_cn import (
    extract_name_glossary,
    parse_cn_chapter,
    read_en_chapters_full,
)


def load_alignment_map() -> dict[int, list[int]]:
    """Returns {cn_num: [en_num, ...]}."""
    with ALIGNMENT_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    result: dict[int, list[int]] = {}
    for en_str, cn_num in raw.items():
        result.setdefault(int(cn_num), []).append(int(en_str))
    for nums in result.values():
        nums.sort()
    return result


def setup_adapter():
    """Create the LLM adapter from settings."""
    import yaml
    from adapters import get_adapter

    if SETTINGS_PATH.exists():
        with SETTINGS_PATH.open("r", encoding="utf-8") as f:
            settings = yaml.safe_load(f) or {}
    else:
        settings = {}

    adapter_cfg = settings.get("adapter", {})
    adapter_name = adapter_cfg.get("active", "gemini")
    return get_adapter(adapter_name, adapter_cfg)


async def extract_one(
    cn_num: int,
    cn_path: Path,
    en_nums: list[int],
    adapter,
    semaphore: asyncio.Semaphore,
    logger: logging.Logger,
    force: bool = False,
) -> tuple[int, int]:
    """Extract glossary for one chapter. Returns (cn_num, num_terms)."""
    cache_path = CACHE_DIR / f"cn_{cn_num:04d}.json"

    # Skip if cached (unless --force)
    if cache_path.exists() and not force:
        try:
            with cache_path.open("r", encoding="utf-8") as f:
                cached = json.load(f)
            return cn_num, len(cached)
        except (json.JSONDecodeError, OSError):
            pass

    # Parse CN chapter
    _, paragraphs = parse_cn_chapter(cn_path)
    if not paragraphs:
        return cn_num, 0

    cn_text = "\n".join(paragraphs)

    # Read EN chapters
    en_texts = read_en_chapters_full(EN_DIR, en_nums)
    if not en_texts:
        return cn_num, 0

    async with semaphore:
        glossary = await extract_name_glossary(
            cn_text=cn_text,
            en_texts=en_texts,
            adapter=adapter,
            cache_path=cache_path,
            logger=logger,
        )

    return cn_num, len(glossary)


async def main_async():
    parser = argparse.ArgumentParser(
        description="Batch-extract CN→EN name glossaries"
    )
    parser.add_argument("--start", type=int, default=2,
                        help="Start CN chapter (default: 2)")
    parser.add_argument("--end", type=int, default=706,
                        help="End CN chapter (default: 706)")
    parser.add_argument("--max-concurrent", type=int, default=5,
                        help="Max concurrent LLM calls (default: 5)")
    parser.add_argument("--force", action="store_true",
                        help="Re-extract even if cached")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger("batch_extract")

    # Load alignment map
    alignment_map = load_alignment_map()
    logger.info("Alignment map: %d CN chapters with EN reference", len(alignment_map))

    # Filter to chapters in range that have EN alignment and CN raw files
    chapters = []
    for cn_num in range(args.start, args.end + 1):
        if cn_num not in alignment_map:
            continue
        cn_path = CN_DIR / f"cn_{cn_num:04d}.txt"
        if not cn_path.exists():
            continue
        chapters.append((cn_num, cn_path, alignment_map[cn_num]))

    # Count how many are already cached
    already_cached = 0
    to_extract = 0
    for cn_num, _, _ in chapters:
        cache_path = CACHE_DIR / f"cn_{cn_num:04d}.json"
        if cache_path.exists() and not args.force:
            already_cached += 1
        else:
            to_extract += 1

    logger.info(
        "Chapters %d-%d: %d total, %d ya en cache, %d por extraer",
        args.start, args.end, len(chapters), already_cached, to_extract,
    )

    if to_extract == 0:
        logger.info("Nada que extraer. Usa --force para re-extraer.")
        return

    # Setup adapter
    adapter = setup_adapter()
    logger.info("Adapter: %s", adapter.model_name)

    # Run extraction with concurrency control
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(args.max_concurrent)

    t0 = time.time()
    tasks = [
        extract_one(cn_num, cn_path, en_nums, adapter, semaphore, logger, args.force)
        for cn_num, cn_path, en_nums in chapters
    ]

    results = []
    completed = 0
    for coro in asyncio.as_completed(tasks):
        cn_num, num_terms = await coro
        completed += 1
        if completed % 25 == 0 or completed == len(tasks):
            elapsed = time.time() - t0
            rate = completed / elapsed if elapsed > 0 else 0
            eta = (len(tasks) - completed) / rate if rate > 0 else 0
            logger.info(
                "Progreso: %d/%d (%.0f/min, ETA: %.0fmin)",
                completed, len(tasks), rate * 60, eta / 60,
            )
        results.append((cn_num, num_terms))

    elapsed = time.time() - t0
    total_terms = sum(n for _, n in results)
    extracted_new = sum(1 for _, n in results if n > 0)

    logger.info(
        "Completado en %.1fmin: %d capítulos, %d términos totales",
        elapsed / 60, extracted_new, total_terms,
    )


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
