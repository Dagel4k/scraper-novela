# Estado Actual del Pipeline (2026-02-16) — v2

## Resumen ejecutivo

El proyecto tiene **un pipeline activo (EN→ES)** y **un pipeline nuevo (CN→ES)** recién implementado.
El pipeline muerto (Argos) fue documentado y descartado. El glosario fue auditado y limpiado.

---

## Arquitectura actual

```
data/cn_raws/          ← 998 capítulos chinos crudos (cn_0002 … cn_1000)
output/tribulation/    ← 1858 capítulos EN individuales (scraped de LightNovelPub)
data/alignment_map.json← {EN_num: CN_num} — 1797 EN → 542 CN únicos

traduccion/            ← Pipeline B (EN→ES): 33 caps traducidos
traduccion_cn/         ← Pipeline C (CN→ES): 5 caps traducidos (nuevo)

data/es_translations/  ← Pipeline A (MUERTO — basura de Argos)
```

---

## Pipeline A — MUERTO (`scripts/translator.py`)

**Estado:** Descartado. Documentado en `docs/estado_actual.md` (v1).
No invertir tiempo aquí. Sus artefactos (`data/glossary.json`, `scripts/glossary_gen.py`) son legacy.

---

## Pipeline B — ACTIVO (`main.py translate`)

**Flujo:** `output/tribulation/` (EN) → LLM → `traduccion/`

**Estado:** Funcional. 33 capítulos EN traducidos al español.

| Componente | Estado | Notas |
|---|---|---|
| `main.py` | ✅ | CLI unificado: scrape, translate, repair, export |
| `adapters/gemini_adapter.py` | ✅ | Gemini 2.0 Flash, async, retry con backoff |
| `adapters/openai_adapter.py` | ✅ | GPT-4o-mini, async, retry con backoff |
| `adapters/__init__.py` | ✅ | Factory `get_adapter()` |
| `interfaces/translator.py` | ✅ | `TranslationPipeline`, `PromptBuilder`, chunking concurrente |
| `core/domain.py` | ✅ | `Glossary`, `ChapterContent`, `TranslationResult` |
| `core/text_processor.py` | ✅ | Protect/restore tokens, chunking |
| `config/settings.yaml` | ✅ | Adapter activo: Gemini 2.0 Flash |
| `config/translation_glossary.json` | ✅ | 511 never_translate + 871 translations (limpiado) |

**Comando:**
```bash
python main.py translate --start 1 --end 50 --adapter gemini --resume
```

---

## Pipeline C — NUEVO (`scripts/translate_cn.py`)

**Flujo:** `data/cn_raws/` (CN) → LLM → `traduccion_cn/`

**Estado:** Implementado. 5 capítulos traducidos (CN 2–6).

**Estrategia de consistencia de nombres:**

1. **Glosario CN→ES directo** (41 entradas): combina el seed `CN_TO_EN` hardcodeado (personajes
   principales, lugares, reinos) con `config/translation_glossary.json` para resolver cada término
   a su valor español correcto. Ejemplos:
   - `苏宇 → Su Yu` (nombre propio, se conserva)
   - `万石境 → Myriad Stone Realm` (o el valor que esté en `translations`)

2. **Referencia EN** (capítulo alineado): el primer capítulo EN correspondiente al capítulo CN
   (vía `data/alignment_map.json`) se incluye truncado (~2000 chars) en el system prompt.
   El LLM lo usa **solo** para romanizar nombres nuevos no cubiertos por el glosario.

3. **Resume automático**: omite capítulos ya traducidos.

**Características:**
- Chunk size: 2500 chars CN (ajustable con `--chunk-chars`)
- Traducciones concurrentes dentro de cada capítulo
- Excluye notas del autor (`\u3000` prefix) y marcadores de fin de capítulo `(本章完)`
- Lee `.env` automáticamente (igual que `main.py`)

**Comandos:**
```bash
# Traducir capítulos CN 2 al 10
python scripts/translate_cn.py --start 2 --end 10

# Con OpenAI
python scripts/translate_cn.py --start 2 --end 50 --adapter openai

# Rango completo (resume-safe)
python scripts/translate_cn.py --start 2 --end 999
```

**Output:** `traduccion_cn/cn_XXXX_es.txt`

---

## Glosario (`config/translation_glossary.json`)

Estado tras la limpieza de esta sesión:

| Métrica | Antes | Después |
|---|---|---|
| `never_translate` | 604 | 511 |
| `translations` | 1097 | 871 |
| Conflictos (mismo término en ambas listas) | 117 | 0 |
| Duplicados CI en `translations` | 146 | 0 |
| Genéricos en `never_translate` (Zhang, Wu, etc.) | 38 | 0 |

**Reglas aplicadas:**
1. Términos de cultivo/habilidades con conflicto → quitados de `never_translate` (deben traducirse)
2. Nombres propios con conflicto → quitados de `translations` (deben protegerse)
3. Duplicados case-insensitive en `translations` → se conservó la traducción española más larga
4. Genéricos de una sola palabra → eliminados de `never_translate`
5. Traducciones triviales (ES == EN) → eliminadas

**Backup:** `config/translation_glossary.json.bak`
**Log completo:** `config/glossary_cleanup.log`

---

## Expansión del glosario (`scripts/glossary_extractor.py`)

Reescrito completamente. Ahora:
- Escanea capítulos EN en `output/tribulation/`
- Extrae candidatos multi-palabra capitalizados (frecuencia ≥ umbral)
- Clasifica vía LLM: `protect` / `translate` / `ignore`
- Escribe directamente a `config/translation_glossary.json`
- Idempotente y resumible (guarda por lote)
- Filtros de ruido: verbos, preposiciones, gerundios, posesivos, fragmentos

**Comando:**
```bash
python scripts/glossary_extractor.py --start 1 --end 100 --adapter gemini
```

---

## Disponibilidad de capítulos

| Fuente | Disponibles | Traducidos |
|---|---|---|
| EN (output/tribulation/) | 1858 | 33 (Pipeline B) |
| CN (data/cn_raws/) | 998 | 5 (Pipeline C) |

La relación CN↔EN es N:1: múltiples capítulos EN corresponden a un capítulo CN
(el scraper EN divide capítulos que en chino son uno solo).

---

## Scripts legacy / no usar

| Script | Estado |
|---|---|
| `scripts/translator.py` | ❌ LEGACY — Argos Translate CN→EN→ES, basura |
| `scripts/glossary_gen.py` | ❌ LEGACY — correlación estadística, glosario corrupto |
| `scripts/aligner.py` | ⚠️ Mantener — generó `alignment_map.json` |
| `scripts/scraper.py` | ✅ Activo vía `main.py scrape` |
| `scripts/combine_chapters.py` | ? No revisado |
| `scripts/run_ollama_translation.sh` | ? No revisado |

---

## Prioridades actuales

| Prioridad | Tarea |
|---|---|
| 🔴 Alta | Continuar traducción CN→ES (`translate_cn.py --start 7 --end 999`) |
| 🔴 Alta | Continuar traducción EN→ES (`main.py translate`) para los ~1800 caps restantes |
| 🟡 Media | Expandir glosario con más capítulos (`glossary_extractor.py --start 100 --end 500`) |
| 🟡 Media | Decidir si integrar `translate_cn.py` como subcomando de `main.py` |
| 🟢 Baja | Limpiar scripts legacy del directorio `scripts/` |
| 🟢 Baja | Revisar `scripts/combine_chapters.py` y `apertium_docker.sh` |
