"""Configuración por variables de entorno, validada y de solo lectura (frozen)."""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

MODES = ("readonly", "readwrite")
_TRUE = {"1", "true", "yes", "on", "si", "sí"}


def _get(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    value = _get(env, name)
    return default if value is None else value.lower() in _TRUE


def _int(env: Mapping[str, str], name: str, default: int, minimum: int = 1) -> int:
    value = _get(env, name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser un entero, recibido: {value!r}") from exc
    if parsed < minimum:
        raise ValueError(f"{name} debe ser >= {minimum}, recibido: {parsed}")
    return parsed


def _default_cache_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "notebooklm-mcp-pro"


@dataclass(frozen=True)
class Settings:
    mode: str
    allow_destructive: bool
    storage_path: str | None
    profile: str | None
    cache_dir: Path
    cache_ttl: int
    max_chars: int
    upload_dirs: tuple[Path, ...]
    max_concurrency: int
    timeout: float
    audit_log: Path
    audit_max_bytes: int
    log_level: str
    log_json: bool
    retries: int
    default_language: str
    strip_images: bool

    @property
    def writable(self) -> bool:
        return self.mode == "readwrite"

    @property
    def destructive_enabled(self) -> bool:
        # Lo destructivo exige explícitamente modo escritura: nunca se activa solo.
        return self.writable and self.allow_destructive

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        mode = (_get(env, "NLM_MCP_MODE") or "readonly").lower()
        if mode not in MODES:
            raise ValueError(f"NLM_MCP_MODE debe ser uno de {MODES}, recibido: {mode!r}")

        cache_dir = Path(_get(env, "NLM_CACHE_DIR") or _default_cache_dir()).expanduser()

        raw_dirs = _get(env, "NLM_UPLOAD_DIRS") or ""
        upload_dirs = tuple(Path(p).expanduser().resolve() for p in raw_dirs.split(os.pathsep) if p.strip())

        audit = Path(_get(env, "NLM_AUDIT_LOG") or cache_dir / "audit.jsonl").expanduser()

        log_level = (_get(env, "NLM_LOG_LEVEL") or "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"NLM_LOG_LEVEL inválido: {log_level!r}")

        return cls(
            mode=mode,
            allow_destructive=_bool(env, "NLM_MCP_ALLOW_DESTRUCTIVE"),
            storage_path=_get(env, "NLM_STORAGE_PATH"),
            profile=_get(env, "NLM_PROFILE"),
            cache_dir=cache_dir,
            cache_ttl=_int(env, "NLM_CACHE_TTL_SECONDS", 3600, minimum=0),
            max_chars=_int(env, "NLM_MAX_CHARS", 20000, minimum=1000),
            upload_dirs=upload_dirs,
            max_concurrency=_int(env, "NLM_MAX_CONCURRENCY", 4),
            timeout=float(_int(env, "NLM_TIMEOUT_SECONDS", 60)),
            audit_log=audit,
            audit_max_bytes=_int(env, "NLM_AUDIT_MAX_BYTES", 10 * 1024 * 1024, minimum=65536),
            log_level=log_level,
            log_json=_bool(env, "NLM_LOG_JSON"),
            retries=min(_int(env, "NLM_RETRIES", 2, minimum=0), 5),
            default_language=_get(env, "NLM_DEFAULT_LANGUAGE") or "es",
            strip_images=_bool(env, "NLM_STRIP_IMAGES", default=True),
        )


def load_dotenv(path: str | Path | None = None, env: dict[str, str] | None = None) -> bool:
    """Cargador mínimo de .env (KEY=VALUE). No sobrescribe variables ya definidas.
    Evita añadir dependencias; solo se usa si el archivo existe."""
    target = Path(path) if path else Path.cwd() / ".env"
    resolver: MutableMapping[str, str] = os.environ if env is None else env
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in resolver:
            resolver[key] = value
    return True
