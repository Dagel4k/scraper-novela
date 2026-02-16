import argparse
import asyncio
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Dict

import re
import warnings

# Silenciar warning de urllib3 (LibreSSL) antes de importar requests por transitividad
try:
    from urllib3.exceptions import NotOpenSSLWarning  # type: ignore
    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except Exception:
    pass

# Make project importable
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.translate_hybrid import (
    Glossary,
    restore_text,
    protect_text,
    apply_postprocess,
    chunk_paragraphs,
)

try:
    # Optional: reuse helper for Ollama
    from scraper.translate_hybrid import _ollama_chat
except Exception:
    _ollama_chat = None  # type: ignore

def _normalize_model(value: Optional[str]) -> str:
    if value is None:
        return ""
    v = value.strip().strip('"').strip("'")
    if v.lower() in {"", "none", "null", "false", "0"}:
        return ""
    return v

# Sin dependencia de OpenAI: este reparador usa únicamente Ollama/Qwen


def iter_txt_files(
    directory: Path,
    start: Optional[int] = None,
    end: Optional[int] = None,
    include_drafts: bool = False,
) -> Iterable[Path]:
    for p in sorted(directory.glob("*.txt")):
        if not p.is_file():
            continue
        if (not include_drafts) and "draft" in p.stem:
            continue
        name = p.stem  # e.g., 0389_es
        parts = name.split("_")
        num = None
        if parts and parts[0].isdigit():
            try:
                num = int(parts[0])
            except Exception:
                num = None
        if start is not None and num is not None and num < start:
            continue
        if end is not None and num is not None and num > end:
            continue
        yield p


def read_chapter_text(path: Path) -> Tuple[str, List[str]]:
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


def write_chapter_text(path: Path, title: str, paragraphs: List[str]) -> None:
    body = "\n\n".join(paragraphs)
    path.write_text(title.strip() + "\n\n" + body + "\n", encoding="utf-8")


LATAM_REGEX_REPLACEMENTS: List[Tuple[re.Pattern, str]] = [
    # Pronombres y formas peninsulares comunes (seguros)
    (re.compile(r"\bvosotros\b", re.IGNORECASE), "ustedes"),
    (re.compile(r"\bvosotras\b", re.IGNORECASE), "ustedes"),
    (re.compile(r"\bsois\b", re.IGNORECASE), "son"),
    (re.compile(r"\bestáis\b", re.IGNORECASE), "están"),
    (re.compile(r"\bhabéis\b", re.IGNORECASE), "han"),
    (re.compile(r"\bpodéis\b", re.IGNORECASE), "pueden"),
    (re.compile(r"\bqueréis\b", re.IGNORECASE), "quieren"),
    (re.compile(r"\bvale\b", re.IGNORECASE), "de acuerdo"),
    # Léxico típico, relativamente seguro
    (re.compile(r"\bordenador(es)?\b", re.IGNORECASE), r"computadora\1"),
]


def apply_latam_regex(text: str) -> str:
    out = text
    for pat, repl in LATAM_REGEX_REPLACEMENTS:
        out = pat.sub(repl, out)
    return out


def build_latam_system_prompt() -> str:
    return "\n".join([
        "Eres un editor profesional de español latino/neutro (América Latina).",
        "Recibes texto YA en español que puede contener giros peninsulares.",
        "Tu tarea: normalizar a español latino/neutro con correcciones mínimas de gramática y puntuación.",
        "",
        "Reglas estrictas:",
        "- No añadas ni elimines información. No resumas ni expliques.",
        "- Respeta párrafos, saltos de línea y formato de diálogos.",
        "- Evita regionalismos de España (p.ej., 'vosotros', 'vale', usos ambiguos de 'coger').",
        "- Prefiere 'ustedes' y vocabulario neutral común en América Latina.",
        "- Respeta tokens '<PROTECT_...>': no los modifiques ni traduzcas.",
    ])


async def refine_ollama(
    base_url: str,
    model: str,
    system_prompt: str,
    text: str,
    *,
    temperature: float = 0.2,
    timeout: Optional[float] = 180.0,
) -> str:
    if _ollama_chat is None:
        raise RuntimeError("Falta soporte Ollama. Asegúrate de poder importar scraper.translate_hybrid._ollama_chat.")
    return _ollama_chat(
        base_url,
        model,
        [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Ajusta a español latino/neutro (América Latina). Devuelve SOLO el texto, sin notas ni etiquetas.\n\n"
                    + text
                ),
            },
        ],
        temperature=temperature,
        timeout=timeout,
    )


async def process_file(
    p: Path,
    glossary: Glossary,
    *,
    ollama_url: str,
    ollama_model: str,
    chunk_chars: int,
    regex_pass: bool,
    verbose: bool,
    dry_run: bool,
) -> bool:
    if verbose:
        print(f"[proc] {p.name}")
    original = p.read_text(encoding="utf-8")

    # 1) Repara placeholders deformados
    fixed = restore_text(original, glossary)

    # 2) Pasada regex opcional para giros peninsulares evidentes
    if regex_pass:
        fixed = apply_latam_regex(fixed)

    # 3) Pasada IA con Ollama/Qwen
    if ollama_model:
        system_prompt = build_latam_system_prompt()
        title, paragraphs = read_chapter_text(p)
        # Process title + paragraphs por chunks
        chunks = []
        # Mantener orden: primero título como chunk propio pequeño
        if title.strip():
            chunks.append([title.strip()])
        # Divide cuerpo
        body_chunks = chunk_paragraphs(paragraphs, max_chars=chunk_chars)
        chunks.extend(body_chunks)

        refined_parts: List[str] = []
        for ci, ch in enumerate(chunks):
            txt = "\n\n".join(ch)
            txt = protect_text(txt, glossary)
            if verbose:
                print(f"  [ai] {p.name} chunk {ci+1}/{len(chunks)} ({len(txt)} chars)")
            out = await refine_ollama(ollama_url, ollama_model, system_prompt, txt)
            out = restore_text(out, glossary)
            out = apply_postprocess(out, glossary)
            refined_parts.append(out.strip())

        # Reconstruir (primer elemento es título)
        if refined_parts:
            new_title = refined_parts[0].splitlines()[0].strip()
            new_body = []
            for segment in refined_parts[1:]:
                # segment puede tener múltiples párrafos
                for s in segment.split("\n\n"):
                    s2 = s.strip()
                    if not s2:
                        continue
                    # Eliminar líneas de título espurias en el cuerpo
                    if re.match(r'^Cap(í|i)tulo\s+\d+\s*:', s2, re.IGNORECASE):
                        continue
                    new_body.append(s2)
            fixed = new_title + "\n\n" + "\n\n".join(new_body) + "\n"

    if fixed != original:
        if dry_run:
            if verbose:
                print(f"[would-fix] {p.name}")
            return True
        # Backup y escritura
        bak = p.with_suffix(p.suffix + ".bak")
        if not bak.exists():
            bak.write_text(original, encoding="utf-8")
        p.write_text(fixed, encoding="utf-8")
        if verbose:
            print(f"[fixed] {p.name}")
        return True
    else:
        if verbose:
            print(f"[skip] {p.name} (sin cambios)")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Repara placeholders y normaliza a español latino/neutro con una pasada opcional de IA.")
    ap.add_argument("--input", dest="input_dir", type=Path, required=True, help="Directorio con .txt traducidos")
    ap.add_argument("--glossary", type=Path, default=Path("config/translation_glossary.json"))
    # Si no quieres IA, pasa --ollama-model "" (vacío) para desactivarla
    ap.add_argument("--ollama-url", default="http://localhost:11434")
    ap.add_argument("--ollama-model", default="qwen2.5:7b")
    ap.add_argument("--chunk-chars", type=int, default=5000)
    ap.add_argument("--start", type=int, default=None, help="Capítulo inicial (incluido), según prefijo NNNN_")
    ap.add_argument("--end", type=int, default=None, help="Capítulo final (incluido), según prefijo NNNN_")
    ap.add_argument("--include-drafts", action="store_true", help="Incluir archivos *_draft_es.txt además de *_es.txt")
    ap.add_argument("--no-regex-pass", action="store_true", help="Desactiva la pasada regex previa a IA")
    ap.add_argument("--dry-run", action="store_true", help="No escribe cambios; solo reporta")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not args.input_dir.exists():
        print(f"[err] No existe el directorio: {args.input_dir}", file=sys.stderr)
        return 1

    glossary = Glossary.load(args.glossary)
    glossary.ensure_placeholders()

    # Normaliza el modelo (permite --ollama-model "" o none/null)
    args.ollama_model = _normalize_model(args.ollama_model)

    if args.verbose:
        print(f"[cfg] rango: {args.start}-{args.end} | drafts: {args.include_drafts} | modelo: {args.ollama_model or 'disabled'}")

    async def runner():
        changed = 0
        scanned = 0
        for p in iter_txt_files(args.input_dir, start=args.start, end=args.end, include_drafts=args.include_drafts):
            scanned += 1
            ok = await process_file(
                p,
                glossary,
                ollama_url=args.ollama_url,
                ollama_model=args.ollama_model,
                chunk_chars=args.chunk_chars,
                regex_pass=(not args.no_regex_pass),
                verbose=args.verbose,
                dry_run=args.dry_run,
            )
            if ok:
                changed += 1
        print(f"[done] Escaneados: {scanned} | Modificados: {changed}")

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        print("\n[interrupted] Cancelado por el usuario", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
