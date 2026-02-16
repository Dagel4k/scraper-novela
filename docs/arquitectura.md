Arquitectura y flujo del scraper

Resumen

- Dominio base: `https://lightnovelpub.org`
- Novela: `/novel/tribulation-of-myriad-races/`
- Capítulos: `/novel/tribulation-of-myriad-races/chapter/<N>/`

Flujo

1) Inicialización
- Configurar `BASE_URL`, directorio de salida, encabezados HTTP, delays.

2) Descubrir total de capítulos
- Descargar la página base de la novela.
- Buscar patrón `<n> Chapters` y extraer el entero.

3) Iteración por capítulos
- Para `N` en `1..TOTAL` (o rango dado):
  - Construir URL del capítulo.
  - Descargar HTML con reintentos.
  - Extraer título (del `h1` con "Chapter").
  - Extraer cuerpo narrativo: párrafos consecutivos desde después del `h1` hasta antes de elementos UI.
  - Guardar `NNNN_en.txt` y registrar metadatos en `index.jsonl`.

4) Traducción (fase posterior)
- No incluida. Se sugiere cargar los archivos guardados y generar `NNNN_es.txt`.

Consideraciones

- Evitar depender de JavaScript: todo por HTML estático y patrón de URL.
- Manejo de protección (Cloudflare): opción de usar `cloudscraper` si está disponible.
- Polite scraping: delays configurables, reintentos exponenciales, reanudación.

