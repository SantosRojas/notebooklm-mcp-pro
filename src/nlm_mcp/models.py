"""Modelos planos y estables que el servidor expone al agente.

Desacoplan la API de herramientas del esquema interno (y no oficial) de notebooklm-py.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class _Model:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NotebookInfo(_Model):
    id: str
    title: str
    sources_count: int = 0
    created_at: str | None = None
    modified_at: str | None = None
    emoji: str | None = None
    is_owner: bool = True


@dataclass
class NotebookOverview(_Model):
    notebook: NotebookInfo
    summary: str = ""
    suggested_topics: list[str] = field(default_factory=list)


@dataclass
class SourceInfo(_Model):
    id: str
    title: str
    type: str = "unknown"
    status: str = "unknown"
    url: str | None = None
    word_count: int | None = None
    created_at: str | None = None
    revision_id: str | None = None


@dataclass
class SourceContent(_Model):
    source_id: str
    title: str
    content: str
    char_count: int
    url: str | None = None


@dataclass
class SourceGuide(_Model):
    summary: str = ""
    keywords: list[str] = field(default_factory=list)


@dataclass
class ChunkHit(_Model):
    source_id: str
    text: str
    rank: int = 0


@dataclass
class Citation(_Model):
    source_id: str
    citation_number: int | None = None
    cited_text: str | None = None


@dataclass
class Answer(_Model):
    answer: str
    conversation_id: str
    turn_number: int = 0
    citations: list[Citation] = field(default_factory=list)


@dataclass
class NoteInfo(_Model):
    id: str
    title: str
    content: str = ""
    created_at: str | None = None


@dataclass
class ArtifactInfo(_Model):
    id: str
    title: str
    type: str = "unknown"
    status: str = "unknown"
    created_at: str | None = None
    url: str | None = None


@dataclass
class GenerationInfo(_Model):
    task_id: str
    status: str
    error: str | None = None
    url: str | None = None


@dataclass
class UsageWindowInfo(_Model):
    kind: str = "unknown"
    used_percent: float | None = None
    remaining_percent: float | None = None
    resets_at: str | None = None


@dataclass
class UsageActionInfo(_Model):
    code: str | None = None
    kind: str = "unknown"
    has_sufficient_quota: bool | None = None
    cost_tier: str | None = None


@dataclass
class UsageInfo(_Model):
    """Resumen de cuota de la cuenta. Con DISABLED/SKIPPED no hay ventanas ni acciones."""

    status: str = "unknown"
    enabled: bool = False
    ready: bool = False
    windows: list[UsageWindowInfo] = field(default_factory=list)
    actions: list[UsageActionInfo] = field(default_factory=list)


@dataclass
class ChatTurn(_Model):
    """Un turno del historial de chat del notebook."""

    question: str = ""
    answer: str = ""
    created_at: str | None = None
