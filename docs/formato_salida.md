Formato de salida local

Archivos por capítulo

- Nombre: `NNNN_en.txt` (relleno con ceros: 0001, 0002, ...)
- Contenido:
  - Primera línea: título oficial del capítulo (del `h1`)
  - Línea en blanco
  - Cuerpo de la narrativa (párrafos separados por línea en blanco)

Índice JSONL

- Archivo: `index.jsonl`
- Una línea por capítulo con:
  - `number`: número de capítulo (int)
  - `title`: título completo (`Chapter N: ...`)
  - `url`: URL del capítulo
  - `file`: nombre del archivo guardado
  - `length`: caracteres del cuerpo
  - `retrieved_at`: ISO timestamp

Metadatos de novela (opcional)

- Archivo: `novel_meta.json`
- Campos: `title`, `author` (si se desea extraer), `total_chapters`.

