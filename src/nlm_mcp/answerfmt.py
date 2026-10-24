"""Presentación de la respuesta de `ask_notebook` para consumo programático.

NotebookLM cierra algunas respuestas con una oferta conversacional del producto
("¿Te gustaría que genere un juego de tarjetas de estudio en el panel de Studio?").
Ese texto no es parte de la respuesta y rompe el parseo: quien recibe el campo
`answer` espera la síntesis, no un llamado a la acción. Se separa en su propio campo
en vez de descartarlo, para no perder información si el operador quiere verla.

El recorte es deliberadamente conservador: solo se separa el último párrafo cuando
combina un marcador de producto con tono de oferta. Una pregunta legítima dentro de
la respuesta no se toca.
"""

from __future__ import annotations

import re

# Marcadores que identifican al producto (no al contenido del notebook).
_PRODUCTO = re.compile(
    r"(?i)(panel de studio|studio\b|flashcards?|juego de tarjetas|tarjetas de estudio|"
    r"cuestionario interactivo|gu[ií]a de estudio|audio overview|resumen de audio|mapa mental)"
)

# Tono de oferta: el párrafo habla de generar algo para el lector.
_OFERTA = re.compile(r"(?i)(¿\s*te gustar|te gustar[ií]a|generar|genere|crear|crea[r]? un|puedo ayudarte)")

_MIN_PARTES = 2


def split_suggestions(answer: str) -> tuple[str, str | None]:
    """Divide una respuesta de NotebookLM en (cuerpo, sugerencias del producto).

    `sugerencias` es None cuando no hay cola que separar: el cuerpo se devuelve intacto,
    sin normalizar espacios ni recortar nada.
    """
    partes = answer.strip().split("\n\n")
    if len(partes) < _MIN_PARTES:
        return answer, None

    cola = partes[-1]
    if not (_PRODUCTO.search(cola) and _OFERTA.search(cola)):
        return answer, None

    cuerpo = "\n\n".join(partes[:-1]).strip()
    if not cuerpo:  # toda la respuesta era la oferta: no se queda vacío
        return answer, None
    return cuerpo, cola.strip()
