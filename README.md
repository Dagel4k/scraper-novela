# novela-scraper-translator

[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![LLM](https://img.shields.io/badge/LLM-Gemini%20%7C%20OpenAI%20%7C%20Ollama-orange)](.env.example)

A complete pipeline to **scrape**, **translate** (EN→ES and CN→ES), and **export** web novels as EPUB/PDF, with a built-in web reader for QA.

Built for *Tribulation of Myriad Races* but designed to be adapted to any LightNovelPub title.

---

## Features

- **Scraper** — Downloads chapters from LightNovelPub with retry logic, configurable delays, and optional Cloudflare bypass
- **Multi-LLM translation** — Pluggable adapters for Google Gemini, OpenAI, and Ollama (local)
- **Hybrid pipeline** — Stage 1: fast local draft with Ollama → Stage 2: cloud refinement with GPT
- **Chinese→Spanish direct** — Dedicated CN→ES pipeline with English chapter alignment for name consistency
- **Glossary system** — Protects names and terms across chapters using placeholder tokens; auto-extracts new terms per chapter
- **Post-processing / polishing** — Regex cleanup of leaked CJK characters, repetitive phrases, and a second LLM pass for natural Spanish
- **EPUB & PDF export** — Block-based export with cover art, custom fonts, and configurable chapter grouping
- **Web reader** — Vite/Vanilla JS app for reading and QA-ing translated chapters in the browser

---

## Project Structure

```
├── main.py                    # Unified CLI (scrape, translate, export)
├── scraper/                   # LightNovelPub scraper
├── core/                      # Domain models and text processing
├── adapters/                  # LLM adapters (Gemini, OpenAI)
├── interfaces/                # Abstract translator interface and prompt builder
├── utils/                     # Logger and file manager
├── scripts/                   # Specialized pipelines
│   ├── translate_cn.py        # Chinese→Spanish pipeline
│   ├── aligner.py             # Align CN chapters to EN for name extraction
│   ├── glossary_extractor.py  # Auto-generate glossary from chapters
│   ├── polish.py              # Post-processing and LLM polishing
│   ├── generate_epubs.py      # EPUB export
│   └── generate_pdfs.py       # PDF export
├── config/
│   ├── settings.yaml          # All settings (scraper, translation, adapters, prompts)
│   ├── translation_glossary.json  # Term and name glossary
│   └── ingest_glossary.json   # Post-processing replacements
├── data/
│   └── alignment_map.json     # CN↔EN chapter alignment map
├── docs/                      # Architecture and pipeline documentation
└── reader-app/                # Vite web reader for QA
```

---

## Setup

**Requirements:** Python 3.9+

```bash
git clone https://github.com/Dagel4k/scraper-novela.git
cd scraper-novela
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Copy the environment template and add your API keys:

```bash
cp .env.example .env
# Edit .env with your GEMINI_API_KEY and/or OPENAI_API_KEY
```

---

## Usage

### 1 — Scrape chapters

```bash
# Auto-discover total and download all chapters
python main.py scrape --output-dir output/tribulation

# Resume an interrupted download
python main.py scrape --output-dir output/tribulation --resume

# Fixed range
python main.py scrape --start 1 --end 100 --output-dir output/tribulation
```

### 2 — Translate (EN → ES)

```bash
# Translate with Gemini (default)
python main.py translate --input-dir output/tribulation --output-dir traduccion

# Resume from where you left off
python main.py translate --input-dir output/tribulation --output-dir traduccion --resume

# Use OpenAI instead
python main.py translate --adapter openai --input-dir output/tribulation --output-dir traduccion

# Specific range
python main.py translate --start 1 --end 50 --input-dir output/tribulation --output-dir traduccion
```

### 3 — Translate (CN → ES)

Direct Chinese-to-Spanish pipeline with automatic name extraction from the English version:

```bash
# Translate chapters 705–720 from Chinese raws
python scripts/translate_cn.py --start 705 --end 720

# Without LLM polishing (faster, regex cleanup only)
python scripts/translate_cn.py --start 705 --end 720 --no-polish

# Re-polish existing translations without re-translating
python scripts/translate_cn.py --start 705 --end 720 --polish-only
```

### 4 — Export EPUB / PDF

```bash
# EPUB — chapters 301–350 in one file
python scripts/generate_epubs.py \
  --input traduccion --output output/epub \
  --range 301-350 --basename novela --cover config/cover.jpg

# PDF — auto-split into blocks of 50 chapters
python scripts/generate_pdfs.py \
  --input traduccion --output output/pdfs \
  --block-size 50 --basename novela --cover config/cover.jpg
```

### 5 — Web reader

```bash
cd reader-app
npm install
npm run sync   # sync translated chapters from ../traduccion_cn
npm run dev    # open at http://localhost:5173
```

---

## Configuration

Everything is driven by `config/settings.yaml`. Key sections:

| Section | What it controls |
|---|---|
| `novel` | Source URL and chapter URL template |
| `scraper` | Delays, retries, selector heuristics |
| `translation` | Chunk size, concurrency, temperature |
| `adapter` | Active LLM and model names |
| `prompts` | System and user prompt templates |
| `output` | Default directories, PDF/EPUB formatting |

The glossary at `config/translation_glossary.json` controls:
- `never_translate` — names and places kept as-is
- `translations` — forced term mappings (e.g. `"Source Opening"` → `"Apertura de Origen"`)

---

## Hybrid Pipeline (Ollama + GPT)

For long translations, combine local speed with cloud quality:

```bash
# Requires: ollama serve + ollama pull qwen2.5:7b
python -m scraper.translate_hybrid \
  --input-dir output/tribulation \
  --start 1 --end 100 \
  --ollama-model qwen2.5:7b \
  --gpt-model gpt-4o-mini \
  --resume
```

Stage 1 (Ollama) generates fast drafts; Stage 2 (GPT) refines Spanish without re-translating from English.

---

## Architecture

```
Scraper → index.jsonl + NNNN_en.txt
           │
           ▼
Translation Pipeline
  ├─ TextProcessor   (glossary placeholders, chunking)
  ├─ PromptBuilder   (system + user prompts from settings.yaml)
  ├─ LLM Adapter     (Gemini / OpenAI / Ollama)
  └─ Output          NNNN_es.txt + index_es.jsonl
           │
           ▼
Export (EPUB / PDF) or Web Reader
```

See [`docs/arquitectura.md`](docs/arquitectura.md) for full pipeline details.

---

## Security

- Never commit your `.env` file — it's in `.gitignore`
- Copy `.env.example` → `.env` and fill in your keys
- The code reads API keys exclusively from environment variables

---

## License

[MIT](LICENSE)
