#!/usr/bin/env python3
"""Unified CLI for scraper-novela: scrape, translate, repair, export."""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import yaml

from adapters import get_adapter
from core.domain import Glossary, IngestGlossary, TranslationRecord
from core.text_processor import TextProcessor
from interfaces.translator import PromptBuilder, TranslationPipeline
from utils.file_manager import (
    append_jsonl,
    discover_chapter_files,
    load_env_file,
    load_jsonl,
    normalize_text,
)
from utils.logger import setup_logger


def load_settings(path: Path) -> dict:
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


# ═══════════════════════════════════════════════════════════════
#  SUBCOMMAND: scrape
# ═══════════════════════════════════════════════════════════════
def cmd_scrape(args: argparse.Namespace, settings: dict) -> int:
    from scraper.lightnovelpub import LightNovelPubScraper

    logger = setup_logger(verbose=True)
    scraper = LightNovelPubScraper(settings)

    output_dir = Path(args.output_dir or settings.get("output", {}).get("default_input_dir", "output/tribulation-of-myriad-races"))
    start = args.start
    end = args.end

    if end == 0:
        logger.info("Discovering total chapter count...")
        total = scraper.extract_total_chapters()
        if total is None:
            logger.error("Could not determine total. Use --end.")
            return 2
        end = total
        logger.info("Total discovered: %d", total)

    scraper.scrape_range(output_dir, start, end, resume=args.resume)
    return 0


# ═══════════════════════════════════════════════════════════════
#  SUBCOMMAND: translate
# ═══════════════════════════════════════════════════════════════
def cmd_translate(args: argparse.Namespace, settings: dict) -> int:
    logger = setup_logger(verbose=args.verbose, debug=args.debug)

    # Load .env
    env_path = Path(args.env_file) if args.env_file else Path(".env")
    load_env_file(env_path)

    # Load glossaries
    glossary_cfg = settings.get("glossary", {})
    glossary_path = Path(args.glossary or glossary_cfg.get("translation_glossary", "config/translation_glossary.json"))
    glossary = Glossary.load(glossary_path)
    glossary.ensure_placeholders()

    ingest_path = Path(args.ingest_glossary or glossary_cfg.get("ingest_glossary", "config/ingest_glossary.json"))
    ingest_glossary = IngestGlossary.load(ingest_path)

    # Translation settings
    trans_cfg = settings.get("translation", {})
    chunk_chars = args.chunk_chars or trans_cfg.get("chunk_chars", 7000)
    max_concurrent = args.max_concurrent or trans_cfg.get("max_concurrent", 3)
    temperature = args.temperature if args.temperature is not None else trans_cfg.get("temperature", 0.2)
    timeout = args.api_timeout or trans_cfg.get("timeout", 120)

    # Adapter
    adapter_cfg = settings.get("adapter", {})
    adapter_name = args.adapter or adapter_cfg.get("active", "gemini")
    try:
        adapter = get_adapter(adapter_name, adapter_cfg)
    except (RuntimeError, ValueError) as e:
        logger.error("%s", e)
        return 1
    logger.info("Adapter: %s (%s)", adapter.adapter_name, adapter.model_name)

    # Build pipeline components
    tp = TextProcessor(glossary, ingest_glossary)
    pb = PromptBuilder(settings, glossary)
    pipeline = TranslationPipeline(
        adapter, tp, pb,
        chunk_chars=chunk_chars,
        max_concurrent=max_concurrent,
        temperature=temperature,
        timeout=timeout,
    )

    # Determine chapter range
    in_dir = Path(args.input_dir or settings.get("output", {}).get("default_input_dir", "output/tribulation-of-myriad-races"))
    out_dir = Path(args.output_dir or settings.get("output", {}).get("default_output_dir", "traduccion"))
    out_dir.mkdir(parents=True, exist_ok=True)

    if not in_dir.exists():
        logger.error("No existe input-dir: %s", in_dir)
        return 2

    idx = load_jsonl(in_dir / "index.jsonl")
    if not idx:
        files = sorted(in_dir.glob("*_en.txt"))
        numbers = [int(p.stem.split("_")[0]) for p in files if p.stem.split("_")[0].isdigit()]
        end_auto = max(numbers) if numbers else 0
    else:
        end_auto = max(item.get("number", 0) for item in idx)

    start = args.start
    end = args.end if args.end > 0 else end_auto
    if end < start:
        logger.error("Rango inválido: start=%d end=%d", start, end)
        return 2

    logger.info("Translating chapters %d..%d with %s", start, end, adapter_name)

    async def run_all():
        for n in range(start, end + 1):
            en_name = f"{str(n).zfill(4)}_en.txt"
            es_name = f"{str(n).zfill(4)}_es.txt"
            in_path = in_dir / en_name
            out_path = out_dir / es_name

            if args.resume and out_path.exists():
                logger.info("[skip] %d already translated", n)
                continue
            if not in_path.exists():
                logger.warning("[miss] %d no existe (%s)", n, en_name)
                continue

            try:
                chapter = tp.read_chapter(in_path, number=n)
                logger.info("[chap] %d: %d párrafos", n, len(chapter.paragraphs))

                result = await pipeline.translate_chapter(chapter)

                tp.write_chapter(out_path, result.title_es, result.paragraphs_es)

                rec = TranslationRecord(
                    number=n,
                    title_en=result.title_en,
                    title_es=result.title_es,
                    file_en=en_name,
                    file_es=es_name,
                    input_dir=str(in_dir),
                    output_dir=str(out_dir),
                    length_en=sum(len(p) for p in chapter.paragraphs),
                    length_es=sum(len(p) for p in result.paragraphs_es),
                    model=result.model,
                    adapter_name=result.adapter_name,
                    translated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                )
                append_jsonl(out_dir / "index_es.jsonl", rec.model_dump())
                print(f"[ok] {n}: {es_name}", flush=True)
            except Exception as e:
                logger.error("[err] %d: %s", n, e)
                if args.debug:
                    import traceback
                    traceback.print_exc()

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        print("\n[interrupted]", file=sys.stderr)
        return 130

    print("[done] Traducción completada.", flush=True)
    return 0


# ═══════════════════════════════════════════════════════════════
#  SUBCOMMAND: repair
# ═══════════════════════════════════════════════════════════════
def cmd_repair(args: argparse.Namespace, settings: dict) -> int:
    logger = setup_logger(verbose=True)

    glossary_cfg = settings.get("glossary", {})
    glossary_path = Path(args.glossary or glossary_cfg.get("translation_glossary", "config/translation_glossary.json"))
    glossary = Glossary.load(glossary_path)
    glossary.ensure_placeholders()
    tp = TextProcessor(glossary)

    input_dir = Path(args.input)
    if not input_dir.exists():
        logger.error("No existe: %s", input_dir)
        return 1

    changed = 0
    scanned = 0
    for p in sorted(input_dir.glob("*.txt")):
        if not p.is_file():
            continue
        scanned += 1
        original = p.read_text(encoding="utf-8")
        fixed = tp.restore_text(original)
        if fixed != original:
            changed += 1
            if args.dry_run:
                print(f"[would-fix] {p.name}")
            else:
                p.write_text(fixed, encoding="utf-8")
                print(f"[fixed] {p.name}")

    print(f"[done] Escaneados: {scanned} | Modificados: {changed}")
    return 0


# ═══════════════════════════════════════════════════════════════
#  SUBCOMMAND: export pdf / epub
# ═══════════════════════════════════════════════════════════════
def _load_ingest_patterns(path: str) -> Optional[Sequence[Tuple[re.Pattern, str]]]:
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    rep = data.get("replace") or {}
    if not isinstance(rep, dict) or not rep:
        return None
    items = sorted(rep.items(), key=lambda kv: len(kv[0]), reverse=True)
    pats: List[Tuple[re.Pattern, str]] = []
    for src, dst in items:
        pats.append((re.compile(rf"(?<!\w){re.escape(src)}(?!\w)"), dst))
    return pats


def cmd_export(args: argparse.Namespace, settings: dict) -> int:
    logger = setup_logger(verbose=True)
    fmt = args.format

    out_cfg = settings.get("output", {})
    fmt_cfg = out_cfg.get(fmt, {})

    input_dir = args.input or "traduccion"
    output_dir = args.output or f"output/{fmt}s"
    block_size = args.block_size or fmt_cfg.get("block_size", 50)
    basename = args.basename or fmt_cfg.get("basename", "novela")
    title_keywords = fmt_cfg.get("title_keywords", ["Capítulo", "Capitulo"])

    glossary_cfg = settings.get("glossary", {})
    ingest_path = args.ingest_glossary or glossary_cfg.get("ingest_glossary", "config/ingest_glossary.json")
    ingest_patterns = _load_ingest_patterns(ingest_path)

    chapters = discover_chapter_files(Path(input_dir))
    if not chapters:
        logger.error("No se encontraron archivos .txt en %s", input_dir)
        return 1

    # Apply range filter
    if args.range:
        try:
            a, b = args.range.split("-", 1)
            lo, hi = int(a), int(b)
            if lo > hi:
                lo, hi = hi, lo
            chapters = [(n, p) for n, p in chapters if lo <= n <= hi]
        except Exception:
            logger.error("Formato de --range inválido. Usa: 51-100")
            return 1

    # Convert to list of (int, str) for compatibility with existing generators
    chapter_list = [(n, str(p)) for n, p in chapters]

    def chunk_list(seq, size):
        return [seq[i:i + size] for i in range(0, len(seq), size)]

    groups = chunk_list(chapter_list, block_size)

    if fmt == "pdf":
        _export_pdfs(groups, output_dir, title_keywords, basename, args, ingest_patterns)
    elif fmt == "epub":
        _export_epubs(groups, output_dir, title_keywords, basename, args, ingest_patterns)
    else:
        logger.error("Formato desconocido: %s", fmt)
        return 1

    print("Listo.")
    return 0


def _export_pdfs(groups, output_dir, title_keywords, basename, args, ingest_patterns):
    # Import the PDF generation function from the legacy module if available,
    # otherwise use a minimal implementation
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from scripts.generate_pdfs import write_chunk_to_pdf
    except ImportError:
        try:
            from _legacy.generate_pdfs import write_chunk_to_pdf
        except ImportError:
            print("[error] No se encontró generate_pdfs. Asegúrate de que scripts/generate_pdfs.py o _legacy/generate_pdfs.py existe.", file=sys.stderr)
            return

    out_cfg_pdf = {}
    cover = getattr(args, "cover", None)
    font_regular = getattr(args, "font_regular", "fonts/times-new-roman.ttf")
    font_italic = getattr(args, "font_italic", "fonts/times-new-roman-italic.ttf")
    font_bold = getattr(args, "font_bold", "fonts/times-new-roman-bold.ttf")

    for group in groups:
        first_ch, last_ch = group[0][0], group[-1][0]
        out_name = f"{basename}_{first_ch:04d}-{last_ch:04d}.pdf"
        out_path = os.path.join(output_dir, out_name)
        print(f"Generando {out_path} ({first_ch}-{last_ch})...")
        write_chunk_to_pdf(
            group, out_path, title_keywords,
            cover_path=cover,
            add_toc=True,
            ingest_glossary=ingest_patterns,
            user_font_path=font_regular,
            user_font_italic_path=font_italic,
            user_font_bold_path=font_bold,
        )


def _export_epubs(groups, output_dir, title_keywords, basename, args, ingest_patterns):
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from scripts.generate_epubs import create_epub_for_group
    except ImportError:
        try:
            from _legacy.generate_epubs import create_epub_for_group
        except ImportError:
            print("[error] No se encontró generate_epubs.", file=sys.stderr)
            return

    cover = getattr(args, "cover", None)

    for group in groups:
        first_ch, last_ch = group[0][0], group[-1][0]
        out_name = f"{basename}_{first_ch:04d}-{last_ch:04d}.epub"
        out_path = os.path.join(output_dir, out_name)
        print(f"Generando {out_path} ({first_ch}-{last_ch})...")
        create_epub_for_group(group, out_path, title_keywords, cover, ingest_patterns)


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scraper-novela",
        description="Pipeline unificado: scrape → translate → repair → export",
    )
    parser.add_argument(
        "--config", default="config/settings.yaml",
        help="Ruta a settings.yaml (default: config/settings.yaml)",
    )

    subs = parser.add_subparsers(dest="command", required=True)

    # ── scrape ──────────────────────────────────────────────────
    sp_scrape = subs.add_parser("scrape", help="Descargar capítulos de LightNovelPub")
    sp_scrape.add_argument("--start", type=int, default=1)
    sp_scrape.add_argument("--end", type=int, default=0)
    sp_scrape.add_argument("--output-dir", default=None)
    sp_scrape.add_argument("--resume", action="store_true")

    # ── translate ───────────────────────────────────────────────
    sp_trans = subs.add_parser("translate", help="Traducir capítulos EN→ES")
    sp_trans.add_argument("--start", type=int, default=1)
    sp_trans.add_argument("--end", type=int, default=0)
    sp_trans.add_argument("--input-dir", default=None)
    sp_trans.add_argument("--output-dir", default=None)
    sp_trans.add_argument("--adapter", default=None, help="Adapter: gemini (default from settings)")
    sp_trans.add_argument("--glossary", default=None)
    sp_trans.add_argument("--ingest-glossary", default=None)
    sp_trans.add_argument("--chunk-chars", type=int, default=None)
    sp_trans.add_argument("--max-concurrent", type=int, default=None)
    sp_trans.add_argument("--temperature", type=float, default=None)
    sp_trans.add_argument("--api-timeout", type=float, default=None)
    sp_trans.add_argument("--resume", action="store_true")
    sp_trans.add_argument("--env-file", default=".env")
    sp_trans.add_argument("--verbose", action="store_true")
    sp_trans.add_argument("--debug", action="store_true")

    # ── repair ──────────────────────────────────────────────────
    sp_repair = subs.add_parser("repair", help="Reparar placeholders en archivos traducidos")
    sp_repair.add_argument("--input", default="traduccion")
    sp_repair.add_argument("--glossary", default=None)
    sp_repair.add_argument("--dry-run", action="store_true")

    # ── export ──────────────────────────────────────────────────
    sp_export = subs.add_parser("export", help="Exportar a PDF o EPUB")
    sp_export.add_argument("format", choices=["pdf", "epub"])
    sp_export.add_argument("--input", default=None)
    sp_export.add_argument("--output", default=None)
    sp_export.add_argument("--block-size", type=int, default=None)
    sp_export.add_argument("--basename", default=None)
    sp_export.add_argument("--cover", default=None)
    sp_export.add_argument("--range", default=None)
    sp_export.add_argument("--ingest-glossary", default=None)
    sp_export.add_argument("--font-regular", default="fonts/times-new-roman.ttf")
    sp_export.add_argument("--font-italic", default="fonts/times-new-roman-italic.ttf")
    sp_export.add_argument("--font-bold", default="fonts/times-new-roman-bold.ttf")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = load_settings(Path(args.config))

    commands = {
        "scrape": cmd_scrape,
        "translate": cmd_translate,
        "repair": cmd_repair,
        "export": cmd_export,
    }

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
