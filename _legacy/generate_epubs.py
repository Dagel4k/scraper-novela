#!/usr/bin/env python3
import argparse
import os
import re
from typing import List, Tuple, Optional, Sequence

TITLE_REGEX = re.compile(r"^\s*(Cap[ií]tulo|Chapter)\b", re.IGNORECASE)
CHAPTER_NUM_IN_NAME = re.compile(r"^(\d+)")
CHAPTER_NUM_IN_TEXT = re.compile(r"Cap[ií]tulo\s+(\d+)", re.IGNORECASE)


def normalize_text(s: str) -> str:
    replacements = {
        "\u201c": '"',  # “
        "\u201d": '"',  # ”
        "\u2018": "'",  # ‘
        "\u2019": "'",  # ’
        "\u00ab": '"',  # «
        "\u00bb": '"',  # »
        # Conservamos el em dash —
        # "\u2014": "--",
        "\u2013": "-",
        "\u2026": "...",
        "\u00a0": " ",
        "\ufeff": "",
    }
    return s.translate(str.maketrans(replacements))


def detect_chapter_number_from_name(filename: str) -> Optional[int]:
    m = CHAPTER_NUM_IN_NAME.search(os.path.basename(filename))
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def detect_chapter_number_from_file(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            for _ in range(30):
                line = f.readline()
                if not line:
                    break
                m = CHAPTER_NUM_IN_TEXT.search(line)
                if m:
                    try:
                        return int(m.group(1))
                    except ValueError:
                        return None
    except Exception:
        return None
    return None


def list_chapter_files(input_dir: str) -> List[Tuple[int, str]]:
    files = [
        os.path.join(input_dir, name)
        for name in os.listdir(input_dir)
        if name.lower().endswith(".txt")
    ]
    # Reúne candidatos por número de capítulo, prefiriendo versiones finales *_es.txt
    by_num: dict[int, str] = {}
    for path in files:
        num = detect_chapter_number_from_name(path)
        if num is None:
            num = detect_chapter_number_from_file(path)
        if num is None:
            continue
        name_lower = os.path.basename(path).lower()
        is_draft = "draft" in name_lower
        prev = by_num.get(num)
        if prev is None:
            by_num[num] = path
        else:
            # Si ya hay uno, preferir el que NO sea draft
            if "draft" in os.path.basename(prev).lower() and not is_draft:
                by_num[num] = path
            # Si ambos son del mismo tipo, deja el existente
    chapters = sorted(by_num.items(), key=lambda t: t[0])
    return chapters


def chunk(seq: List[Tuple[int, str]], size: int) -> List[List[Tuple[int, str]]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def line_to_html_paragraph(line: str) -> str:
    # Añade em dash antes de primera comilla si el párrafo comienza con comillas
    s = line
    leading = len(s) - len(s.lstrip())
    if s.lstrip().startswith('"'):
        idx = s.find('"', leading)
        if idx != -1 and (idx == 0 or s[idx - 1] != '—'):
            s = s[:idx] + '—' + s[idx:]

    # Italic sólo entre comillas; alterna por pares
    out = []
    ital = False
    buf = []
    for ch in s:
        if ch == '"':
            if buf:
                seg = html_escape(''.join(buf))
                if ital:
                    out.append(f"<em>{seg}</em>")
                else:
                    out.append(seg)
                buf = []
            # La comilla literal
            out.append('&quot;')
            ital = not ital
        else:
            buf.append(ch)
    if buf:
        seg = html_escape(''.join(buf))
        if ital:
            out.append(f"<em>{seg}</em>")
        else:
            out.append(seg)
    # Sangría CSS se maneja por estilos; aquí solo devolvemos el contenido
    return "".join(out)


def read_chapter_processed(
    path: str,
    title_keywords_lower: Sequence[str],
    ingest_glossary: Optional[Sequence[Tuple[re.Pattern, str]]],
) -> Tuple[str, List[str]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        with open(path, "r", encoding="latin-1", errors="ignore") as f:
            lines = f.readlines()

    processed: List[str] = []
    first_title: Optional[str] = None

    for raw in lines:
        text = normalize_text(raw.rstrip("\n"))
        if ingest_glossary:
            for pat, repl in ingest_glossary:
                text = pat.sub(repl, text)
        s = text.strip()
        if not s:
            processed.append("")
            continue
        is_title = False
        if TITLE_REGEX.search(s) or any(s.lower().startswith(k) for k in title_keywords_lower):
            if first_title is None:
                first_title = s
                continue  # No incluir aquí; lo pondremos como <h1>
            else:
                # Duplicado: saltar
                continue
        processed.append(text)

    if first_title is None:
        first_title = os.path.basename(path)
    return first_title, processed


def create_epub_for_group(
    group: List[Tuple[int, str]],
    out_path: str,
    title_keywords: Sequence[str],
    cover_path: Optional[str],
    ingest_glossary: Optional[Sequence[Tuple[re.Pattern, str]]],
):
    try:
        from ebooklib import epub
    except Exception:
        raise SystemExit("Falta la librería 'ebooklib'. Instálala con: pip install ebooklib")

    title_keywords_lower = [k.lower() for k in title_keywords]
    first_ch, last_ch = group[0][0], group[-1][0]
    book = epub.EpubBook()
    book.set_title(f"Novela {first_ch:04d}-{last_ch:04d}")
    book.set_language('es')

    # CSS simple embebido
    css = (
        "body{font-family: serif; line-height:1.6;}" \
        "h1{margin:0 0 0.6em 0; font-size:1.6em;}" \
        "p{ text-indent:1.5em; margin:0 0 0.6em 0;}"
    )
    style = epub.EpubItem(uid="style_nav", file_name="style/style.css", media_type="text/css", content=css.encode("utf-8"))
    book.add_item(style)

    if cover_path and os.path.exists(cover_path):
        with open(cover_path, 'rb') as f:
            book.set_cover("cover.jpg", f.read())

    spine = ['nav']
    toc = []

    for chap_num, path in group:
        h1, body_lines = read_chapter_processed(path, title_keywords_lower, ingest_glossary)
        # Construir HTML del capítulo
        paras = []
        for ln in body_lines:
            if ln.strip() == "":
                paras.append("<p>&nbsp;</p>")
            else:
                paras.append(f"<p>{line_to_html_paragraph(ln)}</p>")
        html = (
            "<html xmlns='http://www.w3.org/1999/xhtml'><head>"
            "<meta charset='utf-8'/><title>" + html_escape(h1) + "</title>"
            "<link rel='stylesheet' type='text/css' href='../style/style.css'/></head><body>"
            f"<h1>{html_escape(h1)}</h1>" + "".join(paras) + "</body></html>"
        )

        item = epub.EpubHtml(title=h1, file_name=f"chapters/{chap_num:04d}.xhtml", lang='es')
        item.set_content(html)
        item.add_item(style)
        book.add_item(item)
        spine.append(item)
        toc.append(item)

    book.toc = tuple(toc)
    book.spine = spine

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    epub.write_epub(out_path, book)


def main():
    parser = argparse.ArgumentParser(description="Genera EPUBs por bloques desde TXT")
    parser.add_argument("--input", default="traduccion", help="Carpeta con .txt (por defecto: traduccion)")
    parser.add_argument("--output", default=os.path.join("output", "epub"), help="Carpeta de salida (por defecto: output/epub)")
    parser.add_argument("--block-size", type=int, default=50, help="Capítulos por EPUB (por defecto 50)")
    parser.add_argument("--basename", default="novela", help="Prefijo del nombre (por defecto: novela)")
    parser.add_argument("--title-keywords", default="Capítulo,Capitulo", help="Palabras clave de título separadas por coma")
    parser.add_argument("--cover", default="", help="Ruta de portada (opcional)")
    parser.add_argument("--range", default="", help="Rango de capítulos a incluir, ej. 51-100")
    parser.add_argument("--ingest-glossary", default=os.path.join("config", "ingest_glossary.json"), help="JSON con 'replace': {original: traducción}")

    args = parser.parse_args()
    input_dir = args.input
    output_dir = args.output
    block = max(1, args.block_size)
    title_keywords = [s.strip() for s in args.title_keywords.split(',') if s.strip()]

    if not os.path.isdir(input_dir):
        raise SystemExit(f"No existe la carpeta de entrada: {input_dir}")

    chapters = list_chapter_files(input_dir)
    if not chapters:
        raise SystemExit("No se encontraron .txt con número de capítulo")

    if args.range:
        try:
            a, b = args.range.split('-', 1)
            lo, hi = int(a), int(b)
            if lo > hi:
                lo, hi = hi, lo
            chapters = [c for c in chapters if lo <= c[0] <= hi]
        except Exception:
            raise SystemExit("Formato de --range inválido; usa p.ej. 51-100")

    # Cargar glosario
    ingest_patterns: Optional[Sequence[Tuple[re.Pattern, str]]] = None
    if args.ingest_glossary and os.path.exists(args.ingest_glossary):
        import json
        with open(args.ingest_glossary, 'r', encoding='utf-8') as f:
            data = json.load(f)
        rep = data.get('replace') or {}
        if isinstance(rep, dict) and rep:
            items = sorted(rep.items(), key=lambda kv: len(kv[0]), reverse=True)
            pats = []
            for src, dst in items:
                pats.append((re.compile(rf"(?<!\w){re.escape(src)}(?!\w)"), dst))
            ingest_patterns = pats

    groups = chunk(chapters, block)
    for group in groups:
        first_ch, last_ch = group[0][0], group[-1][0]
        out_name = f"{args.basename}_{first_ch:04d}-{last_ch:04d}.epub"
        out_path = os.path.join(output_dir, out_name)
        print(f"Generando {out_path} con capítulos {first_ch}-{last_ch}...")
        create_epub_for_group(group, out_path, title_keywords, args.cover if args.cover else None, ingest_patterns)

    print("Listo.")


if __name__ == "__main__":
    main()
