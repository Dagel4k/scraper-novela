import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def read_utf8(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    return raw.decode("utf-8")


def write_utf8(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> List[dict]:
    items: List[dict] = []
    if not path.exists():
        return items
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                pass
    return items


def load_env_file(path: Optional[Path]) -> None:
    if not path or not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                os.environ[k] = v
    except Exception:
        pass


def discover_chapter_files(
    directory: Path,
    pattern: str = "*_es.txt",
) -> List[Tuple[int, Path]]:
    by_num: Dict[int, Path] = {}
    for p in directory.glob("*.txt"):
        if not p.is_file():
            continue
        m = re.match(r"^(\d+)", p.name)
        if not m:
            continue
        num = int(m.group(1))
        is_draft = "draft" in p.stem
        prev = by_num.get(num)
        if prev is None:
            by_num[num] = p
        elif "draft" in prev.stem and not is_draft:
            by_num[num] = p
    return sorted(by_num.items(), key=lambda t: t[0])


_NORMALIZE_MAP = str.maketrans({
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u00ab": '"',
    "\u00bb": '"',
    "\u2013": "-",
    "\u2026": "...",
    "\u00a0": " ",
    "\ufeff": "",
})


def normalize_text(s: str) -> str:
    return s.translate(_NORMALIZE_MAP)
