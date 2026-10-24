"""Contrato de entrada de las tools: rechazar los argumentos desconocidos.

El SDK deriva el modelo Pydantic de argumentos de cada tool a partir de su firma, con la
configuración por defecto de Pydantic (``extra="ignore"``): un nombre mal escrito (``limit``
en vez de ``max_matches``) se descarta **en silencio** y la tool se ejecuta con sus valores por
defecto. Para un servidor que consumen agentes eso convierte un error de tecleo en un resultado
distinto sin aviso, así que pedimos ``extra="forbid"``: Pydantic rechaza el campo y el SDK
devuelve un error que lo nombra. El esquema publicado hereda el cambio
(``"additionalProperties": false``), de modo que quien llama ve la restricción de antemano.

Se aplica sobre ``ArgModelBase``, la base de la que el SDK deriva el modelo de cada tool, y
siempre antes de registrar ninguna (lo hace ``build_server``). Si una versión futura de ``mcp``
mueve ese símbolo, el parche se salta con un aviso y el servidor sigue funcionando como antes;
``tests/test_server.py`` lo detecta porque exige que la llamada falle.
"""

from __future__ import annotations

import logging

from pydantic import ConfigDict

log = logging.getLogger("nlm_mcp")


def apply_strict_arguments() -> bool:
    """Exige que las tools rechacen los argumentos que no declaran.

    Devuelve si se pudo aplicar (``False`` = el SDK no expone dónde hacerlo y se mantiene el
    comportamiento permisivo por defecto). Es idempotente.
    """
    try:
        from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
    except ImportError:  # pragma: no cover - depende de la versión de mcp
        log.warning("mcp no expone ArgModelBase: los argumentos desconocidos se ignorarán en silencio")
        return False
    config: ConfigDict = {**ArgModelBase.model_config, "extra": "forbid"}
    ArgModelBase.model_config = config
    return True
