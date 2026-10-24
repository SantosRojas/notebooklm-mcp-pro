"""Traducción de excepciones de notebooklm-py a mensajes accionables para el agente."""

from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from mcp.server.mcpserver.exceptions import ToolError
from notebooklm import exceptions as nx

from .validators import UserError

log = logging.getLogger("nlm_mcp")
T = TypeVar("T")


class SessionMissingError(Exception):
    """No hay sesión de Google guardada (no se ejecutó `notebooklm login` o la ruta es errónea)."""


def _is(exc: BaseException, *names: str) -> bool:
    classes = tuple(getattr(nx, n) for n in names if hasattr(nx, n))
    return bool(classes) and isinstance(exc, classes)


def is_transient(exc: BaseException) -> bool:
    """Errores de red/servidor/cuota: idempotente reintentarlos con backoff (solo lecturas)."""
    return _is(exc, "NetworkError", "ServerError", "RateLimitError")


def is_artifact_not_ready(exc: BaseException) -> bool:
    """El artefacto existe pero no se puede descargar como informe (no es uno, o aún no está listo).

    NotebookLM responde lo mismo en los dos casos («not ready»), que es engañoso cuando el
    artefacto sí está terminado: solo que es un archivo, un audio o una presentación. Quien
    llama necesita distinguirlo, y `read_report` lo resuelve consultando el tipo.
    """
    return _is(exc, "ArtifactNotReadyError")


def describe(exc: BaseException) -> str:
    """Mensaje seguro (sin cookies, tokens, rutas ni cuerpos de respuesta)."""
    if isinstance(exc, SessionMissingError):
        return (
            "No hay sesión de NotebookLM. Ejecuta `notebooklm login` en la máquina del servidor "
            "(o define NLM_STORAGE_PATH con la ruta de storage_state.json) y reinicia el cliente MCP."
        )
    if _is(exc, "AuthError", "HeadlessLoginRequiredError", "HeadlessReauthError"):
        return (
            "Sesión de NotebookLM caducada o ausente. Ejecuta `notebooklm login` en la máquina "
            "del servidor y reinicia el cliente MCP."
        )
    if _is(exc, "RateLimitError"):
        return "Google aplicó un límite de peticiones. Espera unos minutos y reintenta con menos paralelismo."
    if _is(exc, "NotFoundError"):
        return "No se encontró el recurso (notebook, fuente, nota o artefacto). Verifica el ID con list_notebooks / list_sources."
    if _is(exc, "NotebookLimitError"):
        return "Se alcanzó el límite de notebooks o fuentes de la cuenta."
    if _is(exc, "WaitTimeoutError"):
        return "La operación excedió el tiempo de espera. Puede seguir en curso en NotebookLM; reintenta más tarde."
    if _is(exc, "NetworkError"):
        return "Error de red al contactar con NotebookLM. Reintenta en unos segundos."
    if _is(exc, "ServerError"):
        return "NotebookLM devolvió un error de servidor (5xx). Reintenta más tarde."
    if _is(exc, "ValidationError"):
        return f"Parámetros rechazados por NotebookLM: {type(exc).__name__}."
    if _is(exc, "ConfigurationError"):
        return f"Configuración inválida de notebooklm-py: {type(exc).__name__}."
    if _is(exc, "NotebookLMError"):
        return f"Error de NotebookLM ({type(exc).__name__})."
    # Fallback para errores inesperados: SOLO la clase. El detalle completo queda en
    # stderr correlacionado por el ref — el texto del error podría contener secretos.
    return (
        f"Error interno del servidor MCP (tipo {type(exc).__name__}). "
        "Revisa los logs en stderr con el ref indicado."
    )


def guard(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Decorador para tools: nunca filtra trazas ni datos sensibles al agente."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except UserError as exc:
            raise ToolError(str(exc)) from None
        except asyncio.TimeoutError:
            raise ToolError("Tiempo de espera agotado (NLM_TIMEOUT_SECONDS).") from None
        except Exception as exc:
            ref = uuid.uuid4().hex[:8]  # marca de correlación: aparece en stderr y en el mensaje
            log.exception("ref=%s fallo en la herramienta %s", ref, fn.__name__)
            raise ToolError(f"{describe(exc)} (ref {ref})") from None

    return wrapper


def guard_resource(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Equivalente de `guard` para recursos MCP (lanza ResourceError)."""
    from mcp.server.mcpserver.exceptions import ResourceError

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return await fn(*args, **kwargs)
        except ResourceError:
            raise
        except UserError as exc:
            raise ResourceError(str(exc)) from None
        except asyncio.TimeoutError:
            raise ResourceError("Tiempo de espera agotado.") from None
        except Exception as exc:
            ref = uuid.uuid4().hex[:8]
            log.exception("ref=%s fallo en el recurso %s", ref, fn.__name__)
            raise ResourceError(f"{describe(exc)} (ref {ref})") from None

    return wrapper
