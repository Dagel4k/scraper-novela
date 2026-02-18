# Proceso de Traducción Chino -> Español

Este documento describe el flujo de trabajo completo para traducir capítulos de la novela desde el chino original al español, manteniendo la consistencia de nombres y términos técnicos.

## Resumen del Pipeline

El proceso consta de tres fases principales coordinadas mediante scripts en Python:

1.  **Alineación (Align):** Sincronización de capítulos chinos e ingleses.
2.  **Traducción Directa (Translate):** Traducción de Chino a Español usando el contexto inglés para nombres.
3.  **Preparación (Sync):** Preparación de datos para la aplicación de lectura.

---

## 1. Alineación (`scripts/aligner.py`)

Debido a que los capítulos en inglés a veces se dividen o agrupan de forma distinta al original, este script crea un mapa en `data/alignment_map.json`.

-   **Lógica:** Utiliza "anclas" (términos únicos como "Su Yu", "Nanyuan", "Willpower") y un ratio de progresión **dinámico** (0.66 al inicio, 0.38 al final) para determinar qué capítulo inglés corresponde a cada capítulo chino.
-   **Mejora Reciente:** Se ha implementado una ventana de búsqueda con "backtracking" para corregir desviaciones en la alineación de los últimos arcos (caps. > 1700).
-   **Fuente:** Lee los capítulos en inglés desde `output/tribulation` (formato `XXXX_en.txt`) y los chinos desde `data/cn_raws`.
-   **Resultado:** Un archivo JSON que permite al traductor saber qué capítulos de referencia consultar para extraer nombres consistentes.

## 2. Traducción y Glosario (`scripts/translate_cn.py`)

Es el motor principal. No traduce de Inglés a Español, sino que traduce de **Chino a Español directamente** usando el inglés solo como referencia para nombres propios.

### Estrategia de Consistencia:
1.  **Extracción de Nombres:** Para cada capítulo chino, el LLM analiza el texto original y los capítulos ingleses alineados para extraer un glosario temporal (Ej: 苏宇 -> Su Yu).
2.  **Adaptación al Español:** Los títulos y rangos en inglés se convierten a español según reglas predefinidas (Ej: *King* -> *Rey*, *Marquis* -> *Marqués*).
3.  **Glosario de Conceptos:** Se mezcla con un glosario estático (`CN_TO_ES_CONCEPTS`) que contiene términos de cultivo fijos (Reinos, Qi de origen, etc.) para asegurar que nunca cambien.
4.  **Traducción Literaria:** Se instruye al LLM para reestructurar la gramática telegráfica china en un estilo literario español fluido, evitando calcos.

## 3. Sincronización con Reader (`scripts/prepare_reader_data.py`)

Prepara los archivos finales para que sean visibles en la web.

-   Mueve los archivos de `traduccion_cn` a `reader-app/public/chapters`.
-   Genera `chapters.json` con metadatos (títulos y números) para que la app cargue la lista automáticamente.

---

## Recomendaciones de Mejora

-   **Portabilidad:** Los scripts ahora usan rutas relativas al proyecto (`os.getcwd()`) para funcionar en cualquier entorno.
-   **Portabilidad:** Los scripts ahora usan rutas relativas al proyecto (`os.getcwd()`) para funcionar en cualquier entorno.
-   **Alineación Verificada:** Se ha confirmado que el `alignment_map.json` actual es correcto.
    -   **Nota Importante:** Existe un desfase natural de numeración en los primeros capítulos: el archivo `cn_0002.txt` corresponde al contenido del Capítulo 1 (*Father and Son*), alineado correctamente con `EN 1`.
    -   La alineación se mantiene correcta hasta el final (Ej: CN 702 = EN 1837).
-   **Glosario LLM:** Se ha reemplazado la lógica de reglas fijas por una traducción de glosario basada en LLM (`translate_glossary_to_es`), lo que maneja mejor los títulos complejos (ej: "Stable Army Marquis" -> "Marqués del Ejército Estable").
