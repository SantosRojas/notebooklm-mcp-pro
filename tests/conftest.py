"""Backend falso en memoria: permite probar todas las herramientas sin red ni cuenta de Google."""

from __future__ import annotations

from pathlib import Path

import pytest
from notebooklm import exceptions as nx

from nlm_mcp.config import Settings
from nlm_mcp.models import (
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
    UsageInfo,
    UsageWindowInfo,
)
from nlm_mcp.server import build_server

NB = "nb-111"
S_GUIDE, S_FAQ, S_PENDING = "src-guide", "src-faq", "src-pending"

GUIDE_MD = """# Guía de facturación

Introducción general.

## Instalación

Pasos de instalación del certificado digital.

```bash
# esto NO es un encabezado
openssl x509 -in cert.pem
```

## Emisión de comprobantes

La factura electrónica SUNAT se emite con el certificado.

### Notas de crédito

Las notas de crédito anulan comprobantes.

## Uso

Texto final.
"""

FAQ_MD = "# Preguntas frecuentes\n\n¿Cómo anulo una boleta? Mediante comunicación de baja.\n"


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fulltext_calls = 0
        self.notebooks = [
            NotebookInfo(NB, "Facturación SUNAT", 3),
            NotebookInfo("nb-222", "Otro notebook", 0),
        ]
        self.sources = [
            SourceInfo(S_GUIDE, "Guía de facturación", "markdown", "ready"),
            SourceInfo(S_FAQ, "FAQ", "pasted_text", "ready"),
            SourceInfo(S_PENDING, "Procesando", "pdf", "processing"),
        ]
        self.content = {S_GUIDE: GUIDE_MD, S_FAQ: FAQ_MD}
        self.notes = [NoteInfo("note-1", "Mi nota", "contenido de la nota")]
        self.artifacts = [
            ArtifactInfo("art-1", "Informe", "report", "completed"),
            ArtifactInfo("art-file", "opencode_spec.md", "file", "completed"),
            ArtifactInfo("art-pending", "Informe en curso", "report", "pending"),
        ]
        # Fallo simulado para comprobar que un error no relacionado no se disfraza de otro.
        self.report_error: Exception | None = None
        self.deleted: list[str] = []

    async def list_notebooks(self):
        return list(self.notebooks)

    async def get_overview(self, nb):
        return NotebookOverview(self.notebooks[0], "Resumen del notebook", ["tema 1"])

    async def list_sources(self, nb):
        return list(self.sources)

    async def get_source_markdown(self, nb, sid):
        self.fulltext_calls += 1
        text = self.content[sid]
        return SourceContent(sid, next(s.title for s in self.sources if s.id == sid), text, len(text))

    async def get_source_guide(self, nb, sid):
        return SourceGuide("resumen", ["sunat"])

    async def search(self, nb, query, source_ids, limit):
        return [ChunkHit(S_GUIDE, "fragmento relevante", 1)]

    async def ask(self, nb, question, source_ids, conversation_id):
        return Answer("Respuesta.", conversation_id or "conv-1", 1, [Citation(S_GUIDE, 1, "texto citado")])

    async def list_notes(self, nb):
        return list(self.notes)

    async def get_note(self, nb, nid):
        return self.notes[0]

    async def list_artifacts(self, nb):
        return list(self.artifacts)

    async def read_report(self, nb, aid):
        if self.report_error is not None:
            raise self.report_error
        if aid in ("art-file", "art-pending"):
            # NotebookLM responde lo mismo para un archivo que para un informe en curso.
            raise nx.ArtifactNotReadyError(aid)
        return "# Informe\n\ncuerpo"

    async def generation_status(self, nb, tid):
        return GenerationInfo(tid, "completed")

    async def get_usage(self):
        return UsageInfo("ready", enabled=True, ready=True, windows=[UsageWindowInfo("chat", 12.5, 87.5)])

    async def get_chat_history(self, nb, limit):
        turns = [ChatTurn("¿Cómo anulo una boleta?", "Respuesta con base en las fuentes.")]
        return turns[:limit]

    async def create_notebook(self, title):
        self.calls.append(("create_notebook", title))
        return NotebookInfo("nb-new", title)

    async def rename_notebook(self, nb, title):
        self.calls.append(("rename_notebook", nb, title))
        return NotebookInfo(nb, title)

    async def add_url(self, nb, url, wait):
        self.calls.append(("add_url", url, wait))
        return SourceInfo("src-new", url, "web_page", "ready", url=url)

    async def add_text(self, nb, title, content, wait):
        self.calls.append(("add_text", title))
        return SourceInfo("src-new", title, "pasted_text", "ready")

    async def add_file(self, nb, path: Path, wait):
        self.calls.append(("add_file", str(path)))
        return SourceInfo("src-new", path.name, "pdf", "ready")

    async def rename_source(self, nb, sid, title):
        self.calls.append(("rename", sid, title))

    async def create_note(self, nb, title, content):
        self.calls.append(("create_note", title))
        return NoteInfo("note-new", title, content)

    async def update_note(self, nb, nid, title, content):
        self.calls.append(("update_note", nid))

    async def generate_report(self, nb, fmt, source_ids, language, instructions):
        self.calls.append(("generate_report", fmt, language, instructions))
        return GenerationInfo("task-1", "pending")

    async def delete_source(self, nb, sid):
        self.deleted.append(sid)

    async def delete_notebook(self, nb):
        self.deleted.append(nb)

    async def delete_note(self, nb, nid):
        self.calls.append(("delete_note", nb, nid))

    async def delete_artifact(self, nb, aid):
        self.calls.append(("delete_artifact", nb, aid))

    async def close(self):
        pass


def make_settings(tmp_path: Path, **overrides) -> Settings:
    env = {
        "NLM_CACHE_DIR": str(tmp_path / "cache"),
        "NLM_AUDIT_LOG": str(tmp_path / "audit.jsonl"),
        "NLM_MAX_CHARS": "1000",
    }
    env.update({k: str(v) for k, v in overrides.items()})
    return Settings.from_env(env)


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def make_server(tmp_path, backend):
    def _make(**env):
        settings = make_settings(tmp_path, **env)
        return build_server(settings, backend), settings

    return _make
