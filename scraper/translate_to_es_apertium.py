#!/usr/bin/env python3
"""
Traductor EN->ES usando Apertium (offline, gratuito).

- Requiere tener instalado `apertium` y el par de idiomas `en-es` (o `eng-spa`).
- Integra el mismo flujo de archivos que los otros traductores del repo:
  lee `NNNN_en.txt` y escribe `NNNN_es.txt`, actualizando `index_es.jsonl`.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Reutilizamos protección/restauración robusta desde el pipeline híbrido
from scraper.translate_hybrid import protect_text as _protect_text_robust, restore_text as _restore_text_robust


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
        # Crea placeholders para cada término que no se debe traducir
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
                # Clave base sin índice para restauración flexible
                base_key = f"<PROTECT_{slugify(term)}>"
                self.restore_tokens.setdefault(base_key, term)


def slugify(s: str) -> str:
    s2 = re.sub(r"[^A-Za-z0-9]+", "_", (s or "").strip())
    return re.sub(r"_+", "_", s2).strip("_").upper()[:40]


def protect_text(text: str, glossary: Glossary) -> str:
    return _protect_text_robust(text, glossary)


def restore_text(text: str, glossary: Glossary) -> str:
    return _restore_text_robust(text, glossary)


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


def write_chapter_es(dest_dir: Path, number: int, title_es: str, paragraphs_es: List[str]) -> str:
    fname = f"{str(number).zfill(4)}_es.txt"
    path = dest_dir / fname
    body = "\n\n".join(paragraphs_es)
    with path.open("w", encoding="utf-8") as f:
        f.write(title_es.strip() + "\n\n" + body + "\n")
    return fname


def chunk_paragraphs(paragraphs: List[str], max_chars: int = 12000) -> List[List[str]]:
    """Agrupa párrafos en bloques cercanos a max_chars para enviarlos a Apertium.
    Apertium es rápido; por simplicidad se puede usar un único chunk grande por capítulo.
    """
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


def ensure_apertium_available(apertium_bin: str) -> None:
    if not shutil.which(apertium_bin):
        raise RuntimeError(
            f"No se encontró el binario '{apertium_bin}'.\n"
            "Instalación sugerida en macOS (Homebrew):\n"
            "  brew install apertium apertium-en-es\n\n"
            "En Debian/Ubuntu:\n"
            "  sudo apt-get update && sudo apt-get install apertium-en-es\n\n"
            "Después de instalar, vuelve a ejecutar el comando."
        )


def run_apertium(
    text: str,
    *,
    pair: str = "en-es",
    apertium_bin: str = "apertium",
    format_opt: str = "none",
    extra_flags: Optional[List[str]] = None,
    timeout: Optional[float] = 120.0,
) -> str:
    """Ejecuta Apertium con el par especificado y devuelve la salida traducida.

    Por defecto usa `-u` para preservar palabras desconocidas/tokens tal cual.
    """
    ensure_apertium_available(apertium_bin)
    flags = ["-u", "-f", format_opt]
    if extra_flags:
        flags.extend(extra_flags)
    cmd = [apertium_bin] + flags + [pair]
    try:
        proc = subprocess.run(
            cmd,
            input=text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout or 120.0,
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError(f"No se pudo ejecutar '{apertium_bin}'. ¿Está instalado?")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Timeout ejecutando Apertium. Considera reducir --chunk-chars o aumentar --apertium-timeout.")

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        raise RuntimeError(f"Apertium devolvió código {proc.returncode}. Stderr: {err[:300]}")
    return (proc.stdout or "").strip()


def translate_chapter_apertium(
    title_en: str,
    paragraphs: List[str],
    glossary: Glossary,
    *,
    pair: str = "en-es",
    apertium_bin: str = "apertium",
    chunk_chars: int = 20000,
    apertium_timeout: Optional[float] = 180.0,
    format_opt: str = "none",
    verbose: bool = False,
) -> Tuple[str, List[str]]:
    # Traduce TODO el capítulo en un solo paso para evitar truncados
    # y inconsistencias de segmentación. Luego reconstruimos párrafos.
    # Nota: para Apertium evitamos placeholders; interferían con la salida completa.
    title_src = title_en
    body_src = "\n\n".join(paragraphs) if paragraphs else ""
    full_src = title_src + ("\n\n" + body_src if body_src else "")

    if verbose:
        print(f"[apertium] Traduciendo capítulo completo ({len(full_src)} chars)...", flush=True)

    out = run_apertium(
        full_src,
        pair=pair,
        apertium_bin=apertium_bin,
        format_opt=format_opt,
        timeout=apertium_timeout,
    )
    out = restore_text(out, glossary)
    out = apply_postprocess(out, glossary)

    # Segmentación tolerante: dividir por líneas en blanco (uno o más)
    parts = [p.strip() for p in re.split(r"\n\s*\n", out.strip()) if p.strip()]
    if not parts:
        # Fallback extremo: devuelve al menos el título original
        return (title_en, [])
    title_es = parts[0]
    paragraphs_es = parts[1:]
    return title_es, paragraphs_es


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


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Traducir capítulos EN->ES con Apertium (offline)")
    parser.add_argument("--input-dir", default="output/tribulation", help="Directorio con capítulos en inglés")
    parser.add_argument("--start", type=int, default=1, help="Capítulo inicial (incluido)")
    parser.add_argument("--end", type=int, default=0, help="Capítulo final (incluido). 0 = inferir de index.jsonl")
    parser.add_argument("--output-dir", default=None, help="Directorio de salida para los archivos traducidos")
    parser.add_argument("--resume", action="store_true", help="Omitir capítulos ya traducidos")
    parser.add_argument("--glossary", default="config/translation_glossary.json", help="Ruta a glosario JSON opcional")
    parser.add_argument("--chunk-chars", type=int, default=20000, help="Tamaño aprox. de chunk en caracteres para Apertium")
    parser.add_argument("--apertium-bin", default="apertium", help="Binario a usar para ejecutar Apertium")
    parser.add_argument("--pair", default="en-es", help="Par de idiomas de Apertium (p.ej., en-es o eng-spa)")
    parser.add_argument("--apertium-timeout", type=float, default=180.0, help="Timeout por ejecución de Apertium (segundos)")
    parser.add_argument("--format", default="none", help="Formato de entrada para Apertium (-f), por defecto 'none'")
    parser.add_argument("--verbose", action="store_true", help="Imprimir pasos detallados de progreso")
    parser.add_argument("--debug", action="store_true", help="Logs verbosos y trazas de error")

    args = parser.parse_args(argv)

    in_dir = Path(args.input_dir)
    if not in_dir.exists():
        print(f"[error] No existe input-dir: {in_dir}", file=sys.stderr)
        return 2

    idx = load_index(in_dir / "index.jsonl")
    if not idx:
        print("[warn] No se encontró index.jsonl o está vacío. Se listarán *_en.txt.", flush=True)
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

    out_dir = Path(args.output_dir) if args.output_dir else in_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.verbose:
        print(f"[out] Carpeta de salida: {out_dir}", flush=True)

    out_index_path = out_dir / "index_es.jsonl"
    out_index_file = out_index_path.open("a", encoding="utf-8")

    glossary_path = Path(args.glossary) if args.glossary else None
    glossary = Glossary.load(glossary_path)
    glossary.ensure_placeholders()

    # Prueba rápida de disponibilidad de Apertium
    try:
        ensure_apertium_available(args.apertium_bin)
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2

    if args.verbose:
        print(f"[cfg] Apertium: {args.apertium_bin} | Pair: {args.pair}", flush=True)
        print(f"[cfg] chunk-chars: {args.chunk_chars}", flush=True)

    for n in range(start, end + 1):
        en_name = f"{str(n).zfill(4)}_en.txt"
        es_name = f"{str(n).zfill(4)}_es.txt"
        in_path = in_dir / en_name
        out_path = out_dir / es_name
        if args.resume and out_path.exists():
            print(f"[skip] {n} ya traducido ({es_name})", flush=True)
            continue
        if not in_path.exists():
            print(f"[miss] {n} no existe ({en_name})", file=sys.stderr)
            continue

        try:
            title_en, paragraphs = read_chapter(in_path)
            if args.verbose:
                print(f"[chap] {n}: leído '{en_name}' | párrafos: {len(paragraphs)}", flush=True)

            # Aplicar traducciones forzadas del glosario ANTES de proteger (ingest simplificado)
            if glossary.translations:
                items = sorted(glossary.translations.items(), key=lambda kv: len(kv[0]), reverse=True)
                def _apply_ingest(s: str) -> str:
                    for src, dst in items:
                        if not src:
                            continue
                        s = re.sub(r"\b" + re.escape(src) + r"\b", dst, s)
                    return s
                title_en = _apply_ingest(title_en)
                paragraphs = [_apply_ingest(p) for p in paragraphs]

            t0 = time.time()
            title_es, paragraphs_es = translate_chapter_apertium(
                title_en,
                paragraphs,
                glossary,
                pair=args.pair,
                apertium_bin=args.apertium_bin,
                chunk_chars=args.chunk_chars,
                apertium_timeout=args.apertium_timeout,
                format_opt=args.format,
                verbose=args.verbose,
            )
            dt = time.time() - t0

            if args.verbose:
                print(f"[write] {n}: Título traducido: '{title_es[:80]}'...", flush=True)
                print(f"[write] {n}: Párrafos traducidos: {len(paragraphs_es)}", flush=True)
                print(f"[time] {n}: capítulo traducido en {dt:.1f}s", flush=True)

            saved = write_chapter_es(out_dir, n, title_es, paragraphs_es)
            rec = {
                "number": n,
                "title_en": title_en,
                "title_es": title_es,
                "file_en": en_name,
                "file_es": saved,
                "input_dir": str(in_dir),
                "output_dir": str(out_dir),
                "length_en": sum(len(p) for p in paragraphs),
                "length_es": sum(len(p) for p in paragraphs_es),
                "model": f"apertium:{args.pair}",
                "provider": "apertium",
                "translated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            out_index_file.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out_index_file.flush()
            print(f"[ok] {n}: {saved}", flush=True)
        except Exception as e:
            print(f"[err] {n}: {e}", file=sys.stderr)
            if args.debug:
                traceback.print_exc()

    out_index_file.close()
    print("[done] Traducción completada (Apertium).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
