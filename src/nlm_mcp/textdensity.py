"""Aviso de densidad de texto de una fuente recién subida.

Un PDF de 500 KB puede ser un documento denso o un deck de diapositivas donde casi
todo vive en imágenes. NotebookLM solo indexa el texto extraído, así que en el segundo
caso el notebook queda con agujeros de cobertura sin que nadie lo avise: una pregunta
hecha con `ask_notebook` sobre una lámina solo imagen se contesta desde el conocimiento
general del modelo, no desde el material. Este aviso convierte ese fallo silencioso en
una señal accionable (añadir una fuente de texto complementaria o revisar el origen).
"""

from __future__ import annotations

_BYTES_PER_KB = 1024

# Por debajo de 2 palabras por KB el contenido es casi con certeza visual.
# Un PDF de texto ronda 15-60 palabras/KB; un deck de imágenes, menos de 1.
_MIN_WORDS_PER_KB = 2.0


def density_warning(size_bytes: int, word_count: int | None) -> str | None:
    """Devuelve un aviso si la fuente trae muy poco texto para su tamaño, o None.

    `word_count` es el que reporta NotebookLM tras indexar. Sin tamaño no se puede juzgar,
    y si el recuento no vino (`None`) tampoco: avisar entonces sería inventar un dato.
    """
    if not size_bytes or word_count is None:
        return None
    if word_count == 0:
        return (
            f"NotebookLM extrajo 0 palabras de un archivo de {size_bytes // _BYTES_PER_KB} KB: "
            "el contenido es visual (imagen o escaneado) y NO quedó indexado. No resuelvas "
            "preguntas sobre este material sin añadir una fuente de texto."
        )
    density = word_count / (size_bytes / _BYTES_PER_KB)
    if density >= _MIN_WORDS_PER_KB:
        return None
    return (
        f"Poca densidad de texto: {word_count} palabras en {size_bytes // _BYTES_PER_KB} KB "
        f"(≈{density:.1f} palabras/KB). Lo más probable es que el documento sea de imágenes: "
        "el notebook puede no cubrir su contenido. Considera añadir una fuente de texto."
    )
