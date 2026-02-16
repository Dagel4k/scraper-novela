Estructura de páginas relevante

Página principal de la novela

- URL: `https://lightnovelpub.org/novel/tribulation-of-myriad-races/`
- Campos de interés:
  - Título: `Tribulation of Myriad Races`
  - Autor: `Author: Eagle Eats Chicken`
  - Total de capítulos: texto como `1644 Chapters`
- El índice completo no está en el HTML base (se carga dinámicamente), por eso se itera por patrón numérico.

Página de capítulo

- URL: `.../chapter/<N>/`
- Encabezado principal (`h1`) con el patrón `Chapter N: <Título>`.
- Cuerpo: párrafos de narrativa justo después del `h1`.

Heurística de extracción del cuerpo

1) Encontrar `h1` que contenga la palabra "Chapter".
2) A partir de ese nodo, tomar todos los párrafos consecutivos (`<p>`, con soporte de `<br>`) y bloques de texto narrativo.
3) Detenerse al encontrar secciones de UI/ads/comentarios: clases o ids que contengan `comment`, `nav`, `next`, `prev`, `share`, `rating`, `menu`, `footer`, `disqus`, `ads`.
4) Normalizar texto y respetar saltos de párrafo.

Selectores candidatos de contenido

- `#chapter-content`, `.chapter-content`, `#chr-content`, `#chapter-container`, `.reading-content`, `article.chapter`
- Si alguno existe y contiene múltiples `<p>` o suficiente longitud, se toma directamente.
- Si no, se usa la navegación por hermanos desde el `h1` como fallback.

