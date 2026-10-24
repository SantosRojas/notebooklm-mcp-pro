"""Validación de entradas que terminan en rutas de disco o en llamadas RPC."""

from __future__ import annotations

import re
from pathlib import Path

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MAX_QUERY = 2000
_MAX_REGEX = 300


class UserError(Exception):
    """Error de entrada del usuario/agente: se devuelve tal cual, sin traza."""


def validate_id(value: str, label: str = "id") -> str:
    """Los IDs de NotebookLM son UUID/alfanuméricos. Esto además impide path traversal
    cuando el ID se usa como nombre de carpeta/archivo en la caché."""
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise UserError(f"{label} inválido: solo se permiten letras, números, '-' y '_' (máx. 128).")
    return value


def validate_ids(values: list[str] | None, label: str = "source_id") -> list[str] | None:
    if not values:
        return None
    return [validate_id(v, label) for v in values]


def validate_conversation_id(value: str, label: str = "conversation_id") -> str:
    """El ID de conversación solo viaja por RPC, nunca a rutas de disco: se valida de forma
    permisiva (texto no vacío, sin caracteres de control) para no rechazar IDs reales
    de NotebookLM cuyo formato exacto es interno y puede cambiar."""
    if not isinstance(value, str) or not value.strip():
        raise UserError(f"{label} no puede estar vacío.")
    value = value.strip()
    if len(value) > 256 or any(ord(c) < 0x20 for c in value):
        raise UserError(f"{label} contiene caracteres inválidos.")
    return value


def validate_text(value: str, label: str, max_len: int = _MAX_QUERY) -> str:
    value = (value or "").strip()
    if not value:
        raise UserError(f"{label} no puede estar vacío.")
    if len(value) > max_len:
        raise UserError(f"{label} excede {max_len} caracteres.")
    return value


def validate_regex(pattern: str) -> re.Pattern[str]:
    pattern = validate_text(pattern, "pattern", _MAX_REGEX)
    try:
        return re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    except re.error as exc:
        raise UserError(f"Expresión regular inválida: {exc}") from exc


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def require_upload_dirs(allowed_dirs: tuple[Path, ...]) -> tuple[Path, ...]:
    """Garantiza que la subida de archivos está habilitada; si no, el error explica el remedio.

    Compartido por las dos vías de subida (`add_source_file` y `add_source_base64`) para que
    la advertencia sea idéntica y esté escrita en un solo sitio.
    """
    if not allowed_dirs:
        raise UserError(
            "La subida de archivos está deshabilitada. Define NLM_UPLOAD_DIRS con las "
            "carpetas permitidas para habilitarla."
        )
    return allowed_dirs


def resolve_upload_path(raw: str, allowed_dirs: tuple[Path, ...]) -> Path:
    """Devuelve una ruta existente dentro de las carpetas permitidas (sin symlinks que escapen)."""
    require_upload_dirs(allowed_dirs)
    try:
        path = Path(raw).expanduser().resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise UserError("El archivo no existe.") from exc
    if not path.is_file():
        raise UserError("La ruta no es un archivo.")
    if not any(path == d or d in path.parents for d in allowed_dirs):
        raise UserError("El archivo está fuera de las carpetas permitidas (NLM_UPLOAD_DIRS).")
    return path
