import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


def slugify(s: str) -> str:
    s2 = re.sub(r"[^A-Za-z0-9]+", "_", s.strip())
    return re.sub(r"_+", "_", s2).strip("_").upper()[:40]


class Glossary(BaseModel):
    never_translate: List[str] = Field(default_factory=list)
    translations: Dict[str, str] = Field(default_factory=dict)
    protect_tokens: Dict[str, str] = Field(default_factory=dict)
    restore_tokens: Dict[str, str] = Field(default_factory=dict)
    post_replace: Dict[str, str] = Field(default_factory=dict)

    @staticmethod
    def load(path: Optional[Path]) -> "Glossary":
        if path is None or not path.exists():
            return Glossary()
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

    def to_json(self) -> dict:
        return {
            "glossary": {
                "never_translate": self.never_translate,
                "translations": self.translations,
                "preprocess_rules": {
                    "protect_tokens": self.protect_tokens,
                    "restore_tokens": self.restore_tokens,
                },
                "postprocess_rules": {
                    "replace": self.post_replace,
                },
            }
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(self.to_json(), f, ensure_ascii=False, indent=2)

    def ensure_placeholders(self) -> None:
        for term in self.never_translate:
            if term not in self.protect_tokens:
                i = 1
                ph = f"<PROTECT_{slugify(term)}_{i}>"
                while ph in self.restore_tokens:
                    i += 1
                    ph = f"<PROTECT_{slugify(term)}_{i}>"
                self.protect_tokens[term] = ph
                self.restore_tokens[ph] = term
                base_key = f"<PROTECT_{slugify(term)}>"
                if base_key not in self.restore_tokens:
                    self.restore_tokens[base_key] = term
        existing = list(self.restore_tokens.items())
        for ph, term in existing:
            m = re.match(r"^<PROTECT_([A-Z0-9_]+?)_\d+>$", ph)
            if m:
                base_key = f"<PROTECT_{m.group(1)}>"
                if base_key not in self.restore_tokens:
                    self.restore_tokens[base_key] = term

    def merge(self, new: "Glossary") -> None:
        for t in new.never_translate:
            if t and t not in self.never_translate:
                self.never_translate.append(t)
        for k, v in new.translations.items():
            if k and k not in self.translations:
                self.translations[k] = v
        self.ensure_placeholders()


class IngestGlossary(BaseModel):
    replace: Dict[str, str] = Field(default_factory=dict)

    @staticmethod
    def load(path: Optional[Path]) -> "IngestGlossary":
        if path is None or not path.exists():
            return IngestGlossary()
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return IngestGlossary(replace=dict(data.get("replace", {}) or {}))


class ChapterContent(BaseModel):
    number: int
    title: str
    paragraphs: List[str]
    source_path: Optional[str] = None


class TranslationResult(BaseModel):
    number: int
    title_en: str
    title_es: str
    paragraphs_es: List[str]
    model: str
    adapter_name: str


class TranslationRecord(BaseModel):
    number: int
    title_en: str
    title_es: str
    file_en: str
    file_es: str
    input_dir: str
    output_dir: str
    length_en: int
    length_es: int
    model: str
    adapter_name: str
    translated_at: str


class ScrapedChapter(BaseModel):
    number: int
    title: str
    url: str
    file: str
    length: int
    retrieved_at: str
