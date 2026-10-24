# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y
[Versionado Semántico](https://semver.org/lang/es/).

## [Sin publicar]

### Añadido

- `health_check` informa la **antigüedad de la sesión** (`session.age_days`, `stale` a partir de 7 días) con
  un aviso de reloguear: la caducidad de las cookies de Google no avisa y rompía las lecturas sin diagnóstico.
- `export_markdown` (notebook completo) es **incremental**: guarda el hash de cada fuente en `.manifest.json`, reescribe
  solo las que cambiaron (conservando su `exported_at`), elimina los `.md` de fuentes que ya no existen y
  devuelve `written`/`unchanged`/`removed`. `force` vuelve al comportamiento de reescribir todo.
- `mypy` (configuración laxa, `files = ["src"]`) añadido al extra `dev` y al CI, junto a `ruff` y `pytest`;
  los 10 hallazgos iniciales quedaron corregidos (tipado de `load_dotenv`, `resets_at`, `max()` de edades,
  lectura del resultado en `--check`).
- Depuración inline de imágenes incrustadas (`NLM_STRIP_IMAGES=1`, por defecto): los PDFs con visuales
  de NotebookLM llegan al Markdown con megabytes de `base64` incrustado (un PDF de 690 palabras medía
  1.2 MB); ahora se sustituyen por marcadores de texto antes de la caché — descargas ~99% más ligeras
  y sin ruido en `keyword_search`/`grep`.
- `clear_cache` acepta `purge_all: true` para vaciar la caché y las exportaciones de TODOS los
  notebooks de una sola vez (opción local; la opción anterior con `notebook_id` sigue igual).
- Los errores de descarga por fuente (`download_errors`) incluyen ahora `ref` de correlación
  (generado localmente) que coincide con la excepción completa en stderr.
- `keyword_search` y `grep` pueden **barrer todas las notebooks de la cuenta** (o las que indiques con
  `notebook_ids`) en paralelo, etiquetando cada resultado/coincidencia con su notebook de origen y aislando
  los fallos (`notebook_errors` con `ref`): una notebook caída no aborta el barrido. Sin cuota de chat.
- `source_stats`: radiografía local (caracteres, palabras, encabezados y términos dominantes por
  fuente + totales) reutilizando la caché; con `auto_sync=false` mide solo lo descargado. Sin
  `notebook_id` resume toda la caché por notebook (fuentes, caracteres y antigüedad), sin red ni cuota.
- 2 herramientas nuevas en `readonly`: `get_usage` (cuota de la cuenta: ventanas y acciones de la API
  interna de NotebookLM) y `get_chat_history` (turnos pregunta/respuesta del notebook). Además,
  `get_generation_status` acepta `wait_seconds` para esperar el estado terminal sin ciclos manuales
  del agente.
- `rename_notebook` en `readwrite` y `delete_note` en destructivo (con `confirm_title`, la librería
  soporta el borrado de notas), cerrando el CRUD de notas.
- `delete_artifact` en destructivo (informes/audio/cuestionarios: la librería soporta el borrado) y
  `export_markdown` con `source_id` para exportar una sola fuente como `.md`.
- `health_check` informa las versiones de `notebooklm-py` y `mcp` dependientes, para diagnóstico
  cuando Google rompa la API no oficial.
- `list_sources` acepta `limit` opcional (por defecto sigue devolviendo todas).
- Reintentos con backoff exponencial corto ante `NetworkError`/`ServerError`/`RateLimitError` en
  operaciones de lectura idempotentes (`NLM_RETRIES`, 0-5, por defecto 2). `ask_notebook` y las
  escrituras nunca se reintentan: un retry podría duplicar una respuesta de chat o una fuente.
- Logging JSON opcional (`NLM_LOG_JSON=1`) y `error_ref` de correlación en cada error (aparece en
  el mensaje del agente y en stderr, sin exponer trazas ni datos sensibles).
- `add_source_base64` (en `readwrite`): sube bytes ya en memoria — p. ej. un adjunto de correo — sin
  obligar a mover cientos de KB de base64 por el contexto del agente. Los bytes se materializan en un
  temporal efímero dentro de `NLM_UPLOAD_DIRS` y se borran en cuanto termina la subida (el directorio
  de transferencia se elimina si queda vacío). Pide `NLM_UPLOAD_DIRS` con el mismo mensaje que
  `add_source_file` y respeta el tope de 50 MB decodificados.
- `health_check.file_upload_hint`: cuando la subida está apagada, explica en la propia respuesta qué
  variable definir (`NLM_UPLOAD_DIRS`); `None` cuando ya está habilitada.
- `text_density_warning` en `add_source_file` y `add_source_base64`: avisa cuando el archivo trae
  pocas palabras para su tamaño (< 2 palabras/KB, o ninguna), el caso típico de un deck de imágenes
  que NotebookLM no puede cubrir. La clave solo aparece cuando hay aviso, así la respuesta normal no
  crece.
- `ask_notebook` separa la oferta conversacional de NotebookLM (flashcards, panel de Studio) en
  `suggestions`: `answer` queda como la síntesis y nada se descarta. El recorte es conservador — solo
  el último párrafo si combina un marcador de producto con tono de oferta — así que una pregunta
  legítima dentro de la respuesta no se toca.
- El desbordado de marcadores de imagen se colapsa: una racha de `_[imagen retirada]_` consecutivos
  pasa a un solo marcador, para que un PDF con decenas de visuales no llene la lectura de ruido.
  Una imagen única conserva exactamente su forma.

### Cambiado

- La configuración de ejemplo (`examples/opencode.json`), la del repositorio (`opencode.json`) y el fragmento
  del README pasan a la **forma nativa de OpenCode 2**: los servidores van bajo `mcp.servers` y para dejarlos
  configurados sin conectar se usa `disabled` (el inverso del antiguo `enabled`). OpenCode 2 sigue aceptando la
  forma anterior, pero ya no se recomienda en la documentación.
- **Catálogo consolidado de 27 a 23 herramientas** (menos esquemas que cargar en cada conversación y
  menos pares de herramientas casi idénticas entre las que elegir), sin perder capacidades:
  - `keyword_search` y `grep` absorben su variante «entre notebooks»: sin `notebook_id` (o con
    `notebook_ids` / `max_notebooks`) recorren varias notebooks, etiquetan cada resultado con su origen y
    aíslan los fallos por notebook (`notebook_errors` con `ref`). `keyword_search_all` y `grep_sources`
    desaparecen (el grep pasa a llamarse `grep`).
  - `get_generation_status` acepta `wait_seconds`: 0 responde al instante y >0 espera activamente el estado
    terminal devolviendo `final`. `wait_for_generation` desaparece.
  - `export_source_markdown` se integra en `export_markdown` (`source_id` opcional) y la exportación acepta
    `directory` para escribir los `.md` en la carpeta que elija el usuario (absoluta, o relativa a la de
    exportaciones del servidor); las exportaciones locales quedan registradas en la auditoría.
  - `cache_stats` se integra en `source_stats` (sin `notebook_id`, resumen de la caché por notebook):
    era un agregado derivable de los mismos datos, con la red y la cuota de por medio de más.
  - Las tres respuestas de estado local llevan `mode` (`cache` / `notebook`) para que la forma de la
    respuesta se explique sola.
- El barrido entre notebooks de `grep` y `keyword_search` vive en un solo sitio (`_sweep`): el
  aislamiento de fallos por notebook y los contadores ya no están duplicados en dos herramientas que se
  desincronizaban entre ellas. El trabajo local (leer la caché, indexar, buscar) se ejecuta fuera del
  bucle de eventos, así que un `grep` largo o una regex costosa ya no dejan al servidor sin atender
  otras llamadas mientras dura el escaneo.
- Contrato de respuestas unificado: `count` es lo devuelto y `total` lo que coincide antes del recorte
  (`list_notebooks` usaba `total`/`returned` y `list_sources` `count`/`returned`), y el barrido informa
  `sources_not_ready` (contador agregado, como `sources_ready`) para distinguir "no lo encontré" de
  "aún no estaba listo".
- El barrido de `keyword_search`/`grep` informa también **`notebooks_scanned_ids`**: con `max_notebooks`
  los contadores no dejaban saber *qué* notebooks se habían recorrido, así que un recuento que no
  cuadraba era indescifrable (y no había forma de comprobar si la notebook que te interesaba entró en el
  tope). `notebooks_scanned` se calcula ahora a partir de esa lista, de modo que ambos campos no pueden
  contradecirse.
- Verificación de calidad ampliada a `ruff format --check` y a las pruebas del barrido entre notebooks
  (110 en total, 1 omitida). El catálogo de solo lectura sigue en 23; `readwrite` pasa a +10 tools.

### Corregido

- Las tools **rechazan los argumentos que no declaran**. Antes se descartaban en silencio: un
  `context` mal escrito en lugar de `context_lines` ejecutaba la herramienta con sus valores por
  defecto y devolvía otro resultado sin avisar (el peor fallo posible para algo que consumen agentes).
  El esquema publicado declara ahora `"additionalProperties": false`, la llamada falla nombrando el
  campo sobrante y `health_check` informa `strict_arguments` por si una versión futura de `mcp`
  dejara de permitir esta configuración.
- `read_report` solo puede descargar informes, pero ante un artefacto de otro tipo (un `.md` subido,
  un audio, una presentación) devolvía el «no está listo» interno de NotebookLM **aunque el artefacto
  estuviera terminado**, dejando al agente sin saber qué hacer. Ahora consulta el tipo y responde con
  él («no es un informe (tipo `file`): usa `read_source`/`export_markdown`…») o, si el que no está
  listo es un informe en curso, remite a `get_generation_status`. La traducción es solo para el error
  de artefacto: un fallo de red sigue diciendo que es de red.
- `export_markdown`: escribir en una carpeta elegida (`directory`) podía **borrar archivos del usuario**.
  La limpieza recorría todos los `.md` del destino y eliminaba los que no estaban en el manifiesto; ahora
  solo considera los registrados en el propio manifiesto y con el patrón de exportación
  (`titulo__<sid8>.md`), de modo que los archivos ajenos nunca se tocan.
- `export_markdown`: una fuente que no se podía descargar (fallo transitorio o aún no lista) perdía su
  `.md` previo y se contaba como `removed`. Ahora se conserva la última exportación, su digest sigue en
  el manifiesto y la fuente se reporta en el nuevo campo `not_written` (con nota en `INDEX.md`).
- `get_source_outline` devuelve títulos de encabezados extraídos de las fuentes pero no marcaba
  `untrusted_content`, a diferencia del resto de lecturas de contenido: se unifica la marca.
- `get_chat_history` documentaba "retomar la conversación con su `conversation_id`" sin devolver nunca
  ese campo (el historial solo trae pregunta y respuesta): el texto ahora dice lo que la herramienta hace.
- `state.py`: el diccionario de candados de descarga crecía sin límite en un servidor de
  larga vida; ahora se elimina cada entrada cuando nadie la usa ni la espera.
- `keyword_search` / `grep`: los fallos de descarga de fuentes ya no quedan
  silenciados; la respuesta incluye `download_errors` (id y motivo).
- `grep`: `truncated` ya no da falsos positivos cuando las coincidencias llegan
  exactamente al tope; con regex, el escaneo se corta entre líneas tras ~5 s
  (`timed_out: true`) para que un patrón patológico no cuelgue la herramienta.
- `backend.py`: todas las llamadas a `notebooklm-py` usan argumentos nombrados, para que un
  cambio de orden de parámetros en la librería no invierta silenciosamente título/contenido.
- `audit.jsonl`: ahora rota por tamaño (default 10 MiB, configurable con
  `NLM_AUDIT_MAX_BYTES`), conservando una generación en `.1`.
- `ask_notebook`: `conversation_id` se valida de forma permisiva (solo viaje por RPC);
  el formato interno de NotebookLM ya no puede rechazar IDs reales.
- Ciclo de CRUD de notas completado: ya se pueden borrar notas (`delete_note`).

### Añadido (infraestructura)

- Metadatos de empaquetado: `LICENSE` (MIT), `classifiers`, changelog.
- CI en GitHub Actions: `pytest` (Python 3.10–3.13 en Ubuntu; 3.13 en Windows) y
  `ruff check` + `ruff format --check` + `mypy`.
- `ruff` configurado en `[tool.ruff]` y añadido a `dev`.
- Formato normalizado con `ruff format` (`line-length = 110`): el código se escribió a mano con
  líneas algo más largas y la verificación de formato del CI fallaba en 10 archivos.
