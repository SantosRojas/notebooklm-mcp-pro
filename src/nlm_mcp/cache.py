"""Caché en disco de fuentes en Markdown.

Layout:  <cache_dir>/notebooks/<notebook_id>/<source_id>.md   (contenido)
                                              <source_id>.meta.json (título, url, fecha)

* Un archivo de metadatos por fuente => sin índice compartido => sin condiciones de carrera
  entre varios procesos del servidor.
* Escrituras atómicas (tmp + os.replace); permisos 0700/0600 (el contenido es privado).
* ttl == 0 desactiva la caché (siempre se vuelve a pedir).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from .models import SourceContent
from .validators import validate_id


def _private_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:  # p. ej. Windows
        pass


def _atomic_write(path: Path, data: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class SourceCache:
    def __init__(self, root: Path, ttl: int) -> None:
        self.root = Path(root) / "notebooks"
        self.ttl = ttl

    # ── rutas ──
    def _nb_dir(self, notebook_id: str) -> Path:
        return self.root / validate_id(notebook_id, "notebook_id")

    def _md(self, notebook_id: str, source_id: str) -> Path:
        return self._nb_dir(notebook_id) / f"{validate_id(source_id, 'source_id')}.md"

    def _meta(self, notebook_id: str, source_id: str) -> Path:
        return self._nb_dir(notebook_id) / f"{validate_id(source_id, 'source_id')}.meta.json"

    # ── API ──
    def get(self, notebook_id: str, source_id: str, *, allow_stale: bool = False) -> SourceContent | None:
        if self.ttl == 0 and not allow_stale:
            return None
        md, meta = self._md(notebook_id, source_id), self._meta(notebook_id, source_id)
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            content = md.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
        if not allow_stale and (time.time() - float(info.get("fetched_at", 0))) > self.ttl:
            return None
        return SourceContent(
            source_id=source_id,
            title=info.get("title", ""),
            content=content,
            char_count=len(content),
            url=info.get("url"),
        )

    def put(self, notebook_id: str, item: SourceContent) -> None:
        _private_mkdir(self._nb_dir(notebook_id))
        _atomic_write(self._md(notebook_id, item.source_id), item.content)
        _atomic_write(
            self._meta(notebook_id, item.source_id),
            json.dumps({"title": item.title, "url": item.url, "fetched_at": time.time()}),
        )

    def cached_ids(self, notebook_id: str) -> list[str]:
        d = self._nb_dir(notebook_id)
        if not d.is_dir():
            return []
        # Los temporales de _atomic_write (.tmp-*.md) pueden quedar tras un corte de energía;
        # su nombre no es un ID válido y haría fallar invalidate() en el siguiente sync().
        return sorted(p.stem for p in d.glob("*.md") if not p.name.startswith("."))

    def age_seconds(self, notebook_id: str, source_id: str) -> float | None:
        try:
            info = json.loads(self._meta(notebook_id, source_id).read_text(encoding="utf-8"))
            return max(0.0, time.time() - float(info["fetched_at"]))
        except (OSError, ValueError, KeyError):
            return None

    def invalidate(self, notebook_id: str, source_id: str | None = None) -> None:
        if source_id is None:
            shutil.rmtree(self._nb_dir(notebook_id), ignore_errors=True)
            return
        for p in (self._md(notebook_id, source_id), self._meta(notebook_id, source_id)):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    def signature(self, notebook_id: str, source_ids: list[str]) -> tuple[tuple[str, int], ...]:
        """Huella barata (id, mtime_ns) para saber si un índice en memoria sigue vigente."""
        sig = []
        for sid in sorted(source_ids):
            try:
                sig.append((sid, self._md(notebook_id, sid).stat().st_mtime_ns))
            except OSError:
                continue
        return tuple(sig)
