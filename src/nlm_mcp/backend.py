"""Backend: interfaz mínima (Protocol) + implementación sobre notebooklm-py.

El servidor MCP solo conoce `Backend`. Esto permite:
  * probar todas las herramientas con un backend falso, sin red ni cuentas Google;
  * absorber cambios de la librería no oficial en un único archivo.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from .config import Settings
from .errors import SessionMissingError, is_transient
from .models import (
    Answer,
    ArtifactInfo,
    ChatTurn,
    ChunkHit,
    Citation,
    GenerationInfo,
    NotebookInfo,
    NotebookOverview,
    NoteInfo,
    SourceContent,
    SourceGuide,
    SourceInfo,
    UsageActionInfo,
    UsageInfo,
    UsageWindowInfo,
)

REPORT_FORMATS = ("briefing_doc", "study_guide", "blog_post", "concept_explanation", "custom")


class Backend(Protocol):
    # ── lectura ──
    async def list_notebooks(self) -> list[NotebookInfo]: ...
    async def get_overview(self, notebook_id: str) -> NotebookOverview: ...
    async def list_sources(self, notebook_id: str) -> list[SourceInfo]: ...
    async def get_source_markdown(self, notebook_id: str, source_id: str) -> SourceContent: ...
    async def get_source_guide(self, notebook_id: str, source_id: str) -> SourceGuide: ...
    async def search(
        self, notebook_id: str, query: str, source_ids: Sequence[str] | None, limit: int
    ) -> list[ChunkHit]: ...
    async def ask(
        self,
        notebook_id: str,
        question: str,
        source_ids: Sequence[str] | None,
        conversation_id: str | None,
    ) -> Answer: ...
    async def list_notes(self, notebook_id: str) -> list[NoteInfo]: ...
    async def get_note(self, notebook_id: str, note_id: str) -> NoteInfo: ...
    async def list_artifacts(self, notebook_id: str) -> list[ArtifactInfo]: ...
    async def read_report(self, notebook_id: str, artifact_id: str) -> str: ...
    async def generation_status(self, notebook_id: str, task_id: str) -> GenerationInfo: ...
    async def get_usage(self) -> UsageInfo: ...
    async def get_chat_history(self, notebook_id: str, limit: int) -> list[ChatTurn]: ...
    # ── escritura ──
    async def create_notebook(self, title: str) -> NotebookInfo: ...
    async def rename_notebook(self, notebook_id: str, title: str) -> NotebookInfo: ...
    async def add_url(self, notebook_id: str, url: str, wait: bool) -> SourceInfo: ...
    async def add_text(self, notebook_id: str, title: str, content: str, wait: bool) -> SourceInfo: ...
    async def add_file(self, notebook_id: str, path: Path, wait: bool) -> SourceInfo: ...
    async def rename_source(self, notebook_id: str, source_id: str, title: str) -> None: ...
    async def create_note(self, notebook_id: str, title: str, content: str) -> NoteInfo: ...
    async def update_note(self, notebook_id: str, note_id: str, title: str, content: str) -> None: ...
    async def generate_report(
        self,
        notebook_id: str,
        report_format: str,
        source_ids: Sequence[str] | None,
        language: str,
        instructions: str | None,
    ) -> GenerationInfo: ...
    # ── destructivo ──
    async def delete_source(self, notebook_id: str, source_id: str) -> None: ...
    async def delete_notebook(self, notebook_id: str) -> None: ...
    async def delete_note(self, notebook_id: str, note_id: str) -> None: ...
    async def delete_artifact(self, notebook_id: str, artifact_id: str) -> None: ...
    # ── ciclo de vida ──
    async def close(self) -> None: ...


# ───────────────────────── helpers de normalización ─────────────────────────


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _label(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, Enum):
        return value.name.lower() if isinstance(value.value, int) else str(value.value)
    return str(value)


def _source(s: Any) -> SourceInfo:
    return SourceInfo(
        id=s.id,
        title=s.title or "(sin título)",
        type=_label(getattr(s, "kind", None)),
        status=_label(getattr(s, "status", None)),
        url=getattr(s, "url", None),
        word_count=getattr(s, "word_count", None),
        created_at=_iso(getattr(s, "created_at", None)),
        revision_id=getattr(s, "revision_id", None),
    )


def _notebook(n: Any) -> NotebookInfo:
    return NotebookInfo(
        id=n.id,
        title=n.title or "(sin título)",
        sources_count=getattr(n, "sources_count", 0) or 0,
        created_at=_iso(getattr(n, "created_at", None)),
        modified_at=_iso(getattr(n, "modified_at", None)),
        emoji=getattr(n, "emoji", None),
        is_owner=bool(getattr(n, "is_owner", True)),
    )


def _note(n: Any) -> NoteInfo:
    return NoteInfo(
        id=n.id,
        title=n.title or "(sin título)",
        content=n.content or "",
        created_at=_iso(getattr(n, "created_at", None)),
    )


def _artifact(a: Any) -> ArtifactInfo:
    return ArtifactInfo(
        id=a.id,
        title=a.title or "(sin título)",
        type=_label(getattr(a, "kind", None)),
        status=str(getattr(a, "status_str", None) or _label(getattr(a, "status", None))),
        created_at=_iso(getattr(a, "created_at", None)),
        url=getattr(a, "url", None),
    )


def _generation(g: Any) -> GenerationInfo:
    return GenerationInfo(
        task_id=g.task_id,
        status=str(g.status),
        error=getattr(g, "error", None),
        url=getattr(g, "url", None),
    )


def _usage_window(w: Any) -> UsageWindowInfo:
    reset = getattr(w, "resets_at", None)
    return UsageWindowInfo(
        kind=_label(getattr(w, "kind", None)),
        used_percent=getattr(w, "used_percent", None),
        remaining_percent=getattr(w, "remaining_percent", None),
        resets_at=reset.isoformat()
        if reset is not None and hasattr(reset, "isoformat")
        else (str(reset) if reset else None),
    )


def _usage_action(a: Any) -> UsageActionInfo:
    return UsageActionInfo(
        code=getattr(a, "code", None),
        kind=_label(getattr(a, "kind", None)),
        has_sufficient_quota=getattr(a, "has_sufficient_quota", None),
        cost_tier=_label(getattr(a, "cost_tier", None)),
    )


def _usage(s: Any) -> UsageInfo:
    return UsageInfo(
        status=_label(getattr(s, "status", None)),
        enabled=bool(getattr(s, "enabled", False)),
        ready=bool(getattr(s, "ready", False)),
        windows=[_usage_window(w) for w in (getattr(s, "windows", None) or [])],
        actions=[_usage_action(a) for a in (getattr(s, "actions", None) or [])],
    )


def _chat_turn(turn: Any) -> ChatTurn:
    if isinstance(turn, ChatTurn):
        return turn
    if isinstance(turn, (tuple, list)) and len(turn) >= 2:  # (pregunta, respuesta)
        return ChatTurn(question=str(turn[0] or ""), answer=str(turn[1] or ""))
    q = getattr(turn, "question", None)
    a = getattr(turn, "answer", None)
    return ChatTurn(
        question=str(q or getattr(turn, "prompt", None) or ""),
        answer=str(a or getattr(turn, "response", None) or ""),
    )


# ───────────────────────── implementación real ─────────────────────────


async def _backoff_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class NotebookLMBackend:
    """Envuelve `notebooklm.NotebookLMClient`. La sesión se abre en el primer uso (lazy),
    de modo que el servidor arranca aunque la sesión esté caducada y el error llegue
    como un mensaje claro en la herramienta, no como un fallo de arranque."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._retries = settings.retries
        self._ctx: Any = None
        self._client: Any = None
        self._start_lock = asyncio.Lock()

    async def _read(self, make: Any) -> Any:
        """Operación de lectura con reintentos ante red/servidor/cuota (backoff exponencial corto).

        Las operaciones NO idempotentes (ask, escrituras, borrados) nunca se reintentan:
        un retry podría duplicar una respuesta de chat o una fuente añadida."""
        c = await self._c()
        for attempt in range(self._retries + 1):
            try:
                return await make(c)
            except Exception as exc:
                if attempt >= self._retries or not is_transient(exc):
                    raise
                await _backoff_sleep(min(1.5 * (2**attempt), 8.0))
        raise RuntimeError("unreachable")  # pragma: no cover

    async def _c(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._start_lock:
            if self._client is None:
                from notebooklm import NotebookLMClient  # import diferido: arranque rápido

                # El timeout por llamada lo impone AppState.run (asyncio.wait_for): no se pasa
                # a la librería para no depender de sus argumentos de ajuste (obsoletos en 0.8).
                ctx = NotebookLMClient.from_storage(
                    path=self._settings.storage_path,
                    profile=self._settings.profile,
                )
                try:
                    self._client = await ctx.__aenter__()
                except FileNotFoundError as exc:  # storage_state.json inexistente
                    raise SessionMissingError() from exc
                self._ctx = ctx
        return self._client

    async def close(self) -> None:
        if self._ctx is not None:
            ctx, self._ctx, self._client = self._ctx, None, None
            await ctx.__aexit__(None, None, None)

    # ── lectura ──
    async def list_notebooks(self) -> list[NotebookInfo]:
        return [_notebook(n) for n in await self._read(lambda c: c.notebooks.list())]

    async def get_overview(self, notebook_id: str) -> NotebookOverview:
        c = await self._c()
        nb = _notebook(await self._read(lambda c: c.notebooks.get(notebook_id=notebook_id)))
        summary, topics = "", []
        try:  # el resumen es opcional: no debe tumbar la consulta
            d = await c.notebooks.get_description(notebook_id=notebook_id)
            summary = d.summary or ""
            topics = [getattr(t, "question", None) or str(t) for t in (d.suggested_topics or [])]
        except Exception:
            pass
        return NotebookOverview(notebook=nb, summary=summary, suggested_topics=topics)

    async def list_sources(self, notebook_id: str) -> list[SourceInfo]:
        return [_source(s) for s in await self._read(lambda c: c.sources.list(notebook_id=notebook_id))]

    async def get_source_markdown(self, notebook_id: str, source_id: str) -> SourceContent:
        ft = await self._read(
            lambda c: c.sources.get_fulltext(
                notebook_id=notebook_id, source_id=source_id, output_format="markdown"
            )
        )
        return SourceContent(
            source_id=source_id,
            title=ft.title or "(sin título)",
            content=ft.content or "",
            char_count=ft.char_count or len(ft.content or ""),
            url=getattr(ft, "url", None),
        )

    async def get_source_guide(self, notebook_id: str, source_id: str) -> SourceGuide:
        g = await self._read(lambda c: c.sources.get_guide(notebook_id=notebook_id, source_id=source_id))
        return SourceGuide(summary=g.summary or "", keywords=list(g.keywords or ()))

    async def search(
        self, notebook_id: str, query: str, source_ids: Sequence[str] | None, limit: int
    ) -> list[ChunkHit]:
        sid = list(source_ids) if source_ids else None

        def call(c: Any) -> Any:
            return c.sources.search(notebook_id, query, source_ids=sid, limit=limit)

        chunks = await self._read(call)
        return [ChunkHit(source_id=k.source_id, text=k.text, rank=k.rank) for k in chunks]

    async def ask(
        self,
        notebook_id: str,
        question: str,
        source_ids: Sequence[str] | None,
        conversation_id: str | None,
    ) -> Answer:
        c = await self._c()  # NO reintentar: consume cuota y deja rastro en el historial
        r = await c.chat.ask(
            notebook_id,
            question,
            source_ids=list(source_ids) if source_ids else None,
            conversation_id=conversation_id,
        )
        cites = [
            Citation(
                source_id=ref.source_id,
                citation_number=ref.citation_number,
                cited_text=ref.cited_text,
            )
            for ref in (r.references or [])
        ]
        return Answer(
            answer=r.answer,
            conversation_id=r.conversation_id,
            turn_number=r.turn_number,
            citations=cites,
        )

    async def list_notes(self, notebook_id: str) -> list[NoteInfo]:
        return [_note(n) for n in await self._read(lambda c: c.notes.list(notebook_id=notebook_id))]

    async def get_note(self, notebook_id: str, note_id: str) -> NoteInfo:
        return _note(await self._read(lambda c: c.notes.get(notebook_id=notebook_id, note_id=note_id)))

    async def list_artifacts(self, notebook_id: str) -> list[ArtifactInfo]:
        return [_artifact(a) for a in await self._read(lambda c: c.artifacts.list(notebook_id=notebook_id))]

    async def read_report(self, notebook_id: str, artifact_id: str) -> str:
        with tempfile.TemporaryDirectory(prefix="nlm-report-") as tmp:
            out = os.path.join(tmp, "report.md")
            written = await self._read(
                lambda c: c.artifacts.download_report(
                    notebook_id=notebook_id, output_path=out, artifact_id=artifact_id
                )
            )
            return Path(written or out).read_text(encoding="utf-8", errors="replace")

    async def generation_status(self, notebook_id: str, task_id: str) -> GenerationInfo:
        return _generation(
            await self._read(lambda c: c.artifacts.poll_status(notebook_id=notebook_id, task_id=task_id))
        )

    async def get_usage(self) -> UsageInfo:
        return _usage(await self._read(lambda c: c.settings.get_usage()))

    async def get_chat_history(self, notebook_id: str, limit: int) -> list[ChatTurn]:
        turns = await self._read(lambda c: c.chat.get_history(notebook_id=notebook_id, limit=limit))
        return [_chat_turn(t) for t in (turns or [])]

    # ── escritura ──
    async def create_notebook(self, title: str) -> NotebookInfo:
        c = await self._c()
        return _notebook(await c.notebooks.create(title=title))

    async def rename_notebook(self, notebook_id: str, title: str) -> NotebookInfo:
        c = await self._c()
        return _notebook(await c.notebooks.rename(notebook_id=notebook_id, new_title=title))

    async def add_url(self, notebook_id: str, url: str, wait: bool) -> SourceInfo:
        c = await self._c()
        return _source(await c.sources.add_url(notebook_id=notebook_id, url=url, wait=wait))

    async def add_text(self, notebook_id: str, title: str, content: str, wait: bool) -> SourceInfo:
        c = await self._c()
        return _source(
            await c.sources.add_text(notebook_id=notebook_id, title=title, content=content, wait=wait)
        )

    async def add_file(self, notebook_id: str, path: Path, wait: bool) -> SourceInfo:
        c = await self._c()
        return _source(await c.sources.add_file(notebook_id=notebook_id, file_path=path, wait=wait))

    async def rename_source(self, notebook_id: str, source_id: str, title: str) -> None:
        c = await self._c()
        await c.sources.rename(notebook_id=notebook_id, source_id=source_id, new_title=title)

    async def create_note(self, notebook_id: str, title: str, content: str) -> NoteInfo:
        c = await self._c()
        return _note(await c.notes.create(notebook_id=notebook_id, title=title, content=content))

    async def update_note(self, notebook_id: str, note_id: str, title: str, content: str) -> None:
        c = await self._c()
        # OJO: la librería pide (content, title) en este orden (no el natural); por eso kwargs.
        await c.notes.update(notebook_id=notebook_id, note_id=note_id, content=content, title=title)

    async def generate_report(
        self,
        notebook_id: str,
        report_format: str,
        source_ids: Sequence[str] | None,
        language: str,
        instructions: str | None,
    ) -> GenerationInfo:
        from notebooklm import ReportFormat

        c = await self._c()
        fmt = ReportFormat[report_format.upper()]
        status = await c.artifacts.generate_report(
            notebook_id,
            fmt,
            source_ids=list(source_ids) if source_ids else None,
            language=language,
            custom_prompt=instructions if report_format == "custom" else None,
            extra_instructions=instructions if report_format != "custom" else None,
        )
        return _generation(status)

    # ── destructivo ──
    async def delete_source(self, notebook_id: str, source_id: str) -> None:
        c = await self._c()
        await c.sources.delete(notebook_id=notebook_id, source_id=source_id)

    async def delete_notebook(self, notebook_id: str) -> None:
        c = await self._c()
        await c.notebooks.delete(notebook_id=notebook_id)

    async def delete_note(self, notebook_id: str, note_id: str) -> None:
        c = await self._c()
        await c.notes.delete(notebook_id=notebook_id, note_id=note_id)

    async def delete_artifact(self, notebook_id: str, artifact_id: str) -> None:
        c = await self._c()
        await c.artifacts.delete(notebook_id=notebook_id, artifact_id=artifact_id)
