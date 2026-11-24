import os
import re

import pytest
from conftest import GUIDE_MD

from nlm_mcp import bm25, mdtools
from nlm_mcp.cache import SourceCache
from nlm_mcp.config import Settings, load_dotenv
from nlm_mcp.models import SourceContent
from nlm_mcp.validators import UserError, resolve_upload_path, validate_id, validate_regex


# ── config ──
def test_config_defaults_are_safe(tmp_path):
    s = Settings.from_env({"NLM_CACHE_DIR": str(tmp_path)})
    assert s.mode == "readonly" and not s.writable and not s.destructive_enabled
    assert s.upload_dirs == () and s.default_language == "es"


def test_destructive_requires_readwrite(tmp_path):
    env = {"NLM_CACHE_DIR": str(tmp_path), "NLM_MCP_ALLOW_DESTRUCTIVE": "1"}
    assert not Settings.from_env(env).destructive_enabled
    assert Settings.from_env({**env, "NLM_MCP_MODE": "readwrite"}).destructive_enabled


@pytest.mark.parametrize(
    "env",
    [
        {"NLM_MCP_MODE": "root"},
        {"NLM_MAX_CHARS": "10"},
        {"NLM_CACHE_TTL_SECONDS": "x"},
        {"NLM_LOG_LEVEL": "LOUD"},
    ],
)
def test_config_rejects_invalid(env, tmp_path):
    with pytest.raises(ValueError):
        Settings.from_env({"NLM_CACHE_DIR": str(tmp_path), **env})


def test_dotenv_does_not_override(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# c\nA=1\nB='dos'\n")
    env = {"A": "keep"}
    assert load_dotenv(f, env) and env == {"A": "keep", "B": "dos"}
    assert not load_dotenv(tmp_path / "nope", {})


# ── validators ──
@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "a b", "", "x" * 200, "a\\b", "id\x00"])
def test_validate_id_rejects_traversal(bad):
    with pytest.raises(UserError):
        validate_id(bad)


def test_validate_id_accepts_uuid():
    assert validate_id("3f2a9c1e-77b4-4c0e-9d11-0a1b2c3d4e5f")


def test_regex_validation():
    with pytest.raises(UserError):
        validate_regex("(")
    assert validate_regex("abc").search("xABCx")


def test_conversation_id_validation():
    from nlm_mcp.validators import validate_conversation_id

    assert validate_conversation_id(" conv-9x_") == "conv-9x_"
    assert validate_conversation_id("id:con$#!") == "id:con$#!"  # formato interno de Google: permisivo
    with pytest.raises(UserError, match="vac"):
        validate_conversation_id("   ")
    with pytest.raises(UserError, match="inv"):
        validate_conversation_id("a\nb")  # caracteres de control


def test_settings_new_options(tmp_path):
    s = Settings.from_env(
        {
            "NLM_CACHE_DIR": str(tmp_path),
            "NLM_RETRIES": "3",
            "NLM_LOG_JSON": "1",
            "NLM_AUDIT_MAX_BYTES": "131072",
        }
    )
    assert s.retries == 3 and s.log_json is True and s.audit_max_bytes == 131072
    s2 = Settings.from_env({"NLM_CACHE_DIR": str(tmp_path), "NLM_RETRIES": "99"})
    assert s2.retries == 5  # se acota a 5


def test_upload_path_rules(tmp_path):
    allowed = tmp_path / "ok"
    allowed.mkdir()
    inside = allowed / "a.pdf"
    inside.write_text("x")
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    dirs = (allowed.resolve(),)
    assert resolve_upload_path(str(inside), dirs) == inside.resolve()
    with pytest.raises(UserError, match="fuera"):
        resolve_upload_path(str(outside), dirs)
    with pytest.raises(UserError, match="deshabilitada"):
        resolve_upload_path(str(inside), ())
    with pytest.raises(UserError):
        resolve_upload_path(str(allowed / "no-existe.pdf"), dirs)
    with pytest.raises(UserError, match="fuera"):  # traversal con ..
        resolve_upload_path(str(allowed / ".." / "secret.txt"), dirs)
    link = allowed / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks no disponibles")
    with pytest.raises(UserError, match="fuera"):  # symlink que escapa
        resolve_upload_path(str(link), dirs)


# ── markdown ──
def test_outline_ignores_code_fences():
    titles = [h.title for h in mdtools.outline(GUIDE_MD)]
    assert "esto NO es un encabezado" not in titles
    assert titles == [
        "Guía de facturación",
        "Instalación",
        "Emisión de comprobantes",
        "Notas de crédito",
        "Uso",
    ]


def test_section_extraction_respects_levels():
    text, head = mdtools.extract_section(GUIDE_MD, "emisión")
    assert head.level == 2 and "Notas de crédito" in text and "Texto final" not in text
    sub, _ = mdtools.extract_section(GUIDE_MD, "Notas de crédito")
    assert sub.startswith("### Notas") and "Uso" not in sub


def test_section_errors():
    with pytest.raises(UserError, match="No hay"):
        mdtools.extract_section(GUIDE_MD, "inexistente")
    ambiguous = "# A\n## Paso 1\nx\n## Paso 2\ny\n"
    with pytest.raises(UserError, match="ambiguo"):
        mdtools.extract_section(ambiguous, "paso")
    with pytest.raises(UserError, match="sin encabezados|no tiene"):
        mdtools.extract_section("solo texto", "x")


def test_paginate_walks_whole_text():
    text = "".join(f"línea {i}\n" for i in range(500))
    got, off, pages = "", 0, 0
    while off is not None:
        page = mdtools.paginate(text, off, 1000)
        assert len(page["content"]) <= 1000
        got += page["content"]
        off, pages = page["next_offset"], pages + 1
    assert got == text and pages > 1


def test_paginate_offset_clamped():
    page = mdtools.paginate("abc", 999, 10)
    assert page["content"] == "" and page["next_offset"] is None


def test_grep_context():
    hits = mdtools.grep("a\nb\nfoo\nc\nd", re.compile("foo"), context=1)
    assert hits[0]["line"] == 3 and hits[0]["before"] == ["b"] and hits[0]["after"] == ["c"]


def test_chunk_keeps_heading_and_covers_text():
    chunks = mdtools.chunk(GUIDE_MD, target=200)
    assert any(c.heading == "Instalación" for c in chunks)
    assert all(c.text for c in chunks)


# ── bm25 ──
def test_bm25_ignores_accents_and_ranks():
    idx = bm25.BM25Index(
        [("a", "Cert", "# Instalación\ncertificado digital"), ("b", "Arroz", "# Cocina\nreceta de arroz")]
    )
    hits = idx.search("instalacion certificado")
    assert hits and hits[0]["source_id"] == "a"
    assert idx.search("zzzz") == [] and idx.search("") == []


# ── cache ──
def test_cache_roundtrip_ttl_and_perms(tmp_path):
    cache = SourceCache(tmp_path, ttl=1000)
    item = SourceContent("s1", "T", "contenido", 9, "http://x")
    assert cache.get("nb", "s1") is None
    cache.put("nb", item)
    assert cache.get("nb", "s1").content == "contenido"
    assert cache.cached_ids("nb") == ["s1"]
    if os.name != "nt":
        assert oct((tmp_path / "notebooks" / "nb" / "s1.md").stat().st_mode & 0o777) == "0o600"
    cache.invalidate("nb", "s1")
    assert cache.get("nb", "s1") is None


def test_cache_ignores_orphan_temp_files(tmp_path):
    """Un .tmp-*.md de una escritura atómica interrumpida no debe aparecer como fuente."""
    cache = SourceCache(tmp_path, ttl=1000)
    cache.put("nb", SourceContent("s1", "T", "contenido", 9))
    (tmp_path / "notebooks" / "nb" / ".tmp-huérfano.md").write_text("resto")
    assert cache.cached_ids("nb") == ["s1"]


def test_cache_expiry_and_ttl_zero(tmp_path):
    cache = SourceCache(tmp_path, ttl=1)
    cache.put("nb", SourceContent("s1", "T", "c", 1))
    meta = tmp_path / "notebooks" / "nb" / "s1.meta.json"
    meta.write_text(meta.read_text().replace('"fetched_at": ', '"fetched_at": 1, "x": '))
    assert cache.get("nb", "s1") is None and cache.get("nb", "s1", allow_stale=True) is not None
    off = SourceCache(tmp_path, ttl=0)
    assert off.get("nb", "s1") is None


def test_cache_rejects_bad_ids(tmp_path):
    cache = SourceCache(tmp_path, ttl=10)
    with pytest.raises(UserError):
        cache.get("../..", "x")
    with pytest.raises(UserError):
        cache.put("nb", SourceContent("../evil", "T", "c", 1))


def test_strip_inline_images():
    base64 = "A" * 3000
    md = f"# Informe\n\n![image](data:image/jpeg;base64,{base64})\n\n### Planteamiento\nTexto."  # noqa: S105
    out = mdtools.strip_inline_images(md)
    assert "base64" not in out
    assert "_[imagen retirada]_" in out
    assert out.endswith("### Planteamiento\nTexto.")
    # data URI suelto (sin sintaxis de imagen markdown)
    out2 = mdtools.strip_inline_images("prev data:image/png;base64," + "A" * 200 + " posterior")
    assert out2 == "prev [imagen] posterior"
    # sin base64: no altera (retorno temprano)
    assert mdtools.strip_inline_images(GUIDE_MD) == GUIDE_MD
    # imágenes pequeñas (logs/URLs con base64 corto falso) quedan intactas
    intacto = "![x](data:image/gif;base64,AAAA) hola"
    assert mdtools.strip_inline_images(intacto) == "_[imagen retirada]_ hola"


def test_strip_inline_images_colapsa_rachas():
    """Un deck de diapositivas puede llevar decenas de imágenes en líneas consecutivas:
    una sola tira de marcadores en lugar de un marcador por imagen (ahorra contexto y ruido)."""
    b64 = "B" * 400
    tres = "\n\n".join(f"![s](data:image/png;base64,{b64})" for _ in range(3))
    out = mdtools.strip_inline_images(f"Intro.\n\n{tres}\n\nCierre.")
    assert out.count(mdtools.IMAGE_MARKER) == 1
    assert out.startswith("Intro.") and out.endswith("Cierre.")
    # una sola imagen conserva su forma exacta (no normaliza espacios alrededor)
    una = f"A\n\n![s](data:image/png;base64,{b64})\n\nB"
    assert mdtools.strip_inline_images(una) == f"A\n\n{mdtools.IMAGE_MARKER}\n\nB"


def test_densidad_de_texto_detecta_pdf_escaneado():
    """505 KB con 355 palabras es un deck de imágenes: el agente debe saberlo, no
    descubrirlo cuando una pregunta queda sin cobertura."""
    from nlm_mcp.textdensity import density_warning

    assert density_warning(505_000, 355) is not None
    assert density_warning(200_000, 3000) is None
    assert density_warning(0, 0) is None  # sin tamaño no se puede juzgar
    assert density_warning(1_000_000, 0) is not None  # ni palabras a la vista
    assert density_warning(1_000_000, None) is None  # sin recuento no se inventa un aviso


def test_sugerencias_de_studio_no_contaminan_la_respuesta():
    """NotebookLM a veces cierra con una oferta conversacional (flashcards, Studio).
    Ese texto no es parte de la respuesta y rompe el parseo programático."""
    from nlm_mcp.answerfmt import split_suggestions

    cuerpo = "**P1.** b) Diagnosticar y prevenir.\n\n**P2.** c) Antes de iniciar labores."
    cola = "💡 ¿Te gustaría que genere un juego de tarjetas de estudio en el panel de Studio?"
    answer, sugerencias = split_suggestions(f"{cuerpo}\n\n{cola}")
    assert answer == cuerpo
    assert "Studio" in sugerencias
    # sin cola: no inventa nada ni toca el cuerpo
    assert split_suggestions(cuerpo) == (cuerpo, None)
    # una pregunta legítima dentro de la respuesta NO se recorta
    dentro = "El material pregunta ¿cuál es el objetivo? y responde b)."
    assert split_suggestions(dentro) == (dentro, None)


def test_describe_generico_no_fuga_detalle():
    from nlm_mcp.errors import describe

    msg = describe(ImportError("detalle secreto al cargar C:\\Users\\usuario\\oculto\\x.py"))
    assert "ImportError" in msg  # la clase sí viaja: suficiente para diagnósticos simples
    assert "detalle secreto" not in msg
    assert "C:\\Users" not in msg


def test_top_terms_ignora_palabras_vacias():
    terms = bm25.top_terms("El Kardex y el kardex de arrastre: kardex, la la.", 3)
    assert terms[0] == {"term": "kardex", "count": 3}
    assert all(t["term"] not in {"el", "la", "de", "y"} for t in terms)
    assert [t["term"] for t in terms][:2] == ["kardex", "arrastre"]
    assert bm25.top_terms("", 5) == []


def test_marcador_de_imagenes_fuera_del_indice():
    # el marcador no debe colarse como término dominante...
    assert bm25.top_terms("texto " + mdtools.IMAGE_MARKER * 9 + " kardex kardex", 3)[0]["term"] == "kardex"
    # ...pero la prosa real que habla de imágenes sí se conserva
    terms = [t["term"] for t in bm25.top_terms("imagen imagen imagen retirada del anexo", 3)]
    assert "imagen" in terms
