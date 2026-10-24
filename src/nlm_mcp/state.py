"""Estado compartido del servidor: concurrencia, caché y sincronización de fuentes."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from typing import Any, TypeVar

from .audit import AuditLog
from .backend import Backend
from .bm25 import BM25Index
from .cache import SourceCache
from .config import Settings
from .errors import describe
from .mdtools import strip_inline_images
from .models import SourceContent, SourceInfo

log = logging.getLogger("nlm_mcp")
T = TypeVar("T")


@dataclass
class _LockEntry:
    """Candado de descarga por (notebook, fuente) con contador de interesados.

    El contador permite eliminar la entrada cuando nadie la usa ni la espera,
    evitando que el diccionario crezca indefinidamente en un servidor de larga vida."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    refs: int = 0


class AppState:
    def __init__(self, settings: Settings, backend: Backend) -> None:
        self.settings = settings
        self.backend = backend
        self.cache = SourceCache(settings.cache_dir, settings.cache_ttl)
        self.audit = AuditLog(settings.audit_log, max_bytes=settings.audit_max_bytes)
        self._sem = asyncio.Semaphore(settings.max_concurrency)
        self._locks: dict[tuple[str, str], _LockEntry] = {}
        self._bm25: dict[str, tuple[tuple, BM25Index]] = {}

    # ── llamadas al backend con límite de concurrencia y timeout ──
    async def run(self, awaitable: Awaitable[T], timeout_factor: float = 1.0) -> T:
        async with self._sem:
            return await asyncio.wait_for(awaitable, timeout=self.settings.timeout * timeout_factor)

    # ── contenido de fuentes ──
    def _lock(self, notebook_id: str, source_id: str) -> _LockEntry:
        key = (notebook_id, source_id)
        entry = self._locks.get(key)
        if entry is None:
            entry = self._locks[key] = _LockEntry()
        return entry

    async def fetch_markdown(
        self, notebook_id: str, source_id: str, force: bool = False
    ) -> tuple[SourceContent, float]:
        """Devuelve (contenido, antigüedad_en_segundos). Una sola descarga por fuente a la vez."""
        entry = self._lock(notebook_id, source_id)
        entry.refs += 1  # referenciado desde aquí hasta terminar (incluidos los que esperan)
        try:
            async with entry.lock:
                if not force:
                    hit = self.cache.get(notebook_id, source_id)
                    if hit is not None:
                        return hit, self.cache.age_seconds(notebook_id, source_id) or 0.0
                item = await self.run(self.backend.get_source_markdown(notebook_id, source_id))
                if self.settings.strip_images:  # PDFs con screenshots: 1 imagen puede ser ~300 KB
                    limpio = strip_inline_images(item.content)
                    if limpio != item.content:
                        log.info(
                            "base64 limpio %s/%s: %s -> %s caracteres",
                            notebook_id,
                            source_id,
                            len(item.content),
                            len(limpio),
                        )
                        item = dc_replace(item, content=limpio, char_count=len(limpio))
                self.cache.put(notebook_id, item)
                return item, 0.0
        finally:
            entry.refs -= 1
            if entry.refs == 0 and self._locks.get((notebook_id, source_id)) is entry:
                del self._locks[(notebook_id, source_id)]

    async def sync(self, notebook_id: str, force: bool = False) -> dict[str, Any]:
        """Descarga (o refresca) todas las fuentes listas de un notebook a la caché local."""
        sources = await self.run(self.backend.list_sources(notebook_id))
        ready = [s for s in sources if s.status == "ready"]
        remote_ids = {s.id for s in sources}
        removed = 0
        for sid in self.cache.cached_ids(notebook_id):  # fuentes borradas en remoto
            if sid not in remote_ids:
                self.cache.invalidate(notebook_id, sid)
                removed += 1

        counts = {"downloaded": 0, "from_cache": 0}
        errors: list[dict[str, str]] = []

        async def one(s: SourceInfo) -> None:
            try:
                fresh = (not force) and self.cache.get(notebook_id, s.id) is not None
                await self.fetch_markdown(notebook_id, s.id, force=force)
                counts["from_cache" if fresh else "downloaded"] += 1
            except Exception as exc:  # noqa: BLE001 - una fuente rota no aborta el resto
                ref = uuid.uuid4().hex[:8]
                log.exception("ref=%s fallo descargando %s/%s", ref, notebook_id, s.id)
                errors.append({"source_id": s.id, "title": s.title, "error": describe(exc), "ref": ref})

        await asyncio.gather(*(one(s) for s in ready))
        return {
            "notebook_id": notebook_id,
            "sources_total": len(sources),
            "sources_ready": len(ready),
            "not_ready_skipped": len(sources) - len(ready),
            "removed_from_cache": removed,
            **counts,
            "errors": errors,
        }

    async def ensure_cached(
        self, notebook_id: str, source_ids: Sequence[str]
    ) -> tuple[int, list[dict[str, str]]]:
        """Descarga solo las fuentes ausentes o caducadas.

        Devuelve (cuántas descargó, errores de descarga) para que las herramientas
        puedan avisar al agente de qué fuentes quedaron fuera de la búsqueda."""
        missing = [sid for sid in source_ids if self.cache.get(notebook_id, sid) is None]
        results = await asyncio.gather(
            *(self.fetch_markdown(notebook_id, sid) for sid in missing), return_exceptions=True
        )
        downloaded = 0
        errors: list[dict[str, str]] = []
        for sid, r in zip(missing, results, strict=True):  # gather respeta el orden
            if isinstance(r, BaseException):
                ref = uuid.uuid4().hex[:8]
                log.exception("ref=%s fallo descargando %s/%s", ref, notebook_id, sid)
                errors.append({"source_id": sid, "error": describe(r), "ref": ref})
            else:
                downloaded += 1
        return downloaded, errors

    # ── índice léxico local (memoizado por huella de la caché) ──
    def bm25_for(self, notebook_id: str, sources: Sequence[SourceInfo]) -> tuple[BM25Index, int]:
        cached = []
        for s in sources:
            item = self.cache.get(notebook_id, s.id, allow_stale=True)
            if item is not None:
                cached.append((s.id, s.title, item.content))
        sig = self.cache.signature(notebook_id, [c[0] for c in cached])
        key = f"{notebook_id}:{hash(sig)}"
        hit = self._bm25.get(key)
        if hit is None or hit[0] != sig:
            if len(self._bm25) > 8:
                self._bm25.clear()
            hit = (sig, BM25Index(cached))
            self._bm25[key] = hit
        return hit[1], len(cached)
