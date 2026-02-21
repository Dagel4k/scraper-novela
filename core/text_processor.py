import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.domain import ChapterContent, Glossary, IngestGlossary, slugify

# ── ES→EN segment mapping for fuzzy placeholder restoration ────
ES_TO_EN_SEGMENT: Dict[str, str] = {
    # titles & roles
    "VIEJO": "OLD",
    "ANCIANO": "ELDER",
    "ABUELO": "GRANDPA",
    "TIO": "UNCLE",
    "TIO_MARTIAL": "MARTIAL_UNCLE",
    "MAESTRO": "TEACHER",
    "DIRECTOR": "DIRECTOR",
    "ADMINISTRADOR": "ADMINISTRATOR",
    "GERENTE": "MANAGER",
    "JEFE": "HEAD",
    "DECANO": "DEAN",
    "PRINCIPAL": "PRINCIPAL",
    "HERMANO": "BROTHER",
    "HERMANA": "SISTER",
    "CONDE": "MARQUIS",
    "MARQUES": "MARQUIS",
    "REY": "KING",
    "REINA": "QUEEN",
    "GRAN": "GREAT",
    "GRANDES": "GREAT",
    "FAMILIA": "FAMILY",
    "CLAN": "CLAN",
    # levels & realms
    "REINO": "REALM",
    "REINOS": "REALMS",
    "INVENCIBLE": "INVINCIBLE",
    "MONTANASEAS": "MOUNTAINSEAS",
    "MONTANAMAR": "MOUNTAINSEA",
    "CIELOS": "HEAVENS",
    "SOLLUNA": "SUNMOON",
    "ROMPENUBE": "CLOUDBREACH",
    "BUSQUEDA_CONOCIMIENTO": "KNOWLEDGE_SEEKING",
    # factions & academies
    "FACCION": "FACTION",
    "CARACTER": "CHARACTER",
    "CARACTERES": "CHARACTERS",
    "MULTIPLE": "MULTIPLE",
    "ACADEMIA": "ACADEMY",
    "INVESTIGACION": "RESEARCH",
    "CULTURAL": "CULTURAL",
    "GUERRA": "WAR",
    # weapons/techniques
    "ESPADA": "SWORD",
    "MATA_DRAGONES": "DRAGON_SLAYING",
    "ARTE": "ART",
    "TECNOLOGIA": "TECHNIQUE",
    "TECNOLOGIA_CULTURAL": "CULTURAL_WEAPON",
    "GRADO_CELESTIAL": "HEAVEN_GRADE",
    "GRADO_TERRenal": "EARTH_GRADE",
    "GRADO_PROFUNDO": "PROFOUND_GRADE",
    "GRADO_AMARILLO": "YELLOW_GRADE",
    # places
    "REINO_HUMANO": "HUMAN_REALM",
    "GRAN_XIA": "GREAT_XIA",
    "GRAN_ZHOU": "GREAT_ZHOU",
}

_JACCARD_THRESHOLD = 0.66


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _es_to_en_slug(slug: str) -> str:
    parts = [p for p in slug.split("_") if p]
    mapped = []
    for p in parts:
        key = p.upper()
        key = (
            key.replace("Á", "A").replace("É", "E")
            .replace("Í", "I").replace("Ó", "O")
            .replace("Ú", "U").replace("Ñ", "N")
        )
        mapped.append(ES_TO_EN_SEGMENT.get(key, key))
    return "_".join(mapped)


def _lookup_placeholder(
    name: str,
    restore_tokens: Dict[str, str],
    key_sets: List[Tuple[str, str, set]],
) -> Optional[str]:
    """Resolve a variant/malformed placeholder name to its original term.

    Tries three strategies in order:
    1. Exact key match on slugified variants.
    2. Key prefix match.
    3. Fuzzy Jaccard similarity on segment sets.

    Returns None if no match is found above the threshold.
    """
    raw = slugify(_strip_accents(name))
    approx = _es_to_en_slug(raw)
    base = re.sub(r"_\d+\b", "", raw)

    candidates = [f"<PROTECT_{raw}>"]
    if approx != raw:
        candidates.append(f"<PROTECT_{approx}>")
    if base and base != raw:
        candidates.append(f"<PROTECT_{base}>")

    # Exact match
    for key in candidates:
        if key in restore_tokens:
            return restore_tokens[key]

    # Prefix match
    for key in restore_tokens:
        for pref in candidates:
            if key.startswith(pref.rstrip(">")):
                return restore_tokens[key]

    # Fuzzy Jaccard
    for candidate in filter(None, [raw, approx, base]):
        slug = re.sub(r"_\d+\b", "", candidate)
        segs = {s for s in slug.split("_") if s}
        best: Optional[Tuple[str, str]] = None
        best_score = 0.0
        for k, term, ksegs in key_sets:
            inter = len(segs & ksegs)
            denom = max(len(segs), len(ksegs)) or 1
            score = inter / denom
            if score > best_score:
                best_score = score
                best = (k, term)
        if best and best_score >= _JACCARD_THRESHOLD:
            return best[1]

    return None


class TextProcessor:
    def __init__(
        self,
        glossary: Glossary,
        ingest_glossary: Optional[IngestGlossary] = None,
    ) -> None:
        self.glossary = glossary
        self.ingest_glossary = ingest_glossary or IngestGlossary()

    # ── protect ─────────────────────────────────────────────────
    def protect_text(self, text: str) -> str:
        items = sorted(
            self.glossary.protect_tokens.items(),
            key=lambda kv: len(kv[0]),
            reverse=True,
        )
        for term, ph in items:
            if not term:
                continue
            pattern = r"\b" + re.escape(term) + r"\b"
            text = re.sub(pattern, ph, text)
        return text

    # ── restore (robust, from translate_hybrid.py) ──────────────
    def restore_text(self, text: str) -> str:
        glossary = self.glossary

        # Pre-compute segment sets for fuzzy matching
        key_sets: List[Tuple[str, str, set]] = []
        for k, term in glossary.restore_tokens.items():
            m = re.match(r"^<PROTECT_([A-Z0-9_]+?)(?:_\d+)?>$", k)
            if not m:
                continue
            base = m.group(1)
            segs = {s for s in base.split("_") if s}
            key_sets.append((k, term, segs))

        # 1) Exact replacement
        items = sorted(
            glossary.restore_tokens.items(),
            key=lambda kv: len(kv[0]),
            reverse=True,
        )
        for ph, term in items:
            text = text.replace(ph, term)

        # 2) Angle-bracket variants (spaces, case, PROTEGER, accents)
        angle_pat = re.compile(
            r"<\s*(?:PROTECT|PROTEGER)\s*[_:\-\s]*?(?P<name>[^>]+?)\s*>",
            re.IGNORECASE,
        )

        def _repl_angle(m: re.Match) -> str:
            result = _lookup_placeholder(
                m.group("name") or "", glossary.restore_tokens, key_sets
            )
            return result if result is not None else m.group(0)

        text = angle_pat.sub(_repl_angle, text)

        # 3) Bare patterns (no angle brackets): PROTECT_XIA_YUWEN_1
        bare_pat = re.compile(
            r"\b(?:PROTECT|PROTEGER)\s*[_:\-\s]*?(?P<name>[^>\n]*?_\d+)\b",
            re.IGNORECASE,
        )

        def _repl_bare(m: re.Match) -> str:
            result = _lookup_placeholder(
                m.group("name") or "", glossary.restore_tokens, key_sets
            )
            return result if result is not None else m.group(0)

        text = bare_pat.sub(_repl_bare, text)

        return text

    # ── postprocess ─────────────────────────────────────────────
    def apply_postprocess(self, text: str) -> str:
        for pat, repl in self.glossary.post_replace.items():
            text = re.sub(pat, repl, text)
        return text

    # ── ingest replacements ─────────────────────────────────────
    def apply_ingest_replacements(self, text: str) -> str:
        items = sorted(
            self.ingest_glossary.replace.items(),
            key=lambda kv: len(kv[0]),
            reverse=True,
        )
        for original, replacement in items:
            if original:
                pattern = r"\b" + re.escape(original) + r"\b"
                text = re.sub(pattern, replacement, text)
        return text

    # ── read chapter ────────────────────────────────────────────
    def read_chapter(self, path: Path, number: int = 0) -> ChapterContent:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not lines:
            return ChapterContent(number=number, title="", paragraphs=[])
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
        return ChapterContent(
            number=number,
            title=title,
            paragraphs=paragraphs,
            source_path=str(path),
        )

    # ── write chapter ───────────────────────────────────────────
    def write_chapter(self, path: Path, title: str, paragraphs: List[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n\n".join(paragraphs)
        path.write_text(title.strip() + "\n\n" + body + "\n", encoding="utf-8")

    # ── chunk paragraphs ────────────────────────────────────────
    @staticmethod
    def chunk_paragraphs(
        paragraphs: List[str], max_chars: int = 7000
    ) -> List[List[str]]:
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

    # ── full pipeline helpers ───────────────────────────────────
    def prepare_text(self, text: str) -> str:
        text = self.apply_ingest_replacements(text)
        text = self.protect_text(text)
        return text

    def finalize_text(self, text: str) -> str:
        text = self.restore_text(text)
        text = self.apply_postprocess(text)
        return text
