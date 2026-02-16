Scraper de la novela "Tribulation of Myriad Races" (LightNovelPub)

Objetivo: Extraer todos los capítulos (inglés) desde LightNovelPub y guardarlos localmente, uno por archivo, con título, número y cuerpo limpio (sin UI ni comentarios). La traducción a español es una fase posterior y separada.

Ejecución rápida

- Requisitos: Python 3.9+, `requests`, `beautifulsoup4`, opcional `lxml` y opcional `cloudscraper`.
- Instala dependencias: `pip install -r requirements.txt`
- Ejecuta: `python -m scraper.scrape_lightnovelpub --output-dir output/tribulation --discover-total`

Características

- Navegación por patrón numérico de capítulos: `/chapter/<N>/`.
- Descubrimiento del total de capítulos desde la página base (patrón "<n> Chapters").
- Heurísticas robustas para extraer:
  - Título oficial del capítulo (del `h1` que contiene "Chapter").
  - Cuerpo narrativo (párrafos entre el título y cualquier bloque de UI/menú/ads/comentarios).
- Guardado por capítulo en archivos `NNNN_en.txt` y registro `index.jsonl` con metadatos.
- Reintentos con backoff, UA configurable, espera entre peticiones, reanudación por omisión (omite archivos existentes).

Traducción a español (EN -> ES)

Hay tres scripts disponibles:

1. **Pipeline híbrido (recomendado)**: `python -m scraper.translate_hybrid`
   - Stage 1: Ollama (local) traduce inglés → español (borrador)
   - Stage 2: GPT (OpenAI) refina el texto español (no traduce inglés)
   - Combina velocidad local con calidad GPT
   - Uso: `python -m scraper.translate_hybrid --input-dir output/tribulation --start 1 --end 100`

2. **Solo OpenAI**: `python -m scraper.translate_to_es`
   - Proveedor: OpenAI (requiere `openai` y `OPENAI_API_KEY`)
   - Traducción directa inglés → español

3. **Solo Ollama**: `python -m scraper.translate_to_es_ollama`
   - Proveedor: Ollama (local, sin API key)
   - Traducción directa inglés → español
- Configura tu API key como variable de entorno (no la guardes en archivos) para OpenAI:
  - macOS/Linux: `export OPENAI_API_KEY=sk-...`
  - Windows (PowerShell): `$Env:OPENAI_API_KEY="sk-..."`
- Uso típico:
  - Traducir todo el rango detectado: `python -m scraper.translate_to_es --input-dir output/tribulation`
  - Traducir un rango: `python -m scraper.translate_to_es --input-dir output/tribulation --start 1 --end 100`
  - Reanudar (omite ya traducidos): `python -m scraper.translate_to_es --input-dir output/tribulation --resume`
  - Ajustar modelo (OpenAI): `python -m scraper.translate_to_es --model gpt-4o-mini`
  - Ajustar tamaño de chunk: `--chunk-chars 7000` (divide por párrafos para respetar formato)
  - Activar glosario automático: `python -m scraper.translate_to_es --input-dir output/tribulation --auto-glossary --persist-glossary`
  - Cambiar carpeta de salida: `--output-dir traduccion` (por defecto usa la misma que `--input-dir`)

Pipeline híbrido (Ollama + GPT)

- **Recomendado para traducciones largas**: combina velocidad local (Ollama) con calidad GPT
- Requisitos:
  - Ollama instalado y corriendo (`ollama serve`)
  - Modelo Ollama descargado: `ollama pull qwen2.5:7b` (modelo por defecto, optimizado para M4)
  - `OPENAI_API_KEY` configurada para Stage 2
- Uso básico:
  ```bash
  python -m scraper.translate_hybrid \
    --input-dir output/tribulation \
    --start 1 --end 100 \
    --ollama-model qwen2.5:7b \
    --gpt-model gpt-4o-mini \
    --resume
  ```
  Los archivos traducidos se guardan en `traduccion/` por defecto (usa `--output-dir` para cambiarlo).
- Opciones útiles:
  - `--skip-stage1`: Omitir Stage 1 (usar borradores existentes `*_draft_es.txt`)
  - `--skip-stage2`: Solo ejecutar Stage 1 (generar borradores)
  - `--ollama-temp 0.2`: Temperature para Ollama (Stage 1)
  - `--gpt-temp 0.3`: Temperature para GPT (Stage 2)
  - `--chunk-chars 5000`: Tamaño de chunks (default: 5000 para M4)
  - `--max-concurrent 2`: Chunks procesados en paralelo dentro de cada capítulo (default: 2 para M4)
  - `--max-concurrent-chapters 1`: Capítulos procesados en paralelo (default: 1 para evitar saturar Ollama)
- Flujo típico:
  1. Stage 1 genera borradores rápidos con Ollama (Qwen2.5 7B)
  2. Stage 2 refina los borradores con GPT (solo texto español)
  3. Salida final: `NNNN_es.txt` con traducción refinada
- Optimizado para MacBook Air M4:
  - Modelo por defecto: `qwen2.5:7b` (mejor balance calidad/velocidad)
  - Chunks más pequeños (5000 chars) y menor concurrencia (2) para evitar sobrecarga

Uso con Ollama (local, solo Stage 1)

- Instala Ollama en macOS:
  - Con Homebrew: `brew install ollama`
  - O script oficial: `curl https://ollama.ai/install.sh | sh`
- Arranca el servicio: `ollama serve`
- Descarga un modelo ligero (ejemplos):
  - `ollama pull llama3.2:3b` (≈2–4 GB quantizado)
  - `ollama pull mistral:7b`
- Ejecuta el traductor usando Ollama:
  - `python -m scraper.translate_to_es --provider ollama --model llama3.2:3b --input-dir output/tribulation --resume`
  - O con el script dedicado: `python -m scraper.translate_to_es_ollama --input-dir output/tribulation --resume`
  - Si cambias el puerto/host: `--ollama-url http://localhost:11434`
- Notas:
  - No requiere `OPENAI_API_KEY` (solo para Ollama puro).
  - Para equipos con 16 GB RAM, prioriza modelos 3B–7B en versión quantizada (Q4/Q5).

Salida de traducción

- Archivos por capítulo: `NNNN_es.txt` en el mismo directorio de salida.
- Índice: `index_es.jsonl` con `title_en`, `title_es`, `file_en`, `file_es`, `model` y longitudes.
- El script preserva nombres propios y lugares, y traduce poderes/niveles/técnicas.
- Personaliza reglas con `config/translation_glossary.json`:
  - `never_translate`: términos a mantener tal cual (nombres, topónimos, organizaciones).
  - `translations`: glosario forzado (p.ej., "Source Opening" => "Apertura de Origen").
  - Reglas de pre/post-proceso: placeholders para proteger nombres y reemplazos regex.

Glosario automático (descubrimiento en marcha)

- `--auto-glossary`: antes de traducir cada capítulo, el script pide al modelo extraer:
  - `never_translate`: nombres de PERSONAS y LUGARES (base: "Nanyuan", "Great Xia").
  - `translations`: términos de poderes/niveles/técnicas con su traducción.
- `--persist-glossary`: guarda los términos detectados en `config/translation_glossary.json` tras cada capítulo.
- El traductor respeta el glosario y usa placeholders para asegurar que los nombres no se traduzcan; en compuestos traduce el descriptor y mantiene el nombre ("Nanyuan City" => "Ciudad de Nanyuan").

Notas de seguridad

- No pegues tu API key en archivos; usa `OPENAI_API_KEY` en el entorno (solo si usas OpenAI).
- Este repo no envía tu clave a ningún sitio; el script sólo la lee del entorno al ejecutar.

Estructura del repo

- `scraper/scrape_lightnovelpub.py`: Script principal y CLI.
- `requirements.txt`: Dependencias.
- `docs/arquitectura.md`: Flujo de scraping.
- `docs/estructura_pagina.md`: Detalle de páginas y heurísticas.
- `docs/formato_salida.md`: Especificación de salida local.

Uso recomendado

- Descubre el total y descarga todo:
  - `python -m scraper.scrape_lightnovelpub --output-dir output/tribulation --discover-total`
- O fija rango manual:
  - `python -m scraper.scrape_lightnovelpub --start 1 --end 100 --output-dir output/tribulation`
- Reanudar (omitirá existentes):
  - `python -m scraper.scrape_lightnovelpub --discover-total --resume --output-dir output/tribulation`

Notas

- Si el sitio aplica protección (Cloudflare), el script intentará usar `cloudscraper` si está instalado. Si no, puedes instalarlo (`pip install cloudscraper`) o incrementar los delays.
- Respeta el sitio: usa `--delay 2` o mayor y no satures con concurrencia (no está activada por defecto).


python -m scraper.translate_to_es --input-dir output/tribulation --start 227 --end 300 --output-dir traduccion --auto-glossary
        --persist-glossary --verbose --env-file .env --api-timeout 120 --chunk-chars 3500 --resume



        Ran
  └ .venv/bin/python scripts/generate_pdfs.py --input traduccion --output output/pdfs --block-size 50 --basename novela --cover 'config/
        cover.jpg' --tnr-dir '/System/Library/Fonts/Supplemental' --ingest-glossary 'config/ingest_glossary.json' | sed -n '1,160p'

.venv/bin/python scripts/generate_epubs.py --input traduccion --output output/epub --range 301-330 --basename novela
        --cover config/cover.jpg
        (.venv) daniel@MacBook-Air-de-Daniel scraper novela % .venv/bin/python scripts/generate_epubs.py --input traduccion --output output/epub --range 389-416 --basename novela --cover config/cover.jpg