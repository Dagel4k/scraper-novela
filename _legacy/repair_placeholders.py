import argparse
from pathlib import Path
from typing import Iterable

import sys, types

# Ensure project root is on sys.path so 'scraper' package is importable
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Allow running without optional deps used elsewhere
sys.modules.setdefault('requests', types.SimpleNamespace())

from scraper.translate_hybrid import Glossary, restore_text


def iter_txt_files(directory: Path) -> Iterable[Path]:
    for p in sorted(directory.glob('*.txt')):
        if p.is_file():
            yield p


def main() -> int:
    ap = argparse.ArgumentParser(description="Repara placeholders <PROTECT_...> o variantes 'PROTEGER_' en archivos ya traducidos.")
    ap.add_argument('--input', dest='input_dir', type=Path, required=True,
                    help='Directorio con archivos traducidos (p.ej., traduccion)')
    ap.add_argument('--glossary', dest='glossary', type=Path, default=Path('config/translation_glossary.json'),
                    help='Ruta al glossary JSON (default: config/translation_glossary.json)')
    ap.add_argument('--dry-run', action='store_true', help='No escribe cambios, solo reporta')
    args = ap.parse_args()

    if not args.input_dir.exists():
        print(f"[err] No existe el directorio: {args.input_dir}", file=sys.stderr)
        return 1

    g = Glossary.load(args.glossary)

    changed = 0
    scanned = 0
    for p in iter_txt_files(args.input_dir):
        scanned += 1
        original = p.read_text(encoding='utf-8')
        fixed = restore_text(original, g)
        if fixed != original:
            changed += 1
            if args.dry_run:
                print(f"[would-fix] {p.name}")
            else:
                p.write_text(fixed, encoding='utf-8')
                print(f"[fixed] {p.name}")

    print(f"[done] Escaneados: {scanned} | Modificados: {changed}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
