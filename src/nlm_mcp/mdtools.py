"""Utilidades de Markdown: esquema de encabezados, secciones, paginación, grep y troceado."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from typing import Any

from .validators import UserError

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    line: int  # 1-based
    offset: int  # offset de caracteres donde empieza la línea del encabezado

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def outline(text: str) -> list[Heading]:
    """Encabezados ATX (#..######), ignorando los que están dentro de bloques de código."""
    result: list[Heading] = []
    in_fence = False
    offset = 0
    for lineno, line in enumerate(text.splitlines(keepends=True), start=1):
        stripped = line.rstrip("\r\n")
        if _FENCE.match(stripped):
            in_fence = not in_fence
        elif not in_fence:
            m = _HEADING.match(stripped)
            if m:
                result.append(Heading(len(m.group(1)), m.group(2).strip(), lineno, offset))
        offset += len(line)
    return result


def extract_section(text: str, heading: str, occurrence: int = 1) -> tuple[str, Heading]:
    """Devuelve la sección cuyo título coincide (exacto sin mayúsculas; si no, subcadena única).
    La sección llega hasta el siguiente encabezado del mismo nivel o superior."""
    heads = outline(text)
    if not heads:
        raise UserError("El documento no tiene encabezados Markdown; usa read_source con offset/max_chars.")
    needle = heading.strip().lower()
    if not needle:
        raise UserError("heading no puede estar vacío.")
    exact = [h for h in heads if h.title.lower() == needle]
    matches = exact or [h for h in heads if needle in h.title.lower()]
    if not matches:
        raise UserError(f"No hay ningún encabezado que coincida con {heading!r}. Usa get_source_outline.")
    if not exact and len(matches) > 1 and occurrence == 1:
        titles = ", ".join(repr(h.title) for h in matches[:6])
        raise UserError(f"Encabezado ambiguo ({len(matches)} coincidencias): {titles}. Sé más específico.")
    if occurrence < 1 or occurrence > len(matches):
        raise UserError(f"occurrence fuera de rango (hay {len(matches)}).")
    start = matches[occurrence - 1]
    end = len(text)
    for h in heads:
        if h.offset > start.offset and h.level <= start.level:
            end = h.offset
            break
    return text[start.offset : end].rstrip() + "\n", start


def paginate(text: str, offset: int, max_chars: int) -> dict[str, Any]:
    total = len(text)
    offset = max(0, min(int(offset), total))
    end = min(total, offset + max_chars)
    if end < total:  # corta en salto de línea si hay uno en el último 20 % de la ventana
        cut = text.rfind("\n", offset + int(max_chars * 0.8), end)
        if cut != -1:
            end = cut + 1
    return {
        "content": text[offset:end],
        "offset": offset,
        "next_offset": end if end < total else None,
        "total_chars": total,
        "truncated": end < total,
    }


def grep(
    text: str,
    pattern: re.Pattern[str],
    context: int = 2,
    max_matches: int = 50,
    deadline: float | None = None,
) -> list[dict[str, Any]]:
    """Busca línea a línea y devuelve coincidencias con contexto.

    `deadline` (time.monotonic()) corta el escaneo entre líneas si una regex
    costosa está consumiendo más tiempo del presupuestado (mejor esfuerzo: la
    evaluación de una línea es atómica y esa línea individual puede tardar)."""
    lines = text.splitlines()
    out: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if deadline is not None and time.monotonic() > deadline:
            break
        if pattern.search(line):
            out.append(
                {
                    "line": i + 1,
                    "text": line[:500],
                    "before": [ln[:300] for ln in lines[max(0, i - context) : i]],
                    "after": [ln[:300] for ln in lines[i + 1 : i + 1 + context]],
                }
            )
            if len(out) >= max_matches:
                break
    return out


@dataclass(frozen=True)
class Chunk:
    start: int
    end: int
    heading: str
    text: str


def chunk(text: str, target: int = 1200) -> list[Chunk]:
    """Trocea por párrafos respetando encabezados; cada trozo recuerda su encabezado."""
    heads = outline(text)
    chunks: list[Chunk] = []
    bounds = [h.offset for h in heads] + [len(text)]
    if not heads or bounds[0] > 0:
        bounds = [0] + bounds
    titles = ([""] if not heads or heads[0].offset > 0 else []) + [h.title for h in heads]
    for idx in range(len(bounds) - 1):
        seg_start, seg_end, title = bounds[idx], bounds[idx + 1], titles[idx]
        buf_start, buf = seg_start, ""
        pos = seg_start
        for para in re.split(r"(\n{2,})", text[seg_start:seg_end]):
            if buf and len(buf) + len(para) > target:
                chunks.append(Chunk(buf_start, pos, title, buf.strip()))
                buf_start, buf = pos, ""
            buf += para
            pos += len(para)
        if buf.strip():
            chunks.append(Chunk(buf_start, pos, title, buf.strip()))
    return chunks


def snippet(text: str, width: int = 400) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


# ── imágenes incrustadas (base64) ─────────────────────────────────────────────────────────
# NotebookLM incrusta las imágenes de los PDF directamente en el Markdown (data URI).
# Un solo screenshot puede pesar 300-400 KB y ahogar la caché, BM25 y las exportaciones.

_IMG_MD = re.compile(r"!\[[^\]]{0,120}\]\(\s*data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]+\s*\)")
_IMG_URI = re.compile(r"data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]{64,}")

_PLACEHOLDER = "_[imagen retirada]_"
"""Marcador que sustituye a cada imagen. bm25.tokenize lo descarta antes de indexar
para que el propio marcador no aparezca como término dominante (ver bm25.IMAGE_MARKER)."""
IMAGE_MARKER = _PLACEHOLDER

# Rachas de marcadores consecutivos: un deck de diapositivas puede pegar 15 imágenes
# seguidas. Se colapsan a una sola tira para no llenar la respuesta con ruido; la forma
# exacta se conserva cuando la imagen es única (no se normalizan sus espacios).
_IMG_RUN = re.compile(rf"(?:{re.escape(_PLACEHOLDER)}\s*){{2,}}")


def strip_inline_images(text: str) -> str:
    """Reemplaza imágenes incrustadas (data URI base64) por un marcador de texto.

    La conversión HTML→Markdown de NotebookLM puede meter en un PDF exportado
    decenas de imágenes binarias (ej. trozos de slides); si no se limpian, cualquier
    grep o índice BM25 se rompe por ruido y cada descarga se multiplica ~100x."""
    if "base64" not in text:  # el caso común, evita escanear todo
        return text
    text = _IMG_MD.sub(_PLACEHOLDER, text)
    text = _IMG_RUN.sub(f"{_PLACEHOLDER}\n\n", text)  # rachas -> un solo marcador
    return _IMG_URI.sub("[imagen]", text)
