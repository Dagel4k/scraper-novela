"""
glossary_extractor.py — Expansión incremental de config/translation_glossary.json

Escanea capítulos EN scrapeados, detecta términos propios nuevos y usa un LLM
para clasificarlos como:
  - "protect"  → añadir a never_translate (nombres, lugares sin traducción natural)
  - "translate" → añadir a translations con su equivalente español
  - "ignore"   → descartar (palabra común inglesa)

Output: actualiza config/translation_glossary.json directamente.
No genera mappings CN→EN (ese era el pipeline muerto).

Uso:
    python scripts/glossary_extractor.py --input output/tribulation-of-myriad-races \
        --glossary config/translation_glossary.json --start 1 --end 50
"""

import argparse
import json
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# ── Palabras comunes que nunca son términos propios ──────────────────────────
COMMON_WORDS = {
    # Artículos, pronombres, conjunciones
    "The", "A", "An", "And", "Or", "But", "If", "Then", "Else",
    "He", "She", "It", "They", "We", "You", "I",
    "My", "Your", "His", "Her", "Its", "Their", "Our",
    # Tratamientos
    "Mr", "Mrs", "Ms", "Dr", "Sir", "Madam",
    # Relaciones (no son proper nouns por sí solas)
    "Brother", "Sister", "Uncle", "Aunt", "Father", "Mother",
    "Dad", "Mom", "Grandpa", "Grandma", "Teacher", "Disciple",
    "Senior", "Junior", "Elder", "Master",
    # Onomatopeyas / expresiones
    "Sigh", "Humph", "Cough", "Laugh", "Smile", "Cry",
    "Boom", "Bang", "Crash", "Whoosh", "Swish", "Clang", "Hiss",
    # Números ordinales/cardinales
    "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
    "First", "Second", "Third", "Fourth", "Fifth",
    # Tiempo
    "Today", "Tomorrow", "Yesterday", "Now", "Soon", "Later",
    # Interrogativos
    "Here", "There", "Where", "What", "Who", "Why", "How",
    # Adjetivos comunes
    "Good", "Bad", "Big", "Small", "Old", "New", "High", "Low",
    "Strong", "Weak", "Fast", "Slow", "True", "False",
    # Verbos/adverbios frecuentes que el capitalizador confunde
    "Suddenly", "Finally", "Immediately", "Quickly", "Slowly",
    "However", "Moreover", "Furthermore", "Actually", "Gradually",
    # Miscelánea
    "Chapter", "Part", "Volume", "Book", "Haha", "Hahaha",
    # Palabras que se capitalizan al inicio de frase (muy frecuentes en novelas)
    "After", "Before", "Since", "While", "When", "That", "This", "These",
    "Those", "With", "Without", "Even", "Maybe", "Perhaps", "Rather",
    "From", "Into", "Onto", "Upon", "Within", "About", "Against", "Between",
    "Through", "During", "Although", "Because", "Unless", "Until", "Whether",
    "Someone", "Something", "Somewhere", "Anyone", "Anything", "Everyone",
    "Everything", "Nothing", "Nobody", "Nobody", "Somebody", "Thank", "Thanks",
    "Most", "Some", "Many", "Much", "More", "Less", "All", "Both", "Each",
    "Every", "Few", "Any", "Such", "Other", "Same", "Another", "Own",
    "Very", "Quite", "Really", "Truly", "Just", "Only", "Even", "Still",
    "Already", "Again", "Once", "Twice", "Always", "Never", "Often",
    "Calm", "Rise", "Look", "Back", "Long", "Light", "Dark", "Deep",
    "Pale", "Cold", "Warm", "Hard", "Soft", "Tall", "Short",
}


class GlossaryExpander:
    def __init__(
        self,
        input_dir: Path,
        glossary_path: Path,
        adapter: str = "openai",
        model: str = "gpt-4o-mini",
        min_freq: int = 3,
        batch_size: int = 20,
    ):
        self.input_dir = input_dir
        self.glossary_path = glossary_path
        self.min_freq = min_freq
        self.batch_size = batch_size

        # Cargar glosario existente
        self.glossary = self._load_glossary()

        # Conjunto de términos ya conocidos (para skip rápido)
        self.known_terms: set = set()
        self.known_terms.update(self.glossary.get("never_translate", []))
        self.known_terms.update(self.glossary.get("translations", {}).keys())

        # Cliente LLM
        self.client = self._init_client(adapter, model)
        self.model = model
        self.adapter = adapter

    # ── Setup ────────────────────────────────────────────────────────────────

    def _load_glossary(self) -> dict:
        if self.glossary_path.exists():
            with self.glossary_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            # Soportar formato envuelto en "glossary" o directo
            return data.get("glossary", data)
        return {
            "never_translate": [],
            "translations": {},
            "preprocess_rules": {"protect_tokens": {}, "restore_tokens": {}},
            "postprocess_rules": {"replace": {}},
        }

    def _init_client(self, adapter: str, model: str):
        if adapter == "openai":
            from openai import OpenAI
            return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        elif adapter == "gemini":
            from google import genai
            return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        else:
            raise ValueError(f"Adapter desconocido: {adapter}. Usa 'openai' o 'gemini'.")

    # ── Carga de capítulos ───────────────────────────────────────────────────

    def load_chapters(self, start: int, end: int) -> dict[int, str]:
        """Carga capítulos individuales EN del directorio de input."""
        chapters = {}
        for n in range(start, end + 1):
            # Formato: 0001_en.txt
            candidate = self.input_dir / f"{n:04d}_en.txt"
            if candidate.exists():
                chapters[n] = candidate.read_text(encoding="utf-8", errors="ignore")
        print(f"Cargados {len(chapters)} capítulos (rango {start}-{end}).")
        return chapters

    # ── Extracción de candidatos ─────────────────────────────────────────────

    # Palabras que nunca inician un nombre propio válido
    # Incluye: verbos auxiliares, preposiciones, artículos, conjunciones,
    # gerundios comunes, adjetivos genéricos que aparecen al inicio de frase
    _INVALID_STARTS = {
        # Verbos auxiliares y formas verbales comunes
        "Did", "Was", "Has", "Had", "Have", "Is", "Are", "Were",
        "Will", "Would", "Could", "Should", "Does", "Do", "May",
        "Might", "Can", "Must", "Shall", "Been", "Let", "Got",
        "Get", "Put", "Set", "Saw", "Heard", "Felt", "Told",
        "Said", "Asked", "Looked", "Walked", "Ran", "Knew", "Be",
        "Start", "Stop", "Show", "Harm", "Know", "Keep", "Take",
        "Come", "Go", "Make", "Give", "Find", "Watch", "Turn",
        "See", "Saw", "Look", "Think", "Feel", "Need", "Want",
        # Gerundios que inician frases (Seeing Su Yu, Getting To Know...)
        "Seeing", "Getting", "Hearing", "Saying", "Going", "Doing",
        "Being", "Having", "Feeling", "Looking", "Thinking", "Telling",
        "Coming", "Taking", "Making", "Keeping", "Trying", "Using",
        "Knowing", "Including", "Fanning", "Doubting",
        # Conjunciones y adverbios disyuntivos
        "Neither", "Either", "Whether", "Both", "Only", "Even",
        "Yet", "Still", "Already", "Just", "Quite", "Rather",
        # Preposiciones simples (In Great Xia, For Su Yu, To Harm...)
        "In", "On", "At", "Of", "To", "By", "As", "Up", "An",
        "For", "With", "From", "Into", "Onto", "Upon",
        # Preposiciones compuestas
        "Beside", "Beyond", "Behind", "Below", "Above", "Around",
        "Inside", "Outside", "Across", "Along", "Among", "Against",
        "Between", "Beneath", "Toward", "Towards", "Within", "Without",
        "Through", "During", "Except", "Near", "Over", "Under", "Past",
        "Like", "Unlike", "Despite", "Throughout",
        # Adjetivos comunes que inician frases
        "Brave", "Kind", "Extra", "Fresh", "Real", "Fake",
        "True", "False", "Numerous", "Terrifying", "Profile",
    }
    # Palabras que nunca terminan un nombre propio válido
    _INVALID_LAST = {
        "Of", "In", "At", "By", "For", "With", "From", "To", "And", "Or",
        "The", "Me", "Him", "Her", "His", "Us", "Them", "You", "It", "My",
        "Their", "Our", "Any", "Too", "Enough", "More",
    }
    # Si cualquiera de estas palabras aparece EN CUALQUIER posición del término,
    # lo descartamos: son indicadores de que es una oración, no un nombre propio.
    _SENTENCE_INDICATORS = {
        "Is", "Are", "Was", "Were", "Will", "Would", "Could", "Should",
        "Wont", "Cant", "Dont", "Didnt", "Isnt", "Arent", "Wasnt",
        "Im", "Ive", "Ill", "Youre", "Theyre", "Hes", "Shes", "Its",
        "This", "That", "These", "Those",
        "The", "An",               # artículo interno → frase común
        "Coming", "Going", "Being",
        "Cover", "Formed", "Over",
        "Us", "Me", "Him", "Her",  # pronombres objeto
        "Not", "Much", "Too",
    }

    def find_candidates(self, chapters: dict[int, str]) -> list[str]:
        """
        Solo busca frases multi-palabra (≥2 tokens capitalizados).

        Las palabras-única capitalizadas se descartan por completo:
        - en novelas de cultivo, cualquier nombre real también aparece en frases multi-palabra
        - las palabras únicas son casi todas palabras comunes al inicio de oración o
          términos genéricos (Realm, Soul, Blood) demasiado ambiguos sin contexto

        Filtros aplicados:
        - Frecuencia mínima (min_freq capítulos distintos)
        - No empieza con verbo auxiliar, preposición o palabra común
        - No termina con preposición
        - No ya conocido en el glosario
        """
        multi_pat = re.compile(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b')

        multi_freq: Counter = Counter()
        for text in chapters.values():
            for term in set(multi_pat.findall(text)):
                multi_freq[term] += 1

        candidates = []
        seen: set = set()

        for term, freq in multi_freq.items():
            if freq < self.min_freq:
                continue
            if term in self.known_terms or term in seen:
                continue

            words = term.split()
            first, last = words[0], words[-1]

            if first in COMMON_WORDS:
                continue
            if first in self._INVALID_STARTS:
                continue
                # Primer token es gerundio/participio → frase verbal, no nombre propio
            if first.endswith("ing") or first.endswith("ed"):
                continue
            if last in self._INVALID_LAST:
                continue
            # Rechazar si cualquier palabra del término es un indicador de oración
            if any(w in self._SENTENCE_INDICATORS for w in words):
                continue
            # Rechazar posesivos sin apóstrofe: "Bai Fengs" cuando "Bai Feng" ya existe
            if last.endswith("s") and term[:-1] in self.known_terms:
                continue

            seen.add(term)
            candidates.append(term)

        candidates.sort(key=lambda t: -multi_freq.get(t, 0))
        print(f"Candidatos nuevos: {len(candidates)} frases multi-palabra")
        return candidates

    # ── Clasificación con LLM ────────────────────────────────────────────────

    def _build_prompt(self, terms: list[str], context: str) -> str:
        terms_list = "\n".join(f'  - "{t}"' for t in terms)
        return f"""Eres un experto en clasificar términos de la novela china de cultivo "Tribulation of Myriad Races" (征服万族) para un glosario de traducción CN→ES.

Contexto (fragmento de capítulo):
---
{context[:600]}
---

Clasifica CADA término de esta lista según estas reglas ESTRICTAS:

ACCIONES:
- "protect" → SOLO si es un nombre propio ÚNICO que NO tiene traducción en español:
  nombres de personas (Su Yu, Bai Feng), nombres de lugares específicos (Nanyuan, Great Xia),
  nombres de clanes/sects/facciones como nombres propios (Single Character Faction).

- "translate" → SOLO si es un concepto/término técnico de cultivo con traducción natural al español:
  reinos de cultivo (Great Strength Realm → Reino de Gran Fuerza),
  técnicas/artes (War God Art → Arte del Dios de la Guerra),
  instituciones genéricas (Research Academy → Academia de Investigación).
  Proporciona la traducción en español.

- "ignore" → TODO lo demás: verbos, adjetivos, adverbios, palabras comunes, fragmentos de frase,
  expresiones que no son términos del mundo xianxia. ANTE LA DUDA → "ignore".
  Ejemplos: Cultural, Research, Fine, Take, Consider, Nonsense, Hundreds, Above, Toward,
  Impossible, Does, Please, Listen, Cultivate, Piece, Shit, Sure, Thus, Also.

LISTA A CLASIFICAR:
{terms_list}

Responde ÚNICAMENTE con JSON válido:
{{
  "término": {{"action": "protect"}},
  "término": {{"action": "translate", "es": "traducción española"}},
  "término": {{"action": "ignore"}}
}}
"""

    def _classify_batch_openai(self, terms: list[str], context: str) -> dict[str, dict]:
        prompt = self._build_prompt(terms, context)
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as e:
            print(f"  [error LLM] {e}")
            return {}

    def _classify_batch_gemini(self, terms: list[str], context: str) -> dict[str, dict]:
        from google.genai import types
        prompt = self._build_prompt(terms, context)
        try:
            result = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.0),
            )
            text = result.text.strip()
            # Gemini a veces envuelve en ```json ... ```
            text = re.sub(r"^```json\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            return json.loads(text)
        except Exception as e:
            print(f"  [error LLM] {e}")
            return {}

    def classify_batch(self, terms: list[str], context: str) -> dict[str, dict]:
        if self.adapter == "openai":
            return self._classify_batch_openai(terms, context)
        return self._classify_batch_gemini(terms, context)

    # ── Aplicar resultados al glosario ───────────────────────────────────────

    def apply_results(self, results: dict[str, dict]) -> tuple[int, int]:
        """Aplica los resultados de clasificación al glosario en memoria. Retorna (protect, translate)."""
        n_protect = 0
        n_translate = 0

        never_translate: list = self.glossary.setdefault("never_translate", [])
        translations: dict = self.glossary.setdefault("translations", {})

        for term, info in results.items():
            action = info.get("action", "ignore")
            if action == "protect":
                if term not in never_translate:
                    never_translate.append(term)
                    self.known_terms.add(term)
                    n_protect += 1
            elif action == "translate":
                es = info.get("es", "").strip()
                if es and term not in translations:
                    translations[term] = es
                    self.known_terms.add(term)
                    n_translate += 1

        return n_protect, n_translate

    def save_glossary(self) -> None:
        """Guarda el glosario preservando el formato envuelto en 'glossary'."""
        # Reconstruir protect_tokens y restore_tokens desde never_translate
        self._rebuild_protect_tokens()

        output = {"glossary": self.glossary}
        with self.glossary_path.open("w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

    def _rebuild_protect_tokens(self) -> None:
        """
        Regenera protect_tokens y restore_tokens desde never_translate
        para que el TextProcessor del pipeline los respete.
        """
        pre = self.glossary.setdefault("preprocess_rules", {})
        protect = pre.setdefault("protect_tokens", {})
        restore = pre.setdefault("restore_tokens", {})

        for term in self.glossary.get("never_translate", []):
            if term not in protect:
                slug = re.sub(r"[^A-Za-z0-9]+", "_", term).upper()[:40]
                ph = f"<PROTECT_{slug}_1>"
                # Evitar colisiones
                i = 1
                while ph in restore:
                    i += 1
                    ph = f"<PROTECT_{slug}_{i}>"
                protect[term] = ph
                restore[ph] = term
                # Alias sin número para robustez
                base = f"<PROTECT_{slug}>"
                if base not in restore:
                    restore[base] = term

    # ── Flujo principal ──────────────────────────────────────────────────────

    def run(self, start: int = 1, end: int = 50, dry_run: bool = False) -> None:
        chapters = self.load_chapters(start, end)
        if not chapters:
            print(f"No se encontraron capítulos en {self.input_dir} (rango {start}-{end})")
            return

        candidates = self.find_candidates(chapters)
        if not candidates:
            print("No hay candidatos nuevos. El glosario ya está actualizado.")
            return

        # Texto de contexto: concatenación de los primeros 5 capítulos disponibles
        sample_text = "\n\n".join(
            list(chapters.values())[:5]
        )

        total_protect = 0
        total_translate = 0
        total_batches = (len(candidates) + self.batch_size - 1) // self.batch_size

        print(f"Procesando {len(candidates)} candidatos en {total_batches} batches de {self.batch_size}...")

        for batch_idx in range(0, len(candidates), self.batch_size):
            batch = candidates[batch_idx : batch_idx + self.batch_size]
            batch_num = batch_idx // self.batch_size + 1
            print(f"  Batch {batch_num}/{total_batches}: {batch[:5]}{'...' if len(batch) > 5 else ''}")

            if dry_run:
                print(f"  [dry-run] Saltando llamada LLM")
                continue

            results = self.classify_batch(batch, sample_text)
            np, nt = self.apply_results(results)
            total_protect += np
            total_translate += nt

            print(f"  → +{np} protect, +{nt} translate")

            # Guardar cada batch (idempotente: si se interrumpe, no se pierde trabajo)
            self.save_glossary()
            time.sleep(0.5)  # Rate limiting suave

        print(f"\nFinalizado.")
        print(f"  Nuevos términos protegidos: {total_protect}")
        print(f"  Nuevas traducciones: {total_translate}")
        print(f"  Glosario guardado en: {self.glossary_path}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Expande config/translation_glossary.json escaneando capítulos EN."
    )
    parser.add_argument(
        "--input",
        default="output/tribulation",
        help="Directorio con capítulos EN individuales (ej: output/tribulation)",
    )
    parser.add_argument(
        "--glossary",
        default="config/translation_glossary.json",
        help="Ruta al glosario a expandir (default: config/translation_glossary.json)",
    )
    parser.add_argument("--start", type=int, default=1, help="Capítulo inicial")
    parser.add_argument("--end", type=int, default=50, help="Capítulo final")
    parser.add_argument(
        "--adapter",
        default="openai",
        choices=["openai", "gemini"],
        help="LLM a usar (default: openai)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Modelo específico (default: gpt-4o-mini para openai, gemini-2.0-flash para gemini)",
    )
    parser.add_argument(
        "--min-freq",
        type=int,
        default=3,
        help="Frecuencia mínima para considerar un término (default: 3)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Términos por llamada LLM (default: 20)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analizar sin hacer llamadas LLM ni guardar",
    )
    args = parser.parse_args()

    # Defaults de modelo según adapter
    model = args.model
    if model is None:
        model = "gpt-4o-mini" if args.adapter == "openai" else "gemini-2.0-flash"

    expander = GlossaryExpander(
        input_dir=Path(args.input),
        glossary_path=Path(args.glossary),
        adapter=args.adapter,
        model=model,
        min_freq=args.min_freq,
        batch_size=args.batch_size,
    )
    expander.run(start=args.start, end=args.end, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
