"""Registro de auditoría (JSONL) de operaciones de escritura y destructivas.

Nunca guarda contenido: solo longitud y huella SHA-256 truncada. Nunca lanza excepciones.
El archivo rota por tamaño: audit.jsonl -> audit.jsonl.1 (se conserva una generación).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("nlm_mcp.audit")
_REDACT = {"content", "text", "instructions"}
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB


class AuditLog:
    def __init__(self, path: Path, max_bytes: int = _DEFAULT_MAX_BYTES) -> None:
        self.path = Path(path)
        self.max_bytes = max(65536, int(max_bytes))  # con menos de 64 KiB la rotación no tiene sentido

    def _rotate_if_needed(self) -> None:
        """Pasa el log actual a .1 (sobrescribe la generación anterior) si supera el límite."""
        try:
            if self.path.stat().st_size < self.max_bytes:
                return
            rotated = self.path.with_suffix(self.path.suffix + ".1")
            os.replace(self.path, rotated)
            log.info("Registro de auditoría rotado a %s", rotated)
        except OSError:
            pass  # la rotación es un detalle operativo: nunca rompe el registro en curso

    def record(self, tool: str, **fields: Any) -> None:
        entry: dict[str, Any] = {"ts": round(time.time(), 3), "tool": tool}
        for key, value in fields.items():
            if key in _REDACT and isinstance(value, str):
                entry[key] = {"len": len(value), "sha256": hashlib.sha256(value.encode()).hexdigest()[:12]}
            else:
                entry[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed()
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception:  # noqa: BLE001 - auditar nunca debe romper la herramienta
            log.warning("No se pudo escribir el registro de auditoría en %s", self.path)
