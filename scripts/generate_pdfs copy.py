#!/usr/bin/env python3
import argparse
import os
import re
import math
from typing import List, Tuple, Optional, Dict, Sequence

try:
    from fpdf import FPDF  # Provided by fpdf2 (write_html incluido desde v2.6)
except Exception as e:  # pragma: no cover
    FPDF = None


TITLE_REGEX = re.compile(r"^\s*(Cap[ií]tulo|Chapter)\b", re.IGNORECASE)
CHAPTER_NUM_IN_NAME = re.compile(r"^(\d+)")
CHAPTER_NUM_IN_TEXT = re.compile(r"Cap[ií]tulo\s+(\d+)", re.IGNORECASE)


def normalize_text(s: str) -> str:
    # Normalize curly quotes, dashes, ellipsis, NBSP to ASCII/compatible chars
    replacements = {
        "\u201c": '"',  # “
        "\u201d": '"',  # ”
        "\u2018": "'",  # ‘
        "\u2019": "'",  # ’
        "\u00ab": '"',  # «
        "\u00bb": '"',  # »
        # Conservamos em dash —; lo renderizamos con la fuente proporcionada
        # "\u2014": "--",  # — em dash
        "\u2013": "-",   # – en dash
        "\u2026": "...",  # … ellipsis
        "\u00a0": " ",  # NBSP
        "\ufeff": "",   # BOM
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
            for _ in range(30):  # scan a few first lines
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
    chapters: List[Tuple[int, str]] = []
    for path in files:
        num = detect_chapter_number_from_name(path)
        if num is None:
            num = detect_chapter_number_from_file(path)
        if num is None:
            # Skip files without detectable chapter number
            continue
        chapters.append((num, path))

    chapters.sort(key=lambda t: t[0])
    return chapters


def chunk(seq: List[Tuple[int, str]], size: int) -> List[List[Tuple[int, str]]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def ensure_fpdf_available():
    if FPDF is None:
        raise SystemExit(
            "No se encontró la librería 'fpdf2'. Instálala con: pip install fpdf2"
        )

from html import escape as html_escape


def to_html_with_dialogue_italics(line: str, italic_open: bool, dash_markup: str = "—") -> tuple[str, bool]:
    # Convierte una línea de texto en HTML, aplicando <i> solo al contenido entre comillas
    # Dobles (") y manteniendo las comillas sin formatear. El estado italic_open
    # persiste entre líneas.
    parts = []
    buf = []
    italic = italic_open
    for ch in line:
        if ch == '"':
            if buf:
                txt = ''.join(buf)
                if italic:
                    parts.append(f"<i>{html_escape(txt)}</i>")
                else:
                    parts.append(html_escape(txt))
                buf = []
            # Si abrimos diálogo ahora (italic False -> True), insertar guion largo antes de la comilla
            if not italic:
                parts.append(dash_markup)
            # Agregar la comilla literal sin estilo
            parts.append(html_escape(ch))
            # Alternar modo cursiva
            italic = not italic
        else:
            buf.append(ch)
    if buf:
        txt = ''.join(buf)
        if italic:
            parts.append(f"<i>{html_escape(txt)}</i>")
        else:
            parts.append(html_escape(txt))
    return ''.join(parts), italic


def _find_default_tnr_paths() -> Optional[Dict[str, str]]:
    # Ya no autodetectamos Times New Roman por defecto para evitar problemas
    return None


def _register_tnr(pdf: FPDF, tnr_dir: Optional[str]) -> tuple[str, bool]:
    # Returns (family_name, ok)
    paths: Optional[Dict[str, str]] = None
    if tnr_dir:
        files = {
            "": os.path.join(tnr_dir, "Times New Roman.ttf"),
            "B": os.path.join(tnr_dir, "Times New Roman Bold.ttf"),
            "I": os.path.join(tnr_dir, "Times New Roman Italic.ttf"),
            "BI": os.path.join(tnr_dir, "Times New Roman Bold Italic.ttf"),
        }
        if all(os.path.exists(p) for p in files.values()):
            paths = files
    if paths is None:
        return "Helvetica", False
    try:
        pdf.add_font("TNR", "", paths[""])
        pdf.add_font("TNR", "B", paths["B"])
        pdf.add_font("TNR", "I", paths["I"])
        pdf.add_font("TNR", "BI", paths["BI"])
        return "TNR", True
    except Exception:
        return "Helvetica", False


def write_chunk_to_pdf(
    chunk_items: List[Tuple[int, str]],
    out_path: str,
    title_keywords: List[str],
    cover_path: Optional[str] = None,
    add_toc: bool = True,
    dash_font_path: Optional[str] = None,
    tnr_dir: Optional[str] = None,
    ingest_glossary: Optional[Sequence[Tuple[re.Pattern, str]]] = None,
    user_font_path: Optional[str] = None,
):
    ensure_fpdf_available()

    title_keywords_lower = [k.lower() for k in title_keywords]

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(left=15, top=15, right=15)

    # Default font family
    family_name = "Helvetica"

    # If a user-provided TTF is given, use it for all styles
    if user_font_path and os.path.exists(user_font_path):
        try:
            pdf.add_font("UserFont", style="", fname=user_font_path)
            # Register same file for style variants to avoid missing font errors
            pdf.add_font("UserFont", style="B", fname=user_font_path)
            pdf.add_font("UserFont", style="I", fname=user_font_path)
            pdf.add_font("UserFont", style="BI", fname=user_font_path)
            family_name = "UserFont"
        except Exception:
            family_name = "Helvetica"

    # Prepare dash markup with a tiny fallback font only for the em dash
    def _find_default_dash_font_path() -> Optional[str]:
        mac_dir = "/System/Library/Fonts/Supplemental"
        for name in ("Arial Unicode.ttf", "Arial.ttf", "Times New Roman.ttf"):
            p = os.path.join(mac_dir, name)
            if os.path.exists(p):
                return p
        return None

    if family_name == "UserFont":
        dash_markup = '<font face="UserFont">—</font>'
    else:
        dash_markup = "--"
        dash_file = None
        if dash_font_path and os.path.exists(dash_font_path):
            dash_file = dash_font_path
        else:
            dash_file = _find_default_dash_font_path()
        if dash_file:
            try:
                pdf.add_font("DashUni", "", dash_file, uni=True)
                dash_markup = '<font face="DashUni">—</font>'
            except Exception:
                dash_markup = "--"

    # Pre-scan chapter titles and prepare links
    chapter_entries: List[Tuple[int, str, int]] = []  # (number, title, link_id)
    preloaded_lines: dict[int, List[str]] = {}

    for chap_num, path in chunk_items:
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            with open(path, "r", encoding="latin-1", errors="ignore") as f:
                lines = f.readlines()
        preloaded_lines[chap_num] = lines

        # Detect first title line
        first_title = None
        for raw in lines:
            s = normalize_text(raw.rstrip("\n")).strip()
            if not s:
                continue
            if TITLE_REGEX.search(s) or any(s.lower().startswith(k) for k in title_keywords_lower):
                first_title = s
                break
        if first_title is None:
            first_title = f"Capítulo {chap_num}"

        link_id = pdf.add_link()
        chapter_entries.append((chap_num, first_title, link_id))

    # Cover page
    if cover_path and os.path.exists(cover_path):
        pdf.add_page()
        # Fit image to page keeping aspect ratio and centered
        try:
            from PIL import Image  # optional, just for aspect ratio
            with Image.open(cover_path) as im:
                img_w, img_h = im.size
        except Exception:
            img_w, img_h = (1200, 1750)  # assume portrait
        page_w, page_h = pdf.w - pdf.l_margin - pdf.r_margin, pdf.h - pdf.t_margin - pdf.b_margin
        # Use full page without margins for cover
        page_w_full, page_h_full = pdf.w, pdf.h
        img_ratio = img_w / img_h
        page_ratio = page_w_full / page_h_full
        if img_ratio > page_ratio:
            # Image is wider -> fit width
            w = page_w_full
            h = w / img_ratio
            x = 0
            y = (page_h_full - h) / 2
        else:
            # Fit height
            h = page_h_full
            w = h * img_ratio
            y = 0
            x = (page_w_full - w) / 2
        pdf.image(cover_path, x=x, y=y, w=w, h=h)

    # Content pages
    for chap_num, path in chunk_items:
        lines = preloaded_lines.get(chap_num, [])
        # Get the TOC tuple to access link_id and first_title
        entry = next((e for e in chapter_entries if e[0] == chap_num), None)
        link_id = entry[2] if entry else pdf.add_link()
        first_title_text = entry[1] if entry else None

        pdf.add_page()

        italic_open = False
        title_written = False
        for raw in lines:
            text = normalize_text(raw.rstrip("\n"))
            # Apply glossary replacements if provided
            if ingest_glossary:
                for pat, repl in ingest_glossary:
                    text = pat.sub(repl, text)
            stripped = text.strip()
            is_title = False
            # Detect by regex/keywords
            if stripped:
                if TITLE_REGEX.search(stripped) or any(
                    stripped.lower().startswith(k) for k in title_keywords_lower
                ):
                    # Only keep the first title for this chapter
                    if not title_written:
                        is_title = True
                        title_written = True
                    else:
                        # Skip duplicate titles (even if separated by blank lines)
                        if first_title_text and stripped == first_title_text:
                            continue
                        # Else, treat as normal paragraph

            if is_title:
                pdf.set_font(family_name, style="B", size=16)
                y = pdf.get_y()
                pdf.set_link(link_id, y=y, page=pdf.page_no())
                # Optional PDF bookmark if available in this fpdf2 version
                if hasattr(pdf, "bookmark"):
                    try:
                        pdf.bookmark(stripped, level=0)
                    except Exception:
                        pass
                pdf.set_text_color(0, 0, 0)
                pdf.multi_cell(0, 8, stripped)
                pdf.ln(2)
            else:
                if stripped == "":
                    # Blank line -> vertical space
                    pdf.ln(4)
                else:
                    # Texto normal con cursiva solo entre comillas
                    pdf.set_font(family_name, size=12)
                    html_line, italic_open = to_html_with_dialogue_italics(text, italic_open, dash_markup=dash_markup)
                    # Sangría al inicio de cada nueva línea
                    indent = "&nbsp;" * 4
                    html_line = indent + html_line
                    pdf.write_html(html_line + "<br>")

    # TOC page (clickable) at the end, once anchors exist
    if add_toc and chapter_entries:
        pdf.add_page()
        pdf.set_font(family_name, style="B", size=18)
        pdf.cell(0, 10, "Índice", ln=1)
        pdf.ln(2)
        pdf.set_font(family_name, size=12)
        for chap_num, first_title, link_id in chapter_entries:
            label = first_title if first_title else f"Capítulo {chap_num}"
            pdf.cell(0, 7, label, ln=1, link=link_id)

        # Space between chapters
        pdf.ln(6)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    pdf.output(out_path)


def main():
    parser = argparse.ArgumentParser(description="Genera PDFs por bloques de capítulos desde TXT.")
    parser.add_argument(
        "--input",
        default="traduccion",
        help="Carpeta con los .txt (por defecto: traduccion)",
    )
    parser.add_argument(
        "--output",
        default=os.path.join("output", "pdfs"),
        help="Carpeta de salida para los PDFs (por defecto: output/pdfs)",
    )
    parser.add_argument(
        "--block-size",
        type=int,
        default=50,
        help="Cantidad de capítulos por PDF (por defecto: 50)",
    )
    parser.add_argument(
        "--title-keywords",
        default="Capítulo,Capitulo",
        help="Palabras clave para detectar títulos, separadas por coma (por defecto: Capítulo,Capitulo)",
    )
    parser.add_argument(
        "--cover",
        default="",
        help="Ruta de imagen para portada (opcional). Ej: config/cover.jpg",
    )
    parser.add_argument(
        "--no-toc",
        action="store_true",
        help="Desactiva la creación de índice clickeable",
    )
    parser.add_argument(
        "--ttf",
        default="",
        help="Ruta a una fuente TTF Unicode para dibujar el guion largo (—). Ej: /System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    )
    parser.add_argument(
        "--tnr-dir",
        default="",
        help="Directorio que contiene Times New Roman TTF (Regular, Bold, Italic, Bold Italic)",
    )
    parser.add_argument(
        "--ingest-glossary",
        default=os.path.join("config", "ingest_glossary.json"),
        help="Ruta a un JSON con 'replace': { original: traducción } para aplicar durante ingestión",
    )
    parser.add_argument(
        "--font",
        default="",
        help="Ruta a un TTF para usar como fuente principal en todo el documento",
    )
    parser.add_argument(
        "--basename",
        default="novela",
        help="Prefijo/base del nombre del PDF (por defecto: novela)",
    )
    parser.add_argument(
        "--range",
        default="",
        help="Rango de capítulos a incluir, ej. 51-100. Filtra antes de agrupar",
    )

    args = parser.parse_args()

    input_dir = args.input
    output_dir = args.output
    block_size = max(1, args.block_size)
    title_keywords = [s.strip() for s in args.title_keywords.split(",") if s.strip()]

    if not os.path.isdir(input_dir):
        raise SystemExit(f"No existe la carpeta de entrada: {input_dir}")

    chapters = list_chapter_files(input_dir)
    if not chapters:
        raise SystemExit("No se encontraron archivos .txt con número de capítulo detectable.")

    # Filtro por rango si se especifica
    if args.range:
        try:
            start_s, end_s = args.range.split("-", 1)
            r_start, r_end = int(start_s), int(end_s)
            if r_start > r_end:
                r_start, r_end = r_end, r_start
            chapters = [c for c in chapters if r_start <= c[0] <= r_end]
        except Exception:
            raise SystemExit("Formato de --range inválido. Usa por ejemplo: 51-100")

    # Cargar glosario de ingestión si existe
    ingest_patterns: Optional[Sequence[Tuple[re.Pattern, str]]] = None
    if args.ingest_glossary and os.path.exists(args.ingest_glossary):
        import json
        with open(args.ingest_glossary, "r", encoding="utf-8") as f:
            data = json.load(f)
        rep = data.get("replace") or {}
        if isinstance(rep, dict) and rep:
            # Ordenar por longitud descendente para evitar sustituciones parciales
            items = sorted(rep.items(), key=lambda kv: len(kv[0]), reverse=True)
            pats: list[Tuple[re.Pattern, str]] = []
            for src, dst in items:
                # Bordes de palabra para extremos; permite frases con espacios
                # Usamos (?<!\w) y (?!\w) para evitar incrustaciones en palabras
                pattern = re.compile(rf"(?<!\w){re.escape(src)}(?!\w)")
                pats.append((pattern, dst))
            ingest_patterns = pats

    groups = chunk(chapters, block_size)
    for group in groups:
        first_ch, last_ch = group[0][0], group[-1][0]
        out_name = f"{args.basename}_{first_ch:04d}-{last_ch:04d}.pdf"
        out_path = os.path.join(output_dir, out_name)
        print(f"Generando {out_path} con capítulos {first_ch}-{last_ch}...")
        cover_path = args.cover if args.cover else None
        add_toc = not args.no_toc
        write_chunk_to_pdf(
            group,
            out_path,
            title_keywords,
            cover_path=cover_path,
            add_toc=add_toc,
            dash_font_path=args.ttf if args.ttf else None,
            tnr_dir=args.tnr_dir if args.tnr_dir else None,
            ingest_glossary=ingest_patterns,
            user_font_path=args.font if args.font else None,
        )

    print("Listo.")


if __name__ == "__main__":
    main()
