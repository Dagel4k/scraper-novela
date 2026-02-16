#!/usr/bin/env bash
set -euo pipefail

# Ejecuta traducción EN->ES con Ollama en bloques desde un inicio dado hasta el último capítulo disponible.
# Variables configurables por entorno:
#   INPUT_DIR (default: output/tribulation)
#   OUTPUT_DIR (default: traduccion)
#   START (default: 228)
#   BLOCK (default: 50)
#   MODEL (default: $OLLAMA_TRANSLATE_MODEL o 'llama3.2:3b')
#   OLLAMA_BASE_URL (default: http://localhost:11434)
#   CHUNK_CHARS (default: 3500)
#   MAX_CONCURRENT (default: 2)
#   API_TIMEOUT (default: 120)
#   AUTO_GLOSSARY (default: 1 -> activa --auto-glossary --persist-glossary)
#   RESUME (default: 1 -> activa --resume; si 0, sobreescribe)
#   TEMPERATURE (default: vacío -> deja el valor por defecto del script)

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

INPUT_DIR="${INPUT_DIR:-output/tribulation}"
OUTPUT_DIR="${OUTPUT_DIR:-traduccion}"
START="${START:-228}"
BLOCK="${BLOCK:-50}"
MODEL="${MODEL:-${OLLAMA_TRANSLATE_MODEL:-llama3.2:3b}}"
URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
CHUNK_CHARS="${CHUNK_CHARS:-3500}"
MAX_CONCURRENT="${MAX_CONCURRENT:-2}"
API_TIMEOUT="${API_TIMEOUT:-120}"
AUTO_GLOSSARY="${AUTO_GLOSSARY:-1}"
RESUME="${RESUME:-1}"
TEMPERATURE="${TEMPERATURE:-}"

if [ ! -x .venv/bin/python ]; then
  echo "[error] No se encontró .venv/bin/python. Activa o crea el entorno virtual e instala requirements." >&2
  exit 1
fi

if [ ! -d "$INPUT_DIR" ]; then
  echo "[error] INPUT_DIR no existe: $INPUT_DIR" >&2
  exit 1
fi

# Detectar último capítulo disponible en INPUT_DIR
TOT=$(ls -1 "$INPUT_DIR"/*_en.txt 2>/dev/null | sed -E 's|.*/||' | sed -E 's/_en\.txt$//' | sort -n | tail -n 1)
if [ -z "${TOT:-}" ]; then
  echo "[error] No se encontraron archivos *_en.txt en $INPUT_DIR" >&2
  exit 1
fi

echo "[cfg] Provider: ollama | URL: $URL | Model: $MODEL"
echo "[cfg] Range: $START..$TOT | Block: $BLOCK | Out: $OUTPUT_DIR"
echo "[cfg] chunk-chars: $CHUNK_CHARS | max-concurrent: $MAX_CONCURRENT | timeout: $API_TIMEOUT"
echo "[cfg] auto-glossary: $AUTO_GLOSSARY"

EXTRA_FLAGS=""
if [ "$AUTO_GLOSSARY" = "1" ]; then
  EXTRA_FLAGS="--auto-glossary --persist-glossary"
fi

if [ "$RESUME" = "1" ]; then
  EXTRA_FLAGS="$EXTRA_FLAGS --resume"
fi

mkdir -p "$OUTPUT_DIR"

S="$START"
while [ "$S" -le "$TOT" ]; do
  E=$((S + BLOCK - 1))
  if [ "$E" -gt "$TOT" ]; then E="$TOT"; fi
  echo "[run] Translating block $S..$E"
  .venv/bin/python -m scraper.translate_to_es_ollama \
    --ollama-url "$URL" \
    --model "$MODEL" \
    --input-dir "$INPUT_DIR" \
    --output-dir "$OUTPUT_DIR" \
    --start "$S" \
    --end "$E" \
    --chunk-chars "$CHUNK_CHARS" \
    --max-concurrent "$MAX_CONCURRENT" \
    --api-timeout "$API_TIMEOUT" \
    --verbose \
    ${TEMPERATURE:+--temperature "$TEMPERATURE"} \
    $EXTRA_FLAGS
  S=$((E + 1))
done

echo "[done] Traducción con Ollama completada de $START a $TOT."
