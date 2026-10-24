import json
from pathlib import Path

import pytest
from conftest import GUIDE_MD, NB, S_FAQ, S_GUIDE, S_PENDING, FakeBackend
from mcp import Client
from notebooklm import exceptions as nx

from nlm_mcp.__main__ import main
from nlm_mcp.cache import SourceCache
from nlm_mcp.textdensity import density_warning


async def call(client, name, args=None):
    r = await client.call_tool(name, args or {})
    if r.is_error:
        return True, r.content[0].text
    return False, r.structured_content if r.structured_content is not None else json.loads(r.content[0].text)


async def tool_names(server):
    async with Client(server) as c:
        return {t.name for t in (await c.list_tools()).tools}


# ── gating de seguridad ──
async def test_readonly_registers_no_write_tools(make_server):
    names = await tool_names(make_server()[0])
    assert {"read_source", "keyword_search", "grep", "ask_notebook", "export_markdown"} <= names
    for forbidden in (
        "create_notebook",
        "add_source_url",
        "add_source_text",
        "add_source_file",
        "add_source_base64",
        "rename_notebook",
        "delete_source",
        "delete_notebook",
        "delete_note",
        "delete_artifact",
    ):
        assert forbidden not in names
    # las nuevas herramientas de lectura y estado local sí existen en readonly
    for expected in (
        "get_generation_status",
        "get_usage",
        "get_chat_history",
        "export_markdown",
        "sync_notebook",
        "clear_cache",
        "source_stats",
    ):
        assert expected in names
    # el catálogo consolidado no debe volver a crecer por sorpresa (grep y keyword_search
    # absorben sus variantes "entre notebooks"; export, generación y estado local sus modos).
    assert len(names) == 23


async def test_readwrite_without_destructive(make_server):
    names = await tool_names(make_server(NLM_MCP_MODE="readwrite")[0])
    assert {"create_notebook", "add_source_url", "generate_report", "update_note", "rename_notebook"} <= names
    # la subida de bytes en memoria no depende del modo destructivo ni pierde el gate de carpetas
    assert "add_source_base64" in names
    assert "delete_source" not in names and "delete_notebook" not in names and "delete_note" not in names


async def test_destructive_requires_flag_and_readwrite(make_server):
    assert "delete_source" not in await tool_names(make_server(NLM_MCP_ALLOW_DESTRUCTIVE="1")[0])
    assert "delete_source" in await tool_names(
        make_server(NLM_MCP_MODE="readwrite", NLM_MCP_ALLOW_DESTRUCTIVE="1")[0]
    )
    assert "delete_note" in await tool_names(
        make_server(NLM_MCP_MODE="readwrite", NLM_MCP_ALLOW_DESTRUCTIVE="1")[0]
    )


async def test_annotations_flag_destructive_tools(make_server):
    server, _ = make_server(NLM_MCP_MODE="readwrite", NLM_MCP_ALLOW_DESTRUCTIVE="1")
    async with Client(server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert tools["list_notebooks"].annotations.read_only_hint is True
    assert tools["delete_source"].annotations.destructive_hint is True
    assert tools["create_notebook"].annotations.read_only_hint is False


# ── contrato de entrada: argumentos desconocidos ──
async def test_unknown_argument_fails_instead_of_being_ignored(make_server):
    """Un nombre mal escrito debe fallar nombrándolo, no ejecutarse con los valores por defecto:
    Pydantic los descartaba en silencio y un typo cambiaba el resultado sin avisar."""
    async with Client(make_server()[0]) as c:
        err, msg = await call(c, "grep", {"pattern": "certificado", "notebook_id": NB, "context": 1})
        assert err and "context" in msg
        # el argumento correcto sigue funcionando igual
        err, data = await call(c, "grep", {"pattern": "certificado", "notebook_id": NB, "context_lines": 1})
        assert not err and data["count"] >= 1


async def test_published_schemas_forbid_unknown_arguments(make_server):
    """La restricción debe verse en el esquema: el agente no debería llegar a equivocarse."""
    async with Client(make_server()[0]) as c:
        tools = (await c.list_tools()).tools
    permisivos = [t.name for t in tools if t.input_schema.get("additionalProperties") is not False]
    assert tools and not permisivos, f"esquemas que aceptan campos libres: {permisivos}"


async def test_health_check_reports_strict_arguments(make_server):
    async with Client(make_server()[0]) as c:
        _, info = await call(c, "health_check")
    assert info["strict_arguments"] is True


# ── lectura ──
async def test_list_and_filter(make_server):
    async with Client(make_server()[0]) as c:
        _, data = await call(c, "list_notebooks", {"query": "factura"})
        assert data["total"] == 1 and data["notebooks"][0]["id"] == NB
        _, data = await call(c, "list_sources", {"notebook_id": NB, "status": "ready"})
        assert data["count"] == 2
        _, data = await call(c, "list_sources", {"notebook_id": NB, "source_type": "pdf"})
        assert [s["id"] for s in data["sources"]] == [S_PENDING]
        _, data = await call(c, "get_notebook", {"notebook_id": NB})
        assert data["summary"] == "Resumen del notebook"


async def test_read_source_paginates_and_caches(make_server, backend):
    backend.content[S_GUIDE] = "".join(f"párrafo número {i}\n\n" for i in range(400))
    async with Client(make_server()[0]) as c:
        got, off = "", 0
        while off is not None:
            err, page = await call(c, "read_source", {"notebook_id": NB, "source_id": S_GUIDE, "offset": off})
            assert not err and len(page["content"]) <= 1000 and page["untrusted_content"] is True
            got, off = got + page["content"], page["next_offset"]
        assert got == backend.content[S_GUIDE]
        assert backend.fulltext_calls == 1  # las páginas siguientes salen de la caché
        await call(c, "read_source", {"notebook_id": NB, "source_id": S_GUIDE, "refresh": True})
        assert backend.fulltext_calls == 2


async def test_outline_and_section(make_server):
    async with Client(make_server()[0]) as c:
        _, o = await call(c, "get_source_outline", {"notebook_id": NB, "source_id": S_GUIDE})
        assert [h["title"] for h in o["headings"]][1] == "Instalación"
        _, s = await call(
            c, "read_source_section", {"notebook_id": NB, "source_id": S_GUIDE, "heading": "Emisión"}
        )
        assert "Notas de crédito" in s["content"] and "Texto final" not in s["content"]
        err, msg = await call(
            c, "read_source_section", {"notebook_id": NB, "source_id": S_GUIDE, "heading": "nada"}
        )
        assert err and "No hay ningún encabezado" in msg


async def test_search_and_ask_resolve_titles(make_server):
    async with Client(make_server()[0]) as c:
        _, r = await call(c, "search_sources", {"notebook_id": NB, "query": "certificado"})
        assert r["results"][0]["source_title"] == "Guía de facturación"
        _, a = await call(c, "ask_notebook", {"notebook_id": NB, "question": "¿Cómo emito?"})
        assert a["citations"][0]["source_title"] == "Guía de facturación" and a["conversation_id"] == "conv-1"
        _, a2 = await call(
            c, "ask_notebook", {"notebook_id": NB, "question": "¿y luego?", "conversation_id": "conv-9"}
        )
        assert a2["conversation_id"] == "conv-9"


async def test_ask_notebook_separa_la_oferta_de_studio_del_answer(make_server, backend):
    """La cola conversacional de NotebookLM no es la respuesta: va a `suggestions` y `answer`
    queda como la síntesis parseable (sin borrar nada)."""
    from nlm_mcp.models import Answer

    cuerpo = "**P1.** b) Diagnosticar y prevenir."
    cola = "💡 ¿Te gustaría que genere un juego de tarjetas de estudio en el panel de Studio?"

    async def ask_con_cola(nb, question, source_ids, conversation_id):
        return Answer(f"{cuerpo}\n\n{cola}", "conv-1", 1, [])

    backend.ask = ask_con_cola
    async with Client(make_server()[0]) as c:
        _, a = await call(c, "ask_notebook", {"notebook_id": NB, "question": "¿Cuál es el objetivo?"})
    assert a["answer"] == cuerpo
    assert a["suggestions"] == cola
    # sin cola: la clave no existe y el cuerpo no se toca

    async def ask_limpio(nb, question, source_ids, conversation_id):
        return Answer("Respuesta sin oferta.", "conv-1", 1, [])

    backend.ask = ask_limpio
    async with Client(make_server()[0]) as c:
        _, b = await call(c, "ask_notebook", {"notebook_id": NB, "question": "Otra"})
    assert b["answer"] == "Respuesta sin oferta." and "suggestions" not in b


async def test_keyword_search_auto_syncs_only_ready(make_server, backend):
    async with Client(make_server()[0]) as c:
        _, r = await call(c, "keyword_search", {"notebook_id": NB, "query": "anulo boleta baja"})
        assert r["sources_ready"] == 2 and r["downloaded_now"] == 2
        assert r["results"][0]["source_id"] == S_FAQ
        _, r2 = await call(c, "keyword_search", {"notebook_id": NB, "query": "certificado"})
        assert r2["downloaded_now"] == 0 and backend.fulltext_calls == 2  # sin nuevas descargas
        _, r3 = await call(
            c, "keyword_search", {"notebook_id": NB, "query": "certificado", "source_ids": [S_FAQ]}
        )
        assert all(h["source_id"] == S_FAQ for h in r3["results"])


async def test_keyword_search_without_autosync_uses_only_cache(make_server):
    async with Client(make_server()[0]) as c:
        _, r = await call(
            c, "keyword_search", {"notebook_id": NB, "query": "certificado", "auto_sync": False}
        )
        assert r["sources_searched"] == 0 and r["results"] == []


async def test_grep_literal_vs_regex(make_server):
    async with Client(make_server()[0]) as c:
        _, lit = await call(c, "grep", {"notebook_id": NB, "pattern": "factura.*SUNAT"})
        assert lit["count"] == 0  # literal: el .* no es comodín
        _, rx = await call(c, "grep", {"notebook_id": NB, "pattern": "factura.*SUNAT", "literal": False})
        assert rx["count"] == 1 and rx["matches"][0]["source_id"] == S_GUIDE
        err, msg = await call(c, "grep", {"notebook_id": NB, "pattern": "(", "literal": False})
        assert err and "regular inválida" in msg


async def test_sync_reports_and_removes_deleted(make_server, backend):
    async with Client(make_server()[0]) as c:
        _, r = await call(c, "sync_notebook", {"notebook_id": NB})
        assert r["downloaded"] == 2 and r["not_ready_skipped"] == 1
        _, r = await call(c, "sync_notebook", {"notebook_id": NB})
        assert r["from_cache"] == 2 and r["downloaded"] == 0
        backend.sources = [s for s in backend.sources if s.id != S_FAQ]
        _, r = await call(c, "sync_notebook", {"notebook_id": NB})
        assert r["removed_from_cache"] == 1


async def test_sync_isolates_per_source_errors(make_server, backend):
    async def boom(nb, sid):
        if sid == S_FAQ:
            raise RuntimeError("secreto interno")
        return await FakeBackend.get_source_markdown(backend, nb, sid)

    backend.get_source_markdown = boom
    async with Client(make_server()[0]) as c:
        _, r = await call(c, "sync_notebook", {"notebook_id": NB})
    assert r["downloaded"] == 1 and len(r["errors"]) == 1
    assert "secreto interno" not in json.dumps(r)  # no se filtra el detalle


async def test_export_markdown_files(make_server, tmp_path):
    server, settings = make_server()
    async with Client(server) as c:
        _, r = await call(c, "export_markdown", {"notebook_id": NB})
        assert r["mode"] == "notebook" and r["files_count"] == 2 and r["written"] == 2
        out = tmp_path / "cache" / "exports" / NB
        files = sorted(p.name for p in out.glob("*.md"))
        assert "INDEX.md" in files and any(f.startswith("guia-de-facturacion__src-guid") for f in files)
        guide = next(p for p in out.glob("guia-*.md")).read_text(encoding="utf-8")
        assert guide.startswith('---\ntitle: "Guía de facturación"') and "# Guía de facturación" in guide
        _, cl = await call(c, "clear_cache", {"notebook_id": NB})
        assert cl["cleared"] and not out.exists()


async def test_notes_artifacts_report(make_server):
    async with Client(make_server()[0]) as c:
        _, n = await call(c, "list_notes", {"notebook_id": NB})
        assert n["notes"][0]["preview"] == "contenido de la nota"
        _, n = await call(c, "read_note", {"notebook_id": NB, "note_id": "note-1"})
        assert n["content"] == "contenido de la nota"
        _, a = await call(c, "list_artifacts", {"notebook_id": NB})
        assert a["artifacts"][0]["type"] == "report"
        _, rp = await call(c, "read_report", {"notebook_id": NB, "artifact_id": "art-1"})
        assert rp["content"].startswith("# Informe")
        _, g = await call(c, "get_generation_status", {"notebook_id": NB, "task_id": "task-1"})
        assert g["status"] == "completed"


async def test_read_report_explains_non_report_artifact(make_server):
    """Un artefacto que no es un informe debe decirlo con su tipo: NotebookLM responde «no está
    listo» también para un archivo terminado, y ese mensaje hacía dudar de la herramienta."""
    async with Client(make_server()[0]) as c:
        err, msg = await call(c, "read_report", {"notebook_id": NB, "artifact_id": "art-file"})
        assert err and "«file»" in msg and "export_markdown" in msg
        err, msg = await call(c, "read_report", {"notebook_id": NB, "artifact_id": "art-pending"})
        assert err and "get_generation_status" in msg
        err, data = await call(c, "read_report", {"notebook_id": NB, "artifact_id": "art-1"})
        assert not err and data["artifact_id"] == "art-1"


async def test_read_report_does_not_mask_other_errors(make_server, backend):
    """Solo se traduce el error de artefacto: un fallo de red debe seguir diciendo que es de red."""
    backend.report_error = nx.NetworkError("sin red")
    async with Client(make_server()[0]) as c:
        err, msg = await call(c, "read_report", {"notebook_id": NB, "artifact_id": "art-file"})
    assert err and "Error de red" in msg and "no es un informe" not in msg


# ── robustez ──
@pytest.mark.parametrize(
    "args",
    [
        {"notebook_id": "../../etc", "source_id": S_GUIDE},
        {"notebook_id": NB, "source_id": "a/b"},
        {"notebook_id": NB, "source_id": ""},
    ],
)
async def test_ids_with_path_traversal_rejected(make_server, args):
    async with Client(make_server()[0]) as c:
        err, msg = await call(c, "read_source", args)
    assert err and "inválido" in msg


async def test_backend_errors_become_actionable_messages(make_server, backend):
    from notebooklm import exceptions as nx

    async def expired():
        raise nx.AuthError("cookie=SECRET123")

    backend.list_notebooks = expired
    async with Client(make_server()[0]) as c:
        err, msg = await call(c, "list_notebooks")
        assert err and "notebooklm login" in msg and "SECRET123" not in msg
        _, h = await call(c, "health_check")
        assert h["auth"] == "error" and "notebooklm login" in h["detail"]


async def test_unexpected_errors_do_not_leak(make_server, backend):
    async def boom(nb):
        raise ValueError("token=abc123 path=/home/x")

    backend.list_sources = boom
    async with Client(make_server()[0]) as c:
        err, msg = await call(c, "list_sources", {"notebook_id": NB})
    assert err and "abc123" not in msg and "/home/x" not in msg


async def test_timeout_is_reported(make_server, backend):
    import asyncio

    async def slow(nb):
        await asyncio.sleep(5)

    backend.list_sources = slow
    server, settings = make_server(NLM_TIMEOUT_SECONDS="1")
    async with Client(server) as c:
        err, msg = await call(c, "list_sources", {"notebook_id": NB})
    assert err and "Tiempo de espera" in msg


async def test_health_ok(make_server):
    async with Client(make_server()[0]) as c:
        _, h = await call(c, "health_check")
    assert h["auth"] == "ok" and h["mode"] == "readonly" and h["file_upload_enabled"] is False


# ── escritura ──
async def test_write_tools_and_audit_never_log_content(make_server, backend, tmp_path):
    server, settings = make_server(NLM_MCP_MODE="readwrite")
    secret = "CONTENIDO-CONFIDENCIAL " * 5
    async with Client(server) as c:
        _, nb = await call(c, "create_notebook", {"title": "Nuevo"})
        assert nb["id"] == "nb-new"
        _, s = await call(c, "add_source_text", {"notebook_id": NB, "title": "T", "content": secret})
        assert s["id"] == "src-new"
        _, u = await call(c, "add_source_url", {"notebook_id": NB, "url": "https://example.com/x"})
        assert u["type"] == "web_page"
        _, n = await call(c, "create_note", {"notebook_id": NB, "title": "N", "content": "c"})
        await call(c, "update_note", {"notebook_id": NB, "note_id": n["id"], "title": "N2", "content": "c2"})
        await call(c, "rename_source", {"notebook_id": NB, "source_id": S_GUIDE, "title": "Nuevo título"})
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "CONFIDENCIAL" not in audit and "sha256" in audit
    assert len(audit.strip().splitlines()) == 6
    assert ("add_text", "T") in backend.calls


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "javascript:alert(1)", "ftp://x.com/a", "https://", "example.com"]
)
async def test_add_url_rejects_non_http(make_server, backend, url):
    async with Client(make_server(NLM_MCP_MODE="readwrite")[0]) as c:
        err, msg = await call(c, "add_source_url", {"notebook_id": NB, "url": url})
    assert err and not any(x[0] == "add_url" for x in backend.calls)


async def test_add_file_allowlist(make_server, backend, tmp_path):
    ok_dir = tmp_path / "docs"
    ok_dir.mkdir()
    good, bad = ok_dir / "a.pdf", tmp_path / "otro.pdf"
    good.write_text("x"), bad.write_text("x")
    async with Client(make_server(NLM_MCP_MODE="readwrite")[0]) as c:  # sin NLM_UPLOAD_DIRS
        err, msg = await call(c, "add_source_file", {"notebook_id": NB, "path": str(good)})
        assert err and "deshabilitada" in msg
    async with Client(make_server(NLM_MCP_MODE="readwrite", NLM_UPLOAD_DIRS=str(ok_dir))[0]) as c:
        err, _ = await call(c, "add_source_file", {"notebook_id": NB, "path": str(good)})
        assert not err
        err, msg = await call(c, "add_source_file", {"notebook_id": NB, "path": str(bad)})
        assert err and "fuera" in msg
    assert [x for x in backend.calls if x[0] == "add_file"] == [("add_file", str(good.resolve()))]


async def test_add_source_base64_sin_pasarlo_por_el_contexto(make_server, backend, tmp_path):
    """El flujo real (adjunto de Gmail -> NotebookLM) obligaba a mover 673 KB de base64
    por el contexto del agente. La tool escribe un temporal en el allowlist, sube y limpia."""
    ok_dir = tmp_path / "docs"
    ok_dir.mkdir()
    server, _ = make_server(NLM_MCP_MODE="readwrite", NLM_UPLOAD_DIRS=str(ok_dir))
    async with Client(server) as c:
        err, r = await call(
            c,
            "add_source_base64",
            {"notebook_id": NB, "filename": "informe.pdf", "content_base64": "JVBERi0xLjc="},
        )
        assert not err and r["id"] == "src-new"
        # el nombre pedido manda sobre el de la carpeta efímera (así sobrevive la referencia)
        subidas = [Path(p).name for kind, p in backend.calls if kind == "add_file"]
        assert subidas == ["informe.pdf"]
        # y no queda nada tras la subida
        assert list(ok_dir.iterdir()) == []
        # base64 inválido no llega al backend
        err, msg = await call(
            c, "add_source_base64", {"notebook_id": NB, "filename": "x.pdf", "content_base64": "@@@"}
        )
        assert err and len([x for x in backend.calls if x[0] == "add_file"]) == len(subidas)
    # la auditoría registra el evento, nunca el contenido de los bytes
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "add_source_base64" in audit and "JVBERi0xLjc=" not in audit


async def test_add_source_base64_exige_subida_habilitada(make_server, backend, tmp_path):
    server, _ = make_server(NLM_MCP_MODE="readwrite")  # sin NLM_UPLOAD_DIRS
    async with Client(server) as c:
        err, msg = await call(
            c,
            "add_source_base64",
            {"notebook_id": NB, "filename": "a.pdf", "content_base64": "JVBERi0xLjc="},
        )
    assert err and "deshabilitada" in msg


async def test_add_source_file_avisa_de_poca_densidad_de_texto(make_server, backend, tmp_path):
    """Un deck de 505 KB con 355 palabras deja huecos de cobertura: el aviso evita que
    el agente dé por cubierto un material que casi todo está en imágenes."""
    from nlm_mcp.models import SourceInfo

    async def add_file_con_pocas_palabras(nb, path, wait):
        backend.calls.append(("add_file", str(path)))
        return SourceInfo("src-new", path.name, "pdf", "ready", word_count=355)

    backend.add_file = add_file_con_pocas_palabras
    ok_dir = tmp_path / "docs"
    ok_dir.mkdir()
    (ok_dir / "deck.pdf").write_bytes(b"0" * 505_000)
    server, _ = make_server(NLM_MCP_MODE="readwrite", NLM_UPLOAD_DIRS=str(ok_dir))
    async with Client(server) as c:
        err, r = await call(c, "add_source_file", {"notebook_id": NB, "path": str(ok_dir / "deck.pdf")})
    assert not err
    assert "355 palabras" in r["text_density_warning"]

    # sin recuento reportado no se inventa un aviso
    assert density_warning(505_000, None) is None


async def test_health_check_explica_como_habilitar_la_subida(make_server):
    server, _ = make_server(NLM_MCP_MODE="readwrite")
    async with Client(server) as c:
        _, h = await call(c, "health_check")
    assert h["file_upload_enabled"] is False
    assert "NLM_UPLOAD_DIRS" in h["file_upload_hint"]


async def test_generate_report_validation(make_server, backend):
    async with Client(make_server(NLM_MCP_MODE="readwrite")[0]) as c:
        assert (await call(c, "generate_report", {"notebook_id": NB, "report_format": "podcast"}))[0]
        assert (await call(c, "generate_report", {"notebook_id": NB, "report_format": "custom"}))[0]
        err, r = await call(c, "generate_report", {"notebook_id": NB})
        assert not err and r["task_id"] == "task-1"
    assert backend.calls[-1][:3] == ("generate_report", "briefing_doc", "es")


# ── destructivo ──
async def test_delete_requires_exact_title(make_server, backend):
    server, _ = make_server(NLM_MCP_MODE="readwrite", NLM_MCP_ALLOW_DESTRUCTIVE="1")
    async with Client(server) as c:
        err, msg = await call(
            c, "delete_source", {"notebook_id": NB, "source_id": S_GUIDE, "confirm_title": "otra cosa"}
        )
        assert err and "no coincide" in msg and "Guía de facturación" not in msg  # no revela el título
        assert backend.deleted == []
        err, _ = await call(
            c, "delete_source", {"notebook_id": NB, "source_id": "no-existe", "confirm_title": "x"}
        )
        assert err and backend.deleted == []
        err, r = await call(
            c,
            "delete_source",
            {"notebook_id": NB, "source_id": S_GUIDE, "confirm_title": "Guía de facturación"},
        )
        assert not err and backend.deleted == [S_GUIDE]
        err, _ = await call(c, "delete_notebook", {"notebook_id": NB, "confirm_title": "x"})
        assert err and backend.deleted == [S_GUIDE]
        err, _ = await call(c, "delete_notebook", {"notebook_id": NB, "confirm_title": "Facturación SUNAT"})
        assert not err and backend.deleted == [S_GUIDE, NB]


async def test_delete_note_exact_title(make_server):
    server, _ = make_server(NLM_MCP_MODE="readwrite", NLM_MCP_ALLOW_DESTRUCTIVE="1")
    async with Client(server) as c:
        err, msg = await call(
            c, "delete_note", {"notebook_id": NB, "note_id": "note-1", "confirm_title": "otro"}
        )
        assert err and "no coincide" in msg
        err, data = await call(
            c, "delete_note", {"notebook_id": NB, "note_id": "note-1", "confirm_title": "Mi nota"}
        )
        assert not err and data["deleted"] is True


async def test_delete_artifact_exact_title(make_server):
    server, _ = make_server(NLM_MCP_MODE="readwrite", NLM_MCP_ALLOW_DESTRUCTIVE="1")
    async with Client(server) as c:
        err, msg = await call(
            c, "delete_artifact", {"notebook_id": NB, "artifact_id": "art-1", "confirm_title": "no"}
        )
        assert err and "no coincide" in msg
        err, data = await call(
            c, "delete_artifact", {"notebook_id": NB, "artifact_id": "art-1", "confirm_title": "Informe"}
        )
        assert not err and data["deleted"] is True


async def test_export_single_source_writes_front_matter(make_server, backend, tmp_path):
    async with Client(make_server()[0]) as c:
        err, data = await call(c, "export_markdown", {"notebook_id": NB, "source_id": S_GUIDE})
        assert not err and data["mode"] == "source" and data["file"].endswith(".md")
        assert data["chars"] == len(GUIDE_MD)
        path = tmp_path / "cache" / "exports" / NB / data["file"]
        content = path.read_text(encoding="utf-8")
        assert content.startswith("---\ntitle:") and "source_id:" in content and GUIDE_MD in content
        err, msg = await call(c, "export_markdown", {"notebook_id": NB, "source_id": "no-existe"})
        assert err and "no-existe" not in msg.lower()  # el error no refleja la entrada inválida
        # refresh vuelve a descargar la fuente desde NotebookLM
        antes = backend.fulltext_calls
        err, _ = await call(c, "export_markdown", {"notebook_id": NB, "source_id": S_GUIDE, "refresh": True})
        assert not err and backend.fulltext_calls == antes + 1


async def test_export_markdown_directorio_elegido(make_server, tmp_path):
    async with Client(make_server()[0]) as c:
        # relativa: cuelga de la carpeta de exportaciones del servidor
        err, rel = await call(c, "export_markdown", {"notebook_id": NB, "directory": "para-agente"})
        assert not err and rel["mode"] == "notebook"
        destino = tmp_path / "cache" / "exports" / "para-agente"
        assert rel["directory"] == str(destino) and (destino / "INDEX.md").is_file()
        # absoluta: donde pida el usuario
        afuera = tmp_path / "afuera"
        err, abs_ = await call(c, "export_markdown", {"notebook_id": NB, "directory": str(afuera)})
        assert not err and abs_["directory"] == str(afuera) and len(list(afuera.glob("*.md"))) == 3
        # una ruta que es un archivo no se acepta como carpeta
        archivo = tmp_path / "no-soy-carpeta.txt"
        archivo.write_text("x", encoding="utf-8")
        err, msg = await call(c, "export_markdown", {"notebook_id": NB, "directory": str(archivo)})
        assert err and "carpeta" in msg


async def test_rename_notebook_updates_and_audits(make_server):
    server, _ = make_server(NLM_MCP_MODE="readwrite")
    async with Client(server) as c:
        err, data = await call(c, "rename_notebook", {"notebook_id": NB, "title": "Nuevo título"})
        assert not err and data["title"] == "Nuevo título"
        err, _ = await call(c, "rename_notebook", {"notebook_id": NB, "title": "   "})
        assert err  # título vacío rechazado


# ── nuevas herramientas de lectura/estado ──
async def test_usage_shape_and_untrusted(make_server):
    async with Client(make_server()[0]) as c:
        err, data = await call(c, "get_usage")
        assert not err and data["status"] == "ready" and data["enabled"] is True
        assert data["windows"][0]["kind"] == "chat" and data["windows"][0]["used_percent"] == 12.5


async def test_chat_history_trimmed_and_marked(make_server):
    async with Client(make_server()[0]) as c:
        err, data = await call(c, "get_chat_history", {"notebook_id": NB, "limit": 10})
        assert not err and data["count"] == 1
        t = data["turns"][0]
        assert "anulo una boleta" in t["question"] and data["untrusted_content"] is True
        # límites: clamp reduce limit=0 a 1 -> la lib falsa recorta a 1 turno
        err, data = await call(c, "get_chat_history", {"notebook_id": NB, "limit": 0})
        assert not err and data["count"] == 1


async def test_get_generation_status_consulta_y_espera(make_server):
    async with Client(make_server()[0]) as c:
        # sin wait_seconds: consulta única (compatible con el uso anterior)
        err, ya = await call(c, "get_generation_status", {"notebook_id": NB, "task_id": "t-1"})
        assert not err and ya["final"] is True and "completed" in ya["status"]
        assert ya["waited_seconds"] >= 0
        # con wait_seconds: espera activa hasta el estado terminal
        err, esperando = await call(
            c, "get_generation_status", {"notebook_id": NB, "task_id": "t-1", "wait_seconds": 5}
        )
        assert not err and esperando["final"] is True and esperando["status"] == "completed"


async def test_contratos_uniformes_de_listado_y_barrido(make_server):
    server, _ = make_server()
    async with Client(server) as c:
        # count = elementos devueltos; total = coincidencias antes del recorte (mismo contrato en
        # list_notebooks y list_sources, donde antes una usaba "returned" y la otra "count").
        _, nbs = await call(c, "list_notebooks", {"limit": 1})
        assert nbs["count"] == 1 and nbs["total"] == 2 and len(nbs["notebooks"]) == 1
        _, srcs = await call(c, "list_sources", {"notebook_id": NB, "status": "ready", "limit": 1})
        assert srcs["count"] == 1 and srcs["total"] == 2
        # el barrido cuenta también las fuentes que quedaron sin medir por no estar listas aún
        # (contadores agregados, como sources_searched/sources_ready; con una notebook, ids)
        err, barrido = await call(c, "keyword_search", {"query": "facturacion", "notebook_id": NB})
        assert not err and barrido["sources_ready"] == 2 and barrido["sources_not_ready"] == 1
        err, coincidencias = await call(c, "grep", {"pattern": "factura"})
        assert not err and coincidencias["sources_not_ready"] == 2  # una por notebook barrenada
        err, stats = await call(c, "source_stats", {"notebook_id": NB})
        assert not err and stats["mode"] == "notebook" and stats["not_ready"] == [S_PENDING]
        # el índice de encabezados es texto de terceros y se marca como no confiable, como el resto
        err, outline = await call(c, "get_source_outline", {"notebook_id": NB, "source_id": S_GUIDE})
        assert not err and outline["untrusted_content"] is True


async def test_source_stats_resume_la_cache_sin_notebook(make_server, backend):
    backend.content[S_GUIDE] = "a" * 40
    async with Client(make_server()[0]) as c:
        _, _ = await call(c, "read_source", {"notebook_id": NB, "source_id": S_GUIDE})
        err, data = await call(c, "source_stats")
        assert not err and data["mode"] == "cache" and data["untrusted_content"] is False
        entry = next((n for n in data["notebooks"] if n["notebook_id"] == NB), None)
        assert entry and entry["cached_sources"] >= 1 and entry["chars"] >= 40
        assert data["totals"]["sources"] == entry["cached_sources"]
        assert data["totals"]["chars"] == entry["chars"]
        # source_ids exige una notebook concreta (mismo contrato que grep/keyword_search)
        err, msg = await call(c, "source_stats", {"source_ids": [S_GUIDE]})
        assert err and "notebook_id" in msg


# ── recursos y prompts ──
async def test_resources_and_prompts(make_server):
    async with Client(make_server()[0]) as c:
        res = await c.list_resources()
        assert any(str(r.uri) == "notebooklm://notebooks" for r in res.resources)
        templates = await c.list_resource_templates()
        assert any("sources/{source_id}" in t.uri_template for t in templates.resource_templates)
        got = await c.read_resource(f"notebooklm://notebooks/{NB}/sources/{S_GUIDE}")
        assert "Guía de facturación" in got.contents[0].text
        prompts = {p.name for p in (await c.list_prompts()).prompts}
        assert {"research_topic", "summarize_source", "compare_sources"} <= prompts
        p = await c.get_prompt("research_topic", {"notebook_id": NB, "topic": "IGV"})
        assert "IGV" in p.messages[0].content.text


# ── CLI ──
def test_cli_rejects_invalid_config(monkeypatch, capsys):
    monkeypatch.setenv("NLM_MCP_MODE", "root")
    assert main(["--env-file", "/nonexistent"]) == 2
    assert "Configuración inválida" in capsys.readouterr().err


def test_cli_rejects_remote_bind_without_flag(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("NLM_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("NLM_MCP_MODE", raising=False)
    assert main(["--transport", "streamable-http", "--host", "0.0.0.0", "--env-file", "/nonexistent"]) == 2
    assert "loopback" in capsys.readouterr().err


async def test_sync_notebook_errors_incluyen_ref_y_no_fuga_rutas(make_server, backend, monkeypatch):
    async def boom(nb, sid):
        raise RuntimeError(f"se rompio al leer C:\\Users\\secretaria\\archivo ({sid})")

    monkeypatch.setattr(backend, "get_source_markdown", boom)
    async with Client(make_server()[0]) as c:
        err, data = await call(c, "sync_notebook", {"notebook_id": NB})
        assert not err
        errores = data["errors"]
        assert errores and all(e.get("ref") for e in errores)
        joined = " ".join(e["error"] for e in errores)
        assert "C:\\Users" not in joined
        assert "RuntimeError" in joined  # la clase sí viaja para diagnóstico inmediato
    # sin el monkeypatch el sync vuelve a funcionar (mismo servidor ya no usa boom)
    backend.get_source_markdown = backend.__class__.get_source_markdown.__get__(backend)
    async with Client(make_server()[0]) as c:
        err, data = await call(c, "sync_notebook", {"notebook_id": NB})
        assert not err and data["errors"] == [] and data["downloaded"] == 2


async def test_clear_cache_purge_all(make_server):
    server, _ = make_server()
    async with Client(server) as c:
        err, data = await call(c, "sync_notebook", {"notebook_id": NB})
        assert not err and data["downloaded"] == 2
        err, stats = await call(c, "source_stats", {})
        assert not err and stats["totals"]["sources"] == 2
        err, data = await call(c, "clear_cache", {"purge_all": True})
        assert not err and data["purge_all"] is True and data["cleared"] is True
        err, stats = await call(c, "source_stats", {})
        assert not err and stats["totals"]["sources"] == 0 and stats["totals"]["notebooks"] == 0
        # llamada sin argumentos: mensaje accionable
        err, msg = await call(c, "clear_cache", {})
        assert err and "purge_all" in msg


async def test_keyword_search_entre_notebooks_etiqueta_origen(make_server):
    server, _ = make_server()
    async with Client(server) as c:
        err, data = await call(c, "keyword_search", {"query": "facturacion", "limit": 5})
        assert not err
        assert data["notebooks_requested"] == 2 and data["notebooks_scanned"] == 2
        assert data["count"] >= 1
        assert {r["notebook_id"] for r in data["results"]} <= {NB, "nb-222"}
        assert all(r["source_id"] and r["source_title"] for r in data["results"])
        assert all(r["notebook_id"] and r["notebook_title"] for r in data["results"])
        assert data["sources_searched"] >= 2  # agregado de lo medido en cada notebook
        assert data["notebook_errors"] == []
        # limitar a una sola notebook
        err, solo = await call(c, "keyword_search", {"query": "facturacion", "notebook_ids": [NB]})
        assert not err and solo["notebooks_requested"] == 1 and solo["notebooks_scanned"] == 1
        assert {r["notebook_id"] for r in solo["results"]} == {NB}
        assert all(r["notebook_title"] is None for r in solo["results"])  # sin RPC extra de título
        # source_ids sin notebook_id: error accionable en vez de ignorarlo en silencio
        err, msg = await call(c, "keyword_search", {"query": "facturacion", "source_ids": [S_GUIDE]})
        assert err and "notebook_id" in msg


async def test_keyword_search_aisla_notebooks_rotas(make_server, backend, monkeypatch):
    original = backend.list_sources

    async def flaky(nb):
        if nb == "nb-222":
            raise RuntimeError("la notebook no responde")
        return await original(nb)

    monkeypatch.setattr(backend, "list_sources", flaky)
    async with Client(make_server()[0]) as c:
        err, data = await call(c, "keyword_search", {"query": "facturacion"})
        assert not err
        assert data["notebooks_scanned"] == 1 and data["notebooks_requested"] == 2
        assert data["count"] >= 1  # la otra notebook sí se barrió
        fallo = data["notebook_errors"][0]
        assert fallo["notebook_id"] == "nb-222" and fallo["ref"] and "RuntimeError" in fallo["error"]


async def test_grep_entre_notebooks_etiqueta_origen(make_server):
    async with Client(make_server()[0]) as c:
        err, data = await call(c, "grep", {"pattern": "SUNAT"})
        assert not err
        assert data["notebooks_requested"] == 2 and data["notebooks_scanned"] == 2
        assert data["count"] >= 1 and data["truncated"] is False
        assert {m["notebook_id"] for m in data["matches"]} <= {NB, "nb-222"}
        assert all(m["notebook_id"] and m["notebook_title"] for m in data["matches"])
        assert all(m["line"] >= 1 and m["source_title"] for m in data["matches"])
        assert data["sources_searched"] >= 2 and data["downloaded_now"] >= 2
        # con notebook_id se conserva el modo de una sola notebook
        err, solo = await call(c, "grep", {"notebook_id": NB, "pattern": "SUNAT"})
        assert not err and solo["notebooks_requested"] == 1 and solo["notebooks_scanned"] == 1
        assert {m["notebook_id"] for m in solo["matches"]} == {NB}
        assert all(m["notebook_title"] is None for m in solo["matches"])


async def test_grep_tope_global_marca_truncated(make_server):
    async with Client(make_server()[0]) as c:
        err, data = await call(c, "grep", {"pattern": "de", "max_matches": 1})
        assert not err and data["count"] == 1
        assert data["truncated"] is True and len(data["matches"]) == 1


async def test_grep_aisla_notebooks_rotas(make_server, backend, monkeypatch):
    original = backend.list_sources

    async def flaky(nb):
        if nb == "nb-222":
            raise RuntimeError("la notebook no responde")
        return await original(nb)

    monkeypatch.setattr(backend, "list_sources", flaky)
    async with Client(make_server()[0]) as c:
        err, data = await call(c, "grep", {"pattern": "SUNAT"})
        assert not err
        assert data["notebooks_scanned"] == 1 and data["notebooks_requested"] == 2
        assert data["count"] >= 1
        fallo = data["notebook_errors"][0]
        assert fallo["notebook_id"] == "nb-222" and fallo["ref"] and "RuntimeError" in fallo["error"]


async def test_barrido_informa_que_notebooks_recorrio(make_server, backend, monkeypatch):
    """Con `max_notebooks`, un contador que no cuadre es indescifrable: hace falta saber qué
    notebooks se recorrieron y cuáles fallaron (lo pedí yo mismo durante la verificación en vivo)."""
    async with Client(make_server()[0]) as c:
        err, data = await call(c, "grep", {"pattern": "SUNAT"})
        assert not err and data["notebooks_scanned_ids"] == [NB, "nb-222"]
        assert data["notebooks_scanned"] == len(data["notebooks_scanned_ids"]) == 2
        err, solo = await call(c, "grep", {"pattern": "SUNAT", "notebook_ids": [NB]})
        assert not err and solo["notebooks_scanned_ids"] == [NB]

    original = backend.list_sources

    async def flaky(nb):
        if nb == "nb-222":
            raise RuntimeError("la notebook no responde")
        return await original(nb)

    monkeypatch.setattr(backend, "list_sources", flaky)
    async with Client(make_server()[0]) as c:
        err, data = await call(c, "grep", {"pattern": "SUNAT"})
    assert not err and data["notebooks_scanned_ids"] == [NB]  # la rota no cuenta como escaneada
    assert [e["notebook_id"] for e in data["notebook_errors"]] == ["nb-222"]


async def test_source_stats_mide_sin_cuota(make_server):
    async with Client(make_server()[0]) as c:
        err, frio = await call(c, "source_stats", {"notebook_id": NB, "auto_sync": False})
        assert not err
        assert frio["downloaded_now"] == 0 and frio["totals"]["sources_measured"] == 0
        assert set(frio["not_cached"]) == {S_GUIDE, S_FAQ}
        err, data = await call(c, "source_stats", {"notebook_id": NB, "terms": 5})
        assert not err and data["downloaded_now"] == 2
        assert data["totals"]["sources_measured"] == 2
        assert data["totals"]["chars"] > 0 and data["totals"]["words"] > 0
        guia = next(r for r in data["sources"] if r["source_id"] == S_GUIDE)
        assert guia["headings"] >= 1 and guia["words"] > 0
        assert 1 <= len(guia["top_terms"]) <= 5
        assert all(t["term"] and t["count"] >= 1 for t in guia["top_terms"])


async def test_health_check_informa_antiguedad_de_sesion(make_server, tmp_path, monkeypatch):
    sesion = tmp_path / "storage_state.json"
    sesion.write_text("{}")
    async with Client(make_server(NLM_STORAGE_PATH=str(sesion))[0]) as c:
        err, data = await call(c, "health_check")
        assert not err
        assert data["session"]["age_days"] < 1 and data["session"]["stale"] is False
        assert data["session"]["note"] is None
    # sesión vieja: avisa sin tumbar el health_check
    import os as _os
    import time as _time

    viejo = _time.time() - 10 * 86400
    _os.utime(sesion, (viejo, viejo))
    async with Client(make_server(NLM_STORAGE_PATH=str(sesion))[0]) as c:
        err, data = await call(c, "health_check")
        assert not err and data["auth"] == "ok"
        assert data["session"]["age_days"] >= 9 and data["session"]["stale"] is True
        assert "notebooklm login" in data["session"]["note"]
    # sesión inexistente: se reporta, no se falla
    async with Client(make_server(NLM_STORAGE_PATH=str(tmp_path / "nope.json"))[0]) as c:
        err, data = await call(c, "health_check")
        assert not err and data["session"]["missing"] is True


async def test_export_markdown_incremental(make_server, backend):
    server, settings = make_server()
    async with Client(server) as c:
        err, first = await call(c, "export_markdown", {"notebook_id": NB})
        assert not err
        assert first["written"] == 2 and first["unchanged"] == 0 and first["removed"] == 0
        out = Path(first["directory"])
        assert (out / "INDEX.md").is_file()
        # segunda pasada sin cambios: no reescribe nada (conserva exported_at)
        antes = {f.name: f.stat().st_mtime_ns for f in out.glob("*.md") if f.name != "INDEX.md"}
        err, second = await call(c, "export_markdown", {"notebook_id": NB})
        assert not err
        assert second["written"] == 0 and second["unchanged"] == 2 and second["removed"] == 0
        assert {f.name: f.stat().st_mtime_ns for f in out.glob("*.md") if f.name != "INDEX.md"} == antes
        # cambia el contenido de una fuente (y su caché): solo esa se reescribe
        backend.content[S_FAQ] = backend.content[S_FAQ] + "\n\n## Novedad\nOtro párrafo.\n"
        SourceCache(settings.cache_dir, ttl=settings.cache_ttl).invalidate(NB, S_FAQ)
        err, third = await call(c, "export_markdown", {"notebook_id": NB})
        assert not err and third["written"] == 1 and third["unchanged"] == 1
        # force reescribe todo aunque el contenido no cambie
        err, cuarto = await call(c, "export_markdown", {"notebook_id": NB, "force": True})
        assert not err and cuarto["written"] == 2
        # una fuente que desaparece del notebook: su .md se elimina
        backend.sources = [s for s in backend.sources if s.id != S_FAQ]
        err, quinto = await call(c, "export_markdown", {"notebook_id": NB})
        assert not err and quinto["files_count"] == 1 and quinto["removed"] == 1
        assert not [f for f in out.glob("*.md") if S_FAQ[:8] in f.name]


async def test_export_solo_borra_lo_propio_y_conserva_lo_no_descargado(make_server, backend):
    server, settings = make_server()
    async with Client(server) as c:
        err, first = await call(c, "export_markdown", {"notebook_id": NB})
        assert not err and first["written"] == 2
        out = Path(first["directory"])
        ajeno = out / "notas-del-usuario.md"
        ajeno.write_text("notas propias, no una exportación", encoding="utf-8")
        # la fuente sigue en NotebookLM, pero su Markdown ya no se puede descargar
        SourceCache(settings.cache_dir, ttl=settings.cache_ttl).invalidate(NB, S_FAQ)
        del backend.content[S_FAQ]
        err, segundo = await call(c, "export_markdown", {"notebook_id": NB})
        assert not err
        assert ajeno.read_text(encoding="utf-8") == "notas propias, no una exportación"
        conservado = [f for f in out.glob("*.md") if S_FAQ[:8] in f.name]
        assert conservado, "la exportación previa de una fuente no descargable debe conservarse"
        assert segundo["removed"] == 0 and S_FAQ in segundo["not_written"]
        assert S_FAQ in segundo["sync"]["errors"][0]["source_id"]
        # cuando la fuente desaparece de verdad, entonces sí se borra su .md (y solo el suyo)
        backend.sources = [s for s in backend.sources if s.id != S_FAQ]
        err, tercero = await call(c, "export_markdown", {"notebook_id": NB})
        assert not err and tercero["removed"] == 1 and tercero["not_written"] == []
        assert not [f for f in out.glob("*.md") if S_FAQ[:8] in f.name]
        assert ajeno.is_file()
