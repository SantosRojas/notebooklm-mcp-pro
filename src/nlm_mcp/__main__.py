"""Punto de entrada: `notebooklm-mcp-pro` o `python -m nlm_mcp`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from . import __version__
from .config import Settings, load_dotenv
from .server import build_server

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


async def _check(settings: Settings) -> int:
    from mcp import Client

    server = build_server(settings)
    async with Client(server) as client:
        result = await client.call_tool("health_check", {})
    data = result.structured_content
    if data is None:  # cliente antiguo: el resultado llega como bloque de texto
        texto = next((getattr(c, "text", "") for c in result.content if getattr(c, "text", None)), "{}")
        data = json.loads(texto)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0 if data.get("auth") == "ok" else 1


class _JsonLogFormatter(logging.Formatter):
    """Formato JSON en stderr: una línea por evento, pensado para grepear por ref/level."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def _setup_logging(settings: Settings) -> None:
    if settings.log_json:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_JsonLogFormatter())
        logging.basicConfig(level=settings.log_level, handlers=[handler], force=True)
        return
    # stdout está reservado al protocolo MCP en stdio: todo el log va a stderr.
    logging.basicConfig(
        stream=sys.stderr, level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm-mcp-pro", description="Servidor MCP para NotebookLM.")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument(
        "--host", default="127.0.0.1", help="Solo con streamable-http (por defecto, loopback)."
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Permite enlazar fuera de loopback. PELIGROSO: el servidor no tiene autenticación "
        "y expone tu sesión de Google; úsalo solo detrás de un proxy con autenticación.",
    )
    parser.add_argument("--env-file", default=None, help="Ruta a un .env (por defecto ./.env si existe).")
    parser.add_argument("--check", action="store_true", help="Verifica configuración y sesión, y termina.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    load_dotenv(args.env_file)
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"Configuración inválida: {exc}", file=sys.stderr)
        return 2

    _setup_logging(settings)

    if args.check:
        return asyncio.run(_check(settings))

    server = build_server(settings)
    if args.transport == "stdio":
        server.run("stdio")
        return 0

    if args.host not in _LOOPBACK and not args.allow_remote:
        print(
            f"Rechazado: --host {args.host} no es loopback. Usa --allow-remote bajo tu responsabilidad.",
            file=sys.stderr,
        )
        return 2
    if args.host not in _LOOPBACK:
        logging.getLogger("nlm_mcp").warning(
            "Servidor HTTP expuesto SIN autenticación en %s:%s", args.host, args.port
        )
    server.run("streamable-http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
