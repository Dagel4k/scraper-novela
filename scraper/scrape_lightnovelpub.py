import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List

try:
    import cloudscraper  # type: ignore
except Exception:  # pragma: no cover
    cloudscraper = None

import requests
from bs4 import BeautifulSoup, NavigableString, Tag


BASE_URL = "https://lightnovelpub.org/novel/tribulation-of-myriad-races"
CHAPTER_URL = BASE_URL + "/chapter/{n}/"


UI_STOP_KEYWORDS = {
    "comment", "comments", "disqus", "reply", "respond",
    "share", "btn", "button", "nav", "next", "prev",
    "menu", "footer", "header", "ads", "ad", "advert",
    "rating", "review", "donate", "bookmark", "widget",
}

CONTENT_SELECTORS = [
    "#chapter-content", ".chapter-content", "#chr-content",
    "#chapter-container", ".reading-content", "article.chapter",
]


def build_session(user_agent: Optional[str] = None) -> requests.Session:
    """Build an HTTP session, preferring cloudscraper if available."""
    sess: requests.Session
    if cloudscraper is not None:
        sess = cloudscraper.create_scraper()
    else:
        sess = requests.Session()

    sess.headers.update({
        "User-Agent": user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    })
    return sess


def get_soup(html: str) -> BeautifulSoup:
    # Prefer lxml if installed; fallback to built-in parser
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # pragma: no cover
        return BeautifulSoup(html, "html.parser")


def request_with_retries(sess: requests.Session, url: str, *, retries: int = 3, timeout: int = 20, backoff: float = 1.5) -> requests.Response:
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = sess.get(url, timeout=timeout)
            # Cloudflare interstitial detection (best-effort)
            if resp.status_code in (403, 429):
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            text_low = resp.text.lower()
            if (
                "just a moment" in text_low and "cloudflare" in text_low
            ) or "cf-browser-verification" in text_low:
                raise requests.HTTPError("Cloudflare interstitial detected")
            return resp
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                sleep_s = backoff ** (attempt - 1)
                time.sleep(sleep_s)
            else:
                break
    assert last_exc is not None
    raise last_exc


def extract_total_chapters(html: str) -> Optional[int]:
    soup = get_soup(html)
    # Strategy 1: search text for "<n> Chapters"
    text = soup.get_text(" ", strip=True)
    m = re.search(r"(\d{1,5})\s+Chapters\b", text, flags=re.I)
    if m:
        return int(m.group(1))

    # Strategy 2: look for labels containing Chapters:
    for tag in soup.find_all(text=re.compile(r"Chapters", re.I)):
        # Try to find a nearby number
        snippet = tag.parent.get_text(" ", strip=True) if isinstance(tag, NavigableString) else str(tag)
        m2 = re.search(r"(\d{1,5})", snippet)
        if m2:
            return int(m2.group(1))
    return None


def find_h1_chapter(soup: BeautifulSoup) -> Optional[Tag]:
    cands = []
    for tag_name in ("h1", "h2"):
        for h in soup.find_all(tag_name):
            t = h.get_text(" ", strip=True)
            if t and "chapter" in t.lower():
                cands.append(h)
    if cands:
        return cands[0]
    # Fallback: any h1/h2
    return soup.find(["h1", "h2"])


def parse_chapter_title(title_raw: str, expected_num: Optional[int] = None) -> Tuple[str, Optional[int], Optional[str]]:
    """Return (full_title, chapter_num, short_title)."""
    title = title_raw.strip()
    num = None
    short = None
    m = re.search(r"Chapter\s+(\d+)(?::\s*(.*))?", title, flags=re.I)
    if m:
        num = int(m.group(1))
        if m.group(2):
            short = m.group(2).strip()
    if expected_num is not None and num is None:
        num = expected_num
    return title, num, short


def is_ui_block(tag: Tag) -> bool:
    if not isinstance(tag, Tag):
        return False
    parts: List[str] = []
    if tag.get("id"):
        parts.append(str(tag.get("id")))
    cls = tag.get("class") or []
    parts.extend(cls)
    joined = " ".join(parts).lower()
    if any(k in joined for k in UI_STOP_KEYWORDS):
        return True
    # Generic nav blocks
    if tag.name in {"nav", "footer", "aside"}:
        return True
    return False


def normalize_paragraph(text: str) -> str:
    # Collapse spaces while preserving meaningful line breaks
    t = re.sub(r"\s+", " ", text).strip()
    return t


def extract_content_from_container(container: Tag) -> List[str]:
    # Prefer direct <p> children; if none, collect all <p> descendants
    paragraphs: List[str] = []
    p_tags = container.find_all("p")
    if not p_tags:
        # Sometimes the text is split by <br/>
        text = container.get_text("\n", strip=True)
        for chunk in [c.strip() for c in text.split("\n")]:
            if len(chunk) >= 2:
                paragraphs.append(normalize_paragraph(chunk))
        return paragraphs

    for p in p_tags:
        t = p.get_text(" ", strip=True)
        t = normalize_paragraph(t)
        if t:
            paragraphs.append(t)
    return paragraphs


def extract_chapter_body(soup: BeautifulSoup, h1: Optional[Tag]) -> List[str]:
    # Try candidate selectors first
    for sel in CONTENT_SELECTORS:
        el = soup.select_one(sel)
        if el:
            paras = extract_content_from_container(el)
            if len(" ".join(paras)) > 200:  # heuristic length
                return paras

    # Fallback: walk siblings after h1
    if h1 is None:
        # Last resort: try the largest text container
        candidates = sorted(
            soup.find_all(["article", "section", "div"]),
            key=lambda t: len(t.get_text(" ", strip=True)),
            reverse=True,
        )
        for c in candidates[:5]:
            if is_ui_block(c):
                continue
            paras = extract_content_from_container(c)
            if len(" ".join(paras)) > 200:
                return paras
        return []

    parts: List[str] = []
    # Traverse next siblings, collecting paragraphs until a UI block
    node = h1.next_sibling
    while node is not None:
        if isinstance(node, NavigableString):
            # Skip pure whitespace
            if node.strip():
                parts.append(normalize_paragraph(str(node)))
        elif isinstance(node, Tag):
            if is_ui_block(node):
                break
            # If this tag contains many <p>, consider it as a container
            ps = node.find_all("p")
            if ps:
                for p in ps:
                    t = normalize_paragraph(p.get_text(" ", strip=True))
                    if t:
                        parts.append(t)
            else:
                # Use text with line breaks for br/inline splits
                t = node.get_text("\n", strip=True)
                for chunk in [c.strip() for c in t.split("\n")]:
                    if len(chunk) >= 2:
                        parts.append(normalize_paragraph(chunk))
        node = node.next_sibling

    # Coalesce paragraphs, filter very short UI leftovers
    paragraphs = [p for p in parts if len(p) > 1]
    return paragraphs


def zero_pad(n: int, width: int = 4) -> str:
    return str(n).zfill(width)


def save_chapter(output_dir: Path, number: int, title: str, paragraphs: List[str], url: str) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{zero_pad(number)}_en.txt"
    path = output_dir / fname
    body = "\n\n".join(paragraphs)
    with path.open("w", encoding="utf-8") as f:
        f.write(title.strip() + "\n\n" + body + "\n")

    # Append to index.jsonl
    idx_path = output_dir / "index.jsonl"
    record = {
        "number": number,
        "title": title.strip(),
        "url": url,
        "file": fname,
        "length": len(body),
        "retrieved_at": datetime.utcnow().isoformat() + "Z",
    }
    with idx_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return fname


def scrape_total_chapters(sess: requests.Session) -> Optional[int]:
    resp = request_with_retries(sess, BASE_URL)
    if resp.status_code != 200:
        return None
    return extract_total_chapters(resp.text)


def scrape_chapter(sess: requests.Session, n: int) -> Tuple[str, List[str]]:
    url = CHAPTER_URL.format(n=n)
    resp = request_with_retries(sess, url)
    resp.raise_for_status()
    soup = get_soup(resp.text)
    h1 = find_h1_chapter(soup)
    if h1 is None:
        title = f"Chapter {n}"
    else:
        title_raw = h1.get_text(" ", strip=True)
        title, _, _ = parse_chapter_title(title_raw, expected_num=n)
    paragraphs = extract_chapter_body(soup, h1)
    return title, paragraphs


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Scraper de LightNovelPub para 'Tribulation of Myriad Races'")
    parser.add_argument("--output-dir", default="output/tribulation-of-myriad-races", help="Directorio de salida")
    parser.add_argument("--start", type=int, default=1, help="Capítulo inicial (incluido)")
    parser.add_argument("--end", type=int, default=0, help="Capítulo final (incluido). 0 = usar descubrimiento")
    parser.add_argument("--discover-total", action="store_true", help="Descubrir total de capítulos desde la página base")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay entre peticiones (segundos)")
    parser.add_argument("--retries", type=int, default=3, help="Número de reintentos por petición")
    parser.add_argument("--timeout", type=int, default=25, help="Timeout por petición (s)")
    parser.add_argument("--user-agent", default=None, help="User-Agent personalizado")
    parser.add_argument("--resume", action="store_true", help="Omitir capítulos ya guardados")
    parser.add_argument("--min-length", type=int, default=200, help="Longitud mínima aceptable del contenido (caracteres)")

    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sess = build_session(args.user_agent)

    total = None
    if args.discover_total or args.end == 0:
        try:
            total = scrape_total_chapters(sess)
        except Exception as e:
            print(f"[warn] No se pudo descubrir el total: {e}", file=sys.stderr)
        if total is None and args.end == 0:
            print("[error] No se pudo determinar el total de capítulos y no se especificó --end.", file=sys.stderr)
            return 2

    start = args.start
    end = args.end if args.end > 0 else int(total or 0)
    if end < start:
        print(f"[error] Rango inválido: start={start} end={end}", file=sys.stderr)
        return 2

    print(f"[info] Descargando capítulos {start}..{end}")
    print(f"[info] Salida en: {output_dir}")

    # Guardar meta de novela (opcional)
    if total is not None:
        meta_path = output_dir / "novel_meta.json"
        meta = {
            "title": "Tribulation of Myriad Races",
            "author": "Eagle Eats Chicken",
            "total_chapters": total,
            "base_url": BASE_URL,
        }
        with meta_path.open("w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    for n in range(start, end + 1):
        url = CHAPTER_URL.format(n=n)
        fname = f"{zero_pad(n)}_en.txt"
        dest = output_dir / fname
        if args.resume and dest.exists():
            print(f"[skip] {n} ya existe ({fname})")
            continue

        try:
            title, paragraphs = scrape_chapter(sess, n)
            body_len = len("\n\n".join(paragraphs))
            if body_len < args.min_length:
                print(f"[warn] Capítulo {n}: contenido demasiado corto ({body_len} chars). Guardando igualmente.")
            saved_name = save_chapter(output_dir, n, title, paragraphs, url)
            print(f"[ok] {n}: {saved_name} ({body_len} chars)")
        except requests.HTTPError as he:
            print(f"[http] {n}: {he}", file=sys.stderr)
        except Exception as e:
            print(f"[err] {n}: {e}", file=sys.stderr)

        time.sleep(max(0.0, args.delay))

    print("[done] Proceso completado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

