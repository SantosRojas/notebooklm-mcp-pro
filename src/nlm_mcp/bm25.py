"""Búsqueda léxica BM25 sobre las fuentes cacheadas (sin dependencias externas).

Complementa a `search_sources` (relevancia semántica de NotebookLM): aquí la búsqueda es
local, determinista, instantánea y sin consumir cuota de la cuenta.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .mdtools import IMAGE_MARKER, chunk, snippet

_TOKEN = re.compile(r"\w+", re.UNICODE)
_STOP = frozenset(
    [
        "a",
        "al",
        "algo",
        "ante",
        "con",
        "como",
        "de",
        "del",
        "el",
        "ella",
        "ellos",
        "en",
        "es",
        "esta",
        "este",
        "esto",
        "la",
        "las",
        "lo",
        "los",
        "me",
        "mi",
        "muy",
        "no",
        "o",
        "para",
        "por",
        "que",
        "se",
        "si",
        "sin",
        "su",
        "sus",
        "un",
        "una",
        "unos",
        "unas",
        "y",
        "ya",
        "the",
        "of",
        "and",
        "to",
        "in",
        "is",
        "it",
        "that",
        "for",
        "on",
        "with",
        "as",
        "are",
        "was",
        "be",
        "by",
        "this",
        "an",
        "or",
        "at",
        "from",
        "not",
    ]
)


def tokenize(text: str) -> list[str]:
    # El marcador de imágenes retiradas no es contenido: fuera del índice y de las métricas.
    # Se quita la cadena completa (no la palabra "imagen") para no perder prosa legítima.
    text = text.replace(IMAGE_MARKER, " ")
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return [t for t in _TOKEN.findall(text) if len(t) > 1 and t not in _STOP]


def top_terms(text: str, limit: int = 12) -> list[dict[str, Any]]:
    """Términos más frecuentes del texto (sin palabras vacías), como radiografía rápida.

    Reusa el tokenizador de la búsqueda léxica para que los términos que devuelve
    sean exactamente los que `keyword_search` sabe encontrar."""
    counts = Counter(tokenize(text))
    return [{"term": t, "count": c} for t, c in counts.most_common(max(1, limit))]


@dataclass
class _Doc:
    source_id: str
    title: str
    heading: str
    start: int
    end: int
    text: str
    tf: Counter
    length: int


class BM25Index:
    def __init__(self, sources: Iterable[tuple[str, str, str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs: list[_Doc] = []
        for source_id, title, content in sources:
            for c in chunk(content):
                toks = tokenize(f"{title} {c.heading} {c.text}")
                if toks:
                    self.docs.append(
                        _Doc(source_id, title, c.heading, c.start, c.end, c.text, Counter(toks), len(toks))
                    )
        n = len(self.docs)
        self.avg = (sum(d.length for d in self.docs) / n) if n else 0.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.tf.keys())
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        q = tokenize(query)
        if not q or not self.docs:
            return []
        scored: list[tuple[float, _Doc]] = []
        for d in self.docs:
            score = 0.0
            for t in q:
                f = d.tf.get(t)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * d.length / (self.avg or 1))
                score += self.idf.get(t, 0.0) * (f * (self.k1 + 1)) / denom
            if score > 0:
                scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "source_id": d.source_id,
                "source_title": d.title,
                "heading": d.heading,
                "score": round(s, 3),
                "start": d.start,
                "end": d.end,
                "snippet": snippet(d.text),
            }
            for s, d in scored[:limit]
        ]
