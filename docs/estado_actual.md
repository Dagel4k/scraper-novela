# Estado Actual del Pipeline (2026-02-16)

## Resumen ejecutivo

El proyecto tiene **dos pipelines de traducción que coexisten sin integrarse**. Uno está muerto. Otro funciona pero necesita un glosario mejor.

---

## Arquitectura actual

```
data/cn_raws/          ← 999 capítulos chinos crudos
TXT_Notebook/          ← capítulos EN scraped de LightNovelPub (chunks de 10)
output/tribulation.../ ← capítulos EN individuales (input del pipeline activo)
traduccion/            ← output del pipeline activo (ES)
data/es_translations/  ← output del pipeline muerto (basura)
```

---

## Pipeline A — MUERTO (scripts/translator.py)

**Flujo:** `data/cn_raws/` → Argos Translate (CN→EN→ES) → `data/es_translations/`

**Estado:** Inservible. El output de `es_0002.txt` es evidencia suficiente:
```
"One chewing, Su YuPROPER NAME 1735Z border Look Bluring to the kitchen"
```

**Causas raíz:**

| Problema | Descripción |
|---|---|
| Motor NMT offline (Argos) | Diseñado para frases turísticas. Incapaz de manejar narrativa literaria, metáforas o terminología xianxia |
| Pivot CN→EN→ES | Errores se acumulan. Si EN da 40% de precisión y ES da 40%, el resultado compuesto es ~16% |
| Tokens corruptos | `X{i}X` sobreviven el primer salto (CN→EN) pero Argos los corrompe en el segundo (EN→ES). El regex de restauración falla dejando `ZPROPER NAME 1735Z` en el texto final |
| `data/glossary.json` corrupto | Generado por `scripts/glossary_gen.py` con correlación estadística. Los valores son texto chino aleatorio co-ocurrente, no traducciones (ver tabla abajo) |

**Muestras del glosario corrupto (`data/glossary.json`):**

| Clave EN | Valor CN (actual) | Lo que realmente significa |
|---|---|---|
| `"Dad"` | `"自娱自乐"` | "entretenerse uno mismo" |
| `"Calm"` | `"剩余"` | "sobrante / resto" |
| `"Whoosh"` | `"四室"` | "cuatro habitaciones" |
| `"Hahaha"` | `"长发"` | "pelo largo" |
| `"Rip"` | `"没饭"` | "sin comida" |
| `"Ironwing Slash"` | `"后遗症"` | "secuelas médicas" |

**Conclusión:** Este pipeline y sus artefactos (`data/glossary.json`, `scripts/glossary_gen.py`, `data/es_translations/`) son legacy. No invertir tiempo aquí.

---

## Pipeline B — ACTIVO (main.py translate)

**Flujo:** `output/tribulation-of-myriad-races/` (EN) → LLM adapter → `traduccion/`

**Estado:** Arquitectura correcta. Necesita glosario más completo.

**Componentes:**

| Archivo | Estado | Notas |
|---|---|---|
| `main.py` | ✅ Funcional | CLI unificado: scrape, translate, repair, export |
| `adapters/gemini_adapter.py` | ✅ Listo | Gemini 2.0 Flash, async, retry con backoff |
| `adapters/openai_adapter.py` | ✅ Listo | GPT-4o-mini, async, retry con backoff |
| `adapters/__init__.py` | ✅ Listo | Factory `get_adapter()` |
| `interfaces/translator.py` | ✅ Listo | `TranslationPipeline`, `PromptBuilder`, chunking concurrente |
| `core/domain.py` | ✅ Listo | `Glossary`, `ChapterContent`, `TranslationResult` |
| `core/text_processor.py` | ✅ Listo | Protect/restore tokens, chunking |
| `config/settings.yaml` | ✅ Listo | Adapter activo: Gemini 2.0 Flash |
| `config/translation_glossary.json` | ⚠️ Incompleto | Tiene ~60+ entradas manuales correctas. Necesita expansión |
| `config/ingest_glossary.json` | ? | No revisado |

**El glosario activo (`config/translation_glossary.json`) funciona correctamente:**
```json
{
  "glossary": {
    "never_translate": ["Su Yu", "Liu Wenyan", "Nanyuan", ...],
    "translations": { "Great Strength Realm": "Reino Gran Fuerza", ... },
    "preprocess_rules": { "protect_tokens": { "Su Yu": "<PROTECT_SU_YU_1>" } }
  }
}
```
Los tokens `<PROTECT_...>` son mucho más robustos que los `X{i}X` del pipeline muerto porque el LLM los respeta como marcadores opacos.

---

## El problema del glosario

`scripts/glossary_extractor.py` existe y es conceptualmente correcto (usa GPT-4o-mini para buscar equivalentes chinos con contexto bilingüe). **Pero está desconectado del pipeline activo:**

- Genera `{ "English": "Chinese" }` (para el pipeline muerto)
- El pipeline activo necesita entradas en `config/translation_glossary.json`: qué términos proteger y qué traducir al español

Problemas adicionales en el extractor actual:
1. `context_cn = cn_text[:1200]` — usa solo los primeros 1200 chars del capítulo CN, sin importar dónde aparezca el término
2. No valida que el resultado chino realmente aparezca en el texto
3. El output no es compatible con el formato de `config/translation_glossary.json`

---

## Prioridades

| Prioridad | Tarea | Archivo afectado |
|---|---|---|
| 🔴 Alta | Reescribir `glossary_extractor.py` para generar entradas EN→protect/ES compatibles con `config/translation_glossary.json` | `scripts/glossary_extractor.py` |
| 🔴 Alta | Ampliar `config/translation_glossary.json` con términos faltantes | `config/translation_glossary.json` |
| 🟡 Media | Marcar explícitamente como legacy el pipeline A y sus scripts | `scripts/translator.py`, `scripts/glossary_gen.py` |
| 🟢 Baja | Limpiar `data/glossary.json` (no afecta al pipeline activo) | `data/glossary.json` |

---

## Comando de uso actual (pipeline activo)

```bash
# Traducir capítulos 1-10
python main.py translate --start 1 --end 10 --adapter gemini --resume

# Con OpenAI
python main.py translate --start 1 --end 10 --adapter openai

# Exportar a PDF
python main.py export pdf --input traduccion --output output/pdfs
```
