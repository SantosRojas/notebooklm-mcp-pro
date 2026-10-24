"""Valida el adaptador `NotebookLMBackend` contra los MODELOS REALES de notebooklm-py.

No hay red ni credenciales: se inyecta un cliente falso cuyas respuestas son instancias de las
clases reales de la librería. Si una versión futura renombra un atributo que leemos, estas
pruebas fallan antes de que lo haga el servidor en producción.
"""

from datetime import datetime, timezone

import pytest
from conftest import make_settings
from notebooklm import (
    Artifact,
    AskResult,
    ChatReference,
    GenerationStatus,
    Note,
    Notebook,
    RelevantChunk,
    Source,
)
from notebooklm.types import NotebookDescription, SourceFulltext, SourceGuide, SourceStatus

from nlm_mcp.backend import NotebookLMBackend, _label

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


class _Ns:  # espacio de nombres con métodos async
    def __init__(self, **fns):
        for k, v in fns.items():
            setattr(self, k, v)


def _async(value):
    async def fn(*a, **k):
        return value

    return fn


@pytest.fixture
def backend(tmp_path):
    b = NotebookLMBackend(make_settings(tmp_path))
    nb = Notebook(id="nb1", title="Mi notebook", created_at=NOW, sources_count=2, emoji="📘")
    src = Source(
        id="s1", title="Doc", status=SourceStatus.READY, url="https://x.test", word_count=120, created_at=NOW
    )
    art = Artifact(id="a1", title="Informe", _artifact_type=2, status=3)
    stub = _Ns(
        notebooks=_Ns(
            list=_async([nb]),
            get=_async(nb),
            create=_async(nb),
            get_description=_async(NotebookDescription(summary="Resumen", suggested_topics=[])),
        ),
        sources=_Ns(
            list=_async([src]),
            get_fulltext=_async(
                SourceFulltext(source_id="s1", title="Doc", content="# H\ntexto", char_count=8)
            ),
            get_guide=_async(SourceGuide(summary="guía", keywords=("k1", "k2"))),
            search=_async([RelevantChunk(source_id="s1", text="frag", rank=1)]),
            add_url=_async(src),
            add_text=_async(src),
            add_file=_async(src),
        ),
        chat=_Ns(
            ask=_async(
                AskResult(
                    answer="Resp",
                    conversation_id="c1",
                    turn_number=2,
                    is_follow_up=True,
                    references=[ChatReference(source_id="s1", citation_number=1, cited_text="cita")],
                )
            )
        ),
        notes=_Ns(
            list=_async([Note(id="n1", notebook_id="nb1", title="Nota", content="c")]),
            get=_async(Note(id="n1", notebook_id="nb1", title="Nota", content="c")),
            create=_async(Note(id="n2", notebook_id="nb1", title="Nueva", content="x")),
        ),
        artifacts=_Ns(
            list=_async([art]),
            generate_report=_async(GenerationStatus(task_id="t1", status="pending")),
            poll_status=_async(GenerationStatus(task_id="t1", status="completed")),
        ),
    )
    b._client = stub
    return b


async def test_notebooks(backend):
    (n,) = await backend.list_notebooks()
    assert (n.id, n.title, n.sources_count, n.emoji) == ("nb1", "Mi notebook", 2, "📘")
    assert n.created_at.startswith("2026-10-01")
    ov = await backend.get_overview("nb1")
    assert ov.summary == "Resumen" and ov.notebook.id == "nb1"


async def test_sources_normalized(backend):
    (s,) = await backend.list_sources("nb1")
    assert s.status == "ready"  # SourceStatus.READY (IntEnum) -> "ready"
    assert s.type in {"unknown", "web_page"} and s.word_count == 120 and s.url == "https://x.test"
    c = await backend.get_source_markdown("nb1", "s1")
    assert c.content == "# H\ntexto" and c.char_count == 8
    g = await backend.get_source_guide("nb1", "s1")
    assert g.keywords == ["k1", "k2"]
    (h,) = await backend.search("nb1", "q", ["s1"], 5)
    assert (h.source_id, h.text, h.rank) == ("s1", "frag", 1)


async def test_chat_notes_artifacts(backend):
    a = await backend.ask("nb1", "¿?", None, None)
    assert a.answer == "Resp" and a.conversation_id == "c1" and a.citations[0].cited_text == "cita"
    assert (await backend.list_notes("nb1"))[0].title == "Nota"
    assert (await backend.create_note("nb1", "Nueva", "x")).id == "n2"
    art = (await backend.list_artifacts("nb1"))[0]
    assert art.type == "report" and art.id == "a1"
    g = await backend.generate_report("nb1", "briefing_doc", None, "es", None)
    assert g.task_id == "t1" and g.status == "pending"
    assert (await backend.generation_status("nb1", "t1")).status == "completed"


async def test_report_format_names_exist_in_library():
    from notebooklm import ReportFormat

    from nlm_mcp.backend import REPORT_FORMATS

    for fmt in REPORT_FORMATS:
        assert ReportFormat[fmt.upper()]


async def test_retry_repeats_transient_read_errors(tmp_path, monkeypatch):
    """NetworkError/ServerError/RateLimitError se reintentan con backoff en lecturas."""
    from notebooklm import exceptions as nx

    import nlm_mcp.backend as be

    backend = NotebookLMBackend(make_settings(tmp_path, NLM_RETRIES="2"))
    calls = {"n": 0}

    class _Flaky:
        @property
        def notebooks(self):  # stub mínimo
            return self

        async def list(self):
            calls["n"] += 1
            if calls["n"] < 3:
                raise nx.NetworkError("transitorio")
            return []

    async def fast_sleep(_seconds: float) -> None:
        return None  # acelerar el backoff en la prueba

    monkeypatch.setattr(be, "_backoff_sleep", fast_sleep)
    backend._client = _Flaky()
    result = await backend.list_notebooks()
    assert result == [] and calls["n"] == 3


async def test_retry_does_not_apply_to_permanent_errors(tmp_path):
    from notebooklm import exceptions as nx

    backend = NotebookLMBackend(make_settings(tmp_path, NLM_RETRIES="2"))
    calls = {"n": 0}

    class _Broken:
        @property
        def notebooks(self):
            return self

        async def list(self):
            calls["n"] += 1
            raise nx.NotFoundError("no existe")

    backend._client = _Broken()
    with pytest.raises(nx.NotFoundError):
        await backend.list_notebooks()
    assert calls["n"] == 1  # NotFoundError no es transitorio: se propaga de inmediato


def test_label_handles_enums_and_none():
    assert _label(SourceStatus.PROCESSING) == "processing"
    assert _label(None) == "unknown"
    assert _label("x") == "x"


async def test_lazy_session_failure_is_a_clean_error(tmp_path):
    """Sin sesión de Google el servidor arranca y el fallo llega como mensaje accionable."""
    from mcp import Client

    from nlm_mcp.server import build_server

    settings = make_settings(tmp_path, NLM_STORAGE_PATH=str(tmp_path / "no-existe.json"))
    async with Client(build_server(settings)) as c:
        r = await c.call_tool("list_notebooks", {})
        assert r.is_error
        text = r.content[0].text
        print("MENSAJE SIN SESIÓN:", text)
        assert "notebooklm login" in text
        assert "Traceback" not in text and "no-existe.json" not in text
        h = await c.call_tool("health_check", {})
        assert h.structured_content["auth"] == "error"
