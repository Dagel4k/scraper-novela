import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import cloudscraper  # type: ignore
except Exception:
    cloudscraper = None

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from utils.file_manager import append_jsonl
from utils.logger import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


class LightNovelPubScraper:
    def __init__(self, settings: dict) -> None:
        novel = settings.get("novel", {})
        scraper_cfg = settings.get("scraper", {})

        self.base_url: str = novel.get(
            "base_url",
            "https://lightnovelpub.org/novel/tribulation-of-myriad-races",
        )
        tpl = novel.get("chapter_url_template", "{base_url}/chapter/{n}/")
        self.chapter_url_template: str = tpl.replace("{base_url}", self.base_url)

        self.delay: float = scraper_cfg.get("delay", 1.5)
        self.retries: int = scraper_cfg.get("retries", 3)
        self.timeout: int = scraper_cfg.get("timeout", 25)
        self.min_length: int = scraper_cfg.get("min_length", 200)

        self.content_selectors: List[str] = scraper_cfg.get(
            "content_selectors",
            [
                "#chapter-content", ".chapter-content", "#chr-content",
                "#chapter-container", ".reading-content", "article.chapter",
            ],
        )
        self.ui_stop_keywords: set = set(
            scraper_cfg.get(
                "ui_stop_keywords",
                [
                    "comment", "comments", "disqus", "reply", "respond",
                    "share", "btn", "button", "nav", "next", "prev",
                    "menu", "footer", "header", "ads", "ad", "advert",
                    "rating", "review", "donate", "bookmark", "widget",
                ],
            )
        )

        self._session: Optional[requests.Session] = None

    # ── Session ─────────────────────────────────────────────────
    def _build_session(self, user_agent: Optional[str] = None) -> requests.Session:
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

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = self._build_session()
        return self._session

    # ── HTML helpers ────────────────────────────────────────────
    @staticmethod
    def _get_soup(html: str) -> BeautifulSoup:
        try:
            return BeautifulSoup(html, "lxml")
        except Exception:
            return BeautifulSoup(html, "html.parser")

    def _request_with_retries(self, url: str) -> requests.Response:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout)
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
                if attempt < self.retries:
                    time.sleep(1.5 ** (attempt - 1))
        assert last_exc is not None
        raise last_exc

    # ── Chapter extraction ──────────────────────────────────────
    def extract_total_chapters(self) -> Optional[int]:
        resp = self._request_with_retries(self.base_url)
        if resp.status_code != 200:
            return None
        soup = self._get_soup(resp.text)
        text = soup.get_text(" ", strip=True)
        m = re.search(r"(\d{1,5})\s+Chapters\b", text, flags=re.I)
        if m:
            return int(m.group(1))
        for tag in soup.find_all(string=re.compile(r"Chapters", re.I)):
            snippet = tag.parent.get_text(" ", strip=True) if isinstance(tag, NavigableString) else str(tag)
            m2 = re.search(r"(\d{1,5})", snippet)
            if m2:
                return int(m2.group(1))
        return None

    def _parse_chapter_title(self, title_raw: str, expected_num: Optional[int] = None) -> Tuple[str, Optional[int]]:
        title = title_raw.strip()
        num = None
        m = re.search(r"Chapter\s+(\d+)", title, flags=re.I)
        if m:
            num = int(m.group(1))
        if expected_num is not None and num is None:
            num = expected_num
        return title, num

    def _is_ui_block(self, tag: Tag) -> bool:
        if not isinstance(tag, Tag):
            return False
        parts: List[str] = []
        if tag.get("id"):
            parts.append(str(tag.get("id")))
        cls = tag.get("class") or []
        parts.extend(cls)
        joined = " ".join(parts).lower()
        if any(k in joined for k in self.ui_stop_keywords):
            return True
        if tag.name in {"nav", "footer", "aside"}:
            return True
        return False

    @staticmethod
    def _normalize_paragraph(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _extract_content_from_container(self, container: Tag) -> List[str]:
        paragraphs: List[str] = []
        p_tags = container.find_all("p")
        if not p_tags:
            text = container.get_text("\n", strip=True)
            for chunk in [c.strip() for c in text.split("\n")]:
                if len(chunk) >= 2:
                    paragraphs.append(self._normalize_paragraph(chunk))
            return paragraphs
        for p in p_tags:
            t = p.get_text(" ", strip=True)
            t = self._normalize_paragraph(t)
            if t:
                paragraphs.append(t)
        return paragraphs

    def _extract_chapter_body(self, soup: BeautifulSoup, h1: Optional[Tag]) -> List[str]:
        for sel in self.content_selectors:
            el = soup.select_one(sel)
            if el:
                paras = self._extract_content_from_container(el)
                if len(" ".join(paras)) > self.min_length:
                    return paras

        if h1 is None:
            candidates = sorted(
                soup.find_all(["article", "section", "div"]),
                key=lambda t: len(t.get_text(" ", strip=True)),
                reverse=True,
            )
            for c in candidates[:5]:
                if self._is_ui_block(c):
                    continue
                paras = self._extract_content_from_container(c)
                if len(" ".join(paras)) > self.min_length:
                    return paras
            return []

        parts: List[str] = []
        node = h1.next_sibling
        while node is not None:
            if isinstance(node, NavigableString):
                if node.strip():
                    parts.append(self._normalize_paragraph(str(node)))
            elif isinstance(node, Tag):
                if self._is_ui_block(node):
                    break
                ps = node.find_all("p")
                if ps:
                    for p in ps:
                        t = self._normalize_paragraph(p.get_text(" ", strip=True))
                        if t:
                            parts.append(t)
                else:
                    t = node.get_text("\n", strip=True)
                    for chunk in [c.strip() for c in t.split("\n")]:
                        if len(chunk) >= 2:
                            parts.append(self._normalize_paragraph(chunk))
            node = node.next_sibling
        return [p for p in parts if len(p) > 1]

    @staticmethod
    def _find_h1_chapter(soup: BeautifulSoup) -> Optional[Tag]:
        cands = []
        for tag_name in ("h1", "h2"):
            for h in soup.find_all(tag_name):
                t = h.get_text(" ", strip=True)
                if t and "chapter" in t.lower():
                    cands.append(h)
        if cands:
            return cands[0]
        return soup.find(["h1", "h2"])

    # ── Public API ──────────────────────────────────────────────
    def scrape_chapter(self, n: int) -> Tuple[str, List[str]]:
        url = self.chapter_url_template.format(n=n)
        resp = self._request_with_retries(url)
        resp.raise_for_status()
        soup = self._get_soup(resp.text)
        h1 = self._find_h1_chapter(soup)
        if h1 is None:
            title = f"Chapter {n}"
        else:
            title_raw = h1.get_text(" ", strip=True)
            title, _ = self._parse_chapter_title(title_raw, expected_num=n)
        paragraphs = self._extract_chapter_body(soup, h1)
        return title, paragraphs

    def save_chapter(
        self, output_dir: Path, number: int, title: str, paragraphs: List[str], url: str
    ) -> str:
        output_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{str(number).zfill(4)}_en.txt"
        path = output_dir / fname
        body = "\n\n".join(paragraphs)
        path.write_text(title.strip() + "\n\n" + body + "\n", encoding="utf-8")

        record = {
            "number": number,
            "title": title.strip(),
            "url": url,
            "file": fname,
            "length": len(body),
            "retrieved_at": datetime.utcnow().isoformat() + "Z",
        }
        append_jsonl(output_dir / "index.jsonl", record)
        return fname

    def scrape_range(
        self,
        output_dir: Path,
        start: int,
        end: int,
        *,
        resume: bool = False,
    ) -> None:
        logger.info("Downloading chapters %d..%d → %s", start, end, output_dir)
        for n in range(start, end + 1):
            fname = f"{str(n).zfill(4)}_en.txt"
            dest = output_dir / fname
            if resume and dest.exists():
                logger.info("[skip] %d already exists", n)
                continue
            try:
                title, paragraphs = self.scrape_chapter(n)
                body_len = len("\n\n".join(paragraphs))
                if body_len < self.min_length:
                    logger.warning(
                        "Chapter %d: short content (%d chars)", n, body_len
                    )
                url = self.chapter_url_template.format(n=n)
                saved = self.save_chapter(output_dir, n, title, paragraphs, url)
                logger.info("[ok] %d: %s (%d chars)", n, saved, body_len)
            except requests.HTTPError as he:
                logger.error("[http] %d: %s", n, he)
            except Exception as e:
                logger.error("[err] %d: %s", n, e)
            time.sleep(max(0.0, self.delay))
        logger.info("Scraping complete.")
