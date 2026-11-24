# notebooklm-mcp-pro

Servidor **MCP** para **NotebookLM** (ahora «Gemini Notebook») pensado para que un agente de código
**lea, busque y razone sobre los documentos de tu cuenta** gastando el mínimo de contexto, y que solo
pueda escribir o borrar si tú lo habilitas expresamente.

Aprovecha que NotebookLM ya guarda cada fuente convertida a **Markdown**: el agente puede ver el
índice de encabezados, leer solo la sección que necesita, buscar en local (BM25 / grep) sin gastar
cuota, o exportar todo a una carpeta de `.md` para trabajar con sus propias herramientas de archivos.

> ⚠️ **API no oficial.** NotebookLM no tiene API pública para cuentas personales. Este proyecto usa
> [`notebooklm-py`](https://github.com/teng-lin/notebooklm-py), que emplea endpoints internos de Google
> y puede romperse sin aviso (por eso la versión está fijada a `>=0.8.5,<0.9`). Úsalo con tu propia
> cuenta y bajo tu responsabilidad; revisa los términos de servicio de Google.
>
> 🔐 El `storage_state.json` que crea `notebooklm login` **es tu sesión de Google**. Trátalo como una
> contraseña: no lo subas a Git ni lo compartas.

## ¿En qué se diferencia del MCP que ya trae `notebooklm-py`?

`notebooklm-py` incluye su propio servidor (`notebooklm-mcp`), amplio en administración: compartir
notebooks, investigación web/Drive, generación de audio/vídeo, OAuth para alojarlo en remoto, etc.
Este proyecto **no lo reemplaza**; se centra en otra cosa:

| | Este proyecto | `notebooklm-mcp` incluido |
|---|---|---|
| Enfoque | Acceso al conocimiento para agentes de código | Automatización integral de NotebookLM |
| Lectura económica en contexto | `get_source_outline`, `read_source_section`, paginación por `next_offset` | — |
| Búsqueda local sin cuota | `keyword_search` (BM25) y `grep` (texto/regex con contexto), en una notebook o **en todas a la vez** | — |
| Exportar fuentes a `.md` en disco | `export_markdown` (notebook completo o una fuente, en la carpeta que elijas) | — |
| Seguridad | Solo lectura por defecto; las herramientas de escritura **ni se registran** si no se habilitan; borrado exige repetir el título exacto; auditoría | Confirmaciones vía anotaciones MCP |
| No cubre | Compartir, investigación web/Drive, audio/vídeo/cuestionarios, OAuth remoto | — |

Si necesitas lo que aquí no está, puedes ejecutar ambos servidores a la vez.

## Instalación

Requiere Python ≥ 3.10.

```bash
cd notebooklm-mcp-pro
uv sync --extra browser              # o: python -m venv .venv && pip install -e ".[browser]"
source .venv/bin/activate            # bash/zsh | fish: source .venv/bin/activate.fish
playwright install chromium          # si el login pide el navegador de Playwright
notebooklm login                     # abre el navegador y guarda la sesión (storage_state.json)
notebooklm-mcp-pro --check           # verifica configuración y sesión
```

> **Fish:** `.venv/bin/activate` es un script de bash y falla con `Unsupported use of '='`. Usa
> `source .venv/bin/activate.fish`, o evita activar nada con `uv run notebooklm-mcp-pro --check`.

`--check` imprime un JSON de estado y devuelve código `0` si la sesión es válida (`auth: ok`).

### Servidor sin navegador (headless)

`notebooklm login` abre un navegador con Playwright, así que no sirve en un servidor sin escritorio
(ni en Alpine/musl, donde Playwright no tiene binarios). El login se hace **en tu equipo local** y
solo se copia el archivo de sesión:

```bash
# 1) En tu equipo local (con navegador), misma versión de notebooklm-py:
pipx install "notebooklm-py[browser]==0.8.5"
notebooklm login

# 2) Copia la sesión al servidor (es un secreto: permisos 600):
scp ~/.notebooklm/profiles/default/storage_state.json \
    usuario@servidor:~/.notebooklm/profiles/default/storage_state.json

# 3) En el servidor: apunta el servidor MCP a esa sesión y verifica
export NLM_STORAGE_PATH="$HOME/.notebooklm/profiles/default/storage_state.json"
notebooklm-mcp-pro --check
```

Si prefieres no copiar archivos, `notebooklm auth import-cookies cookies.json` acepta un
`storage_state` de Playwright o cookies exportadas desde tu navegador (JSON).

Cuando Google caduque la sesión, repite los pasos 1-2. Los errores de auth del servidor MCP
(`auth: error`) apuntan a este mismo remedio.

### Conectarlo a opencode

Añade esto a `~/.config/opencode/opencode.json` (o al `opencode.json` del proyecto), con la ruta real
(`examples/opencode.json` tiene el archivo listo para copiar):

```json
{
  "mcp": {
    "servers": {
      "notebooklm": {
        "type": "local",
        "command": ["/RUTA/ABSOLUTA/notebooklm-mcp-pro/.venv/bin/notebooklm-mcp-pro"],
        "environment": { "NLM_MCP_MODE": "readonly" }
      }
    }
  }
}
```

En Windows el ejecutable está en `.venv\Scripts\notebooklm-mcp-pro.exe`. Para Claude Desktop y otros
clientes con la clave `mcpServers`, usa `examples/claude_desktop_config.json`.

Esta es la forma nativa de OpenCode 2: los servidores van bajo `mcp.servers` y para dejar uno
configurado sin conectar se usa `disabled: true`. En OpenCode 1 el nombre va directamente bajo `mcp` y
el campo equivalente es `enabled` (su inverso); V2 sigue aceptando esa forma, pero conviene migrarla.

## Herramientas

### Siempre disponibles (modo `readonly`) — 23

| Herramienta | Para qué sirve |
|---|---|
| `health_check` | Modo, caché, versiones, **antigüedad de la sesión** (avisa cuando toca reloguear) y `file_upload_hint` (cómo habilitar la subida si está apagada) |
| `list_notebooks`, `get_notebook` | Notebooks, resumen generado y temas sugeridos |
| `list_sources` | Fuentes con tipo, estado, URL y nº de palabras (filtrable; `limit` opcional) |
| `read_source` | Fuente en Markdown, **paginada** (`offset` → `next_offset`) |
| `get_source_outline` | Índice de encabezados (ignora `#` dentro de bloques de código) |
| `read_source_section` | Solo una sección, hasta el siguiente encabezado del mismo nivel |
| `get_source_guide` | Resumen y palabras clave que NotebookLM genera por fuente |
| `search_sources` | Búsqueda **semántica** de NotebookLM (fragmentos por relevancia) |
| `keyword_search` | Búsqueda léxica **BM25 local** (sin acentos/mayúsculas) |
| `grep` | Texto o regex con líneas de contexto, sobre la caché |
| `source_stats` | Radiografía local **sin cuota**: por fuente (caracteres, palabras, encabezados, términos dominantes) o, sin `notebook_id`, resumen de toda la caché (fuentes, caracteres, antigüedad) |
| `ask_notebook` | Pregunta con respuesta **y citas** a las fuentes (ver nota); si NotebookLM cierra con una oferta de Studio, va en `suggestions` y no ensucia `answer` |
| `list_notes`, `read_note` | Notas del notebook |
| `list_artifacts`, `read_report`, `get_generation_status` | Informes y su estado: `read_report` lee los de tipo `report` (los de `generate_report`), `get_generation_status` acepta `wait_seconds` y espera el estado terminal |
| `get_usage` | Cuota de la cuenta (ventanas y acciones con capacidad restante). Si la cuenta no la expone, responde `status: disabled` |
| `get_chat_history` | Historial de chat del notebook (turnos pregunta/respuesta) |
| `sync_notebook` | Descarga todas las fuentes a la caché local |
| `export_markdown` | Deja un `.md` por fuente (con front matter) + `INDEX.md` en disco; **incremental** (reescribe solo lo que cambió). Con `source_id` exporta **una sola fuente**, y `directory` elige la carpeta destino |
| `clear_cache` | Borra caché y exportaciones locales de un notebook — o de TODOS con `purge_all: true` |

Convenciones de las respuestas: `count` es lo que devuelve la llamada y `total` lo que coincide antes del
recorte; los listados con `limit` añaden `truncated` y las lecturas paginadas `next_offset`. Lo que sale
del contenido de las fuentes va marcado con `untrusted_content: true` (texto de terceros: nunca
instrucciones). Las herramientas de barrido añaden `notebooks_requested`, `notebooks_scanned` (y
`notebooks_scanned_ids`: con `max_notebooks` conviene comprobar que tu notebook entró en el tope),
`sources_not_ready`, `download_errors` (por fuente) y `notebook_errors` (por notebook). Las subidas
(`add_source_file`, `add_source_base64`) añaden `text_density_warning` **solo** cuando el archivo trae
muy poco texto para su tamaño — típicamente un deck de imágenes que NotebookLM no podrá cubrir del todo.

Tres detalles del contrato que son deliberados: en un barrido, `notebook_title` solo se rellena cuando se
recorren varias notebooks (en una sola ya sabes cuál es y se ahorra una llamada); las búsquedas no
devuelven `total` porque el escaneo se corta al agotar el presupuesto y un total sería un dato falso
(`truncated` avisa de que había más); y `grep`/`keyword_search` son locales, así que no consumen cuota.

También expone el recurso `notebooklm://notebooks`, la plantilla
`notebooklm://notebooks/{notebook_id}/sources/{source_id}` y tres prompts
(`research_topic`, `summarize_source`, `compare_sources`).

> **Nota sobre `ask_notebook`:** no modifica fuentes, por eso está en modo lectura, pero consume la
> cuota de chat de tu cuenta y deja la pregunta en el historial del notebook.

### Con `NLM_MCP_MODE=readwrite` — +10

`create_notebook`, `rename_notebook`, `add_source_url` (solo http/https), `add_source_text`,
`add_source_file` (solo desde carpetas de `NLM_UPLOAD_DIRS`), `add_source_base64` (bytes ya en
memoria, p. ej. un adjunto de correo: pasan por un temporal efímero dentro de `NLM_UPLOAD_DIRS` y no
viajan por el contexto del agente), `rename_source`, `create_note`, `update_note`, `generate_report`
(asíncrona: luego `get_generation_status` y `read_report`).

### Con `readwrite` + `NLM_MCP_ALLOW_DESTRUCTIVE=1` — +4

`delete_source`, `delete_note`, `delete_artifact` y `delete_notebook`: exigen `confirm_title` con el
**título exacto actual**. Si no coincide, no se borra nada y el error no revela el título correcto,
así el agente tiene que consultarlo (y tú confirmarlo) en lugar de reintentar a ciegas.

## Flujo recomendado para el agente

1. `list_notebooks` → `list_sources` para ubicar el material.
2. Localizar: `search_sources` (semántica) o `keyword_search` / `grep` (local). Si no sabes en qué
   notebook está algo, omite `notebook_id` (`notebook_ids` para limitar el barrido): ambas recorren
   todas las notebooks y etiquetan cada resultado con su origen.
3. Leer: `get_source_outline` → `read_source_section`. `read_source` solo si hace falta el documento entero.
   `source_stats` resume tamaño, encabezados y términos dominantes sin gastar cuota (sin `notebook_id`,
   toda la caché: útil para decidir `sync_notebook` o `clear_cache`).
4. `ask_notebook` cuando se quiere una síntesis con citas ya hecha por NotebookLM.
5. `export_markdown` si el agente va a trabajar con sus herramientas de archivos (`directory` para
   dejar los `.md` donde te interese).

La primera `keyword_search`/`grep` de un notebook descarga sus fuentes (campo `downloaded_now`);
después usan la caché. Con `auto_sync=false` buscan solo en lo ya descargado.

## Configuración (variables de entorno o `.env`)

| Variable | Defecto | Descripción |
|---|---|---|
| `NLM_MCP_MODE` | `readonly` | `readonly` o `readwrite` |
| `NLM_MCP_ALLOW_DESTRUCTIVE` | `0` | Habilita borrado (solo con `readwrite`) |
| `NLM_UPLOAD_DIRS` | *(vacío)* | Carpetas permitidas para `add_source_file` y `add_source_base64` (separador `:` en Linux/Mac, `;` en Windows). Vacío = subida deshabilitada |
| `NLM_STORAGE_PATH` | *(de la librería)* | Ruta a `storage_state.json` |
| `NLM_PROFILE` | *(activo)* | Perfil de `notebooklm-py` (varias cuentas) |
| `NLM_CACHE_DIR` | `~/.cache/notebooklm-mcp-pro` | Caché, exportaciones y auditoría |
| `NLM_CACHE_TTL_SECONDS` | `3600` | Vigencia de la caché (`0` = nunca usarla) |
| `NLM_MAX_CHARS` | `20000` | Tamaño máximo de página devuelta al agente (mín. 1000) |
| `NLM_MAX_CONCURRENCY` | `4` | Llamadas simultáneas a NotebookLM |
| `NLM_TIMEOUT_SECONDS` | `60` | Timeout por llamada (algunas operaciones lo multiplican) |
| `NLM_DEFAULT_LANGUAGE` | `es` | Idioma por defecto de `generate_report` |
| `NLM_AUDIT_LOG` | `<cache>/audit.jsonl` | Registro de escrituras/borrados |
| `NLM_AUDIT_MAX_BYTES` | `10485760` | Rotación del registro de auditoría por tamaño (se guarda una generación en `.1`) |
| `NLM_RETRIES` | `2` | Reintentos (0-5) ante errores de red/servidor/cuota en **lecturas**; las operaciones no idempotentes nunca se reintentan |
| `NLM_LOG_JSON` | `0` | `1` = logs en JSON estructurado en stderr (una línea por evento) |
| `NLM_STRIP_IMAGES` | `1` | Recorta imágenes incrustadas (base64) del Markdown de las fuentes: los PDFs con screenshots vienen con MB de blobs; por defecto se reemplazan por marcadores de texto |
| `NLM_LOG_LEVEL` | `INFO` | Los logs van a **stderr** (stdout es del protocolo) |

Hay una plantilla en `.env.example`. El servidor lee `./.env` si existe (sin pisar variables ya definidas).

## Modelo de seguridad

- **Mínimo privilegio:** en `readonly` las herramientas de escritura no existen para el agente.
- **Borrado deliberado:** doble habilitación (`readwrite` + flag) y título exacto como confirmación.
- **Contenido no confiable:** el texto de las fuentes se marca con `untrusted_content: true` y las
  instrucciones del servidor piden tratarlo como datos, no como órdenes (mitiga *prompt injection*).
- **IDs validados** (`[A-Za-z0-9_-]`): imposible escapar de la carpeta de caché con `../`.
- **Subidas** solo desde carpetas permitidas; se resuelven symlinks y `..` antes de comprobar.
- **Sin filtración de errores:** al agente solo llegan mensajes controlados (nunca cookies, rutas ni
  trazas). Cada fallo lleva un `ref` (p. ej. `... (ref 3a9c1f2b)`) que aparece en los logs de stderr
  para que el operador pueda encontrar la traza exacta.
- **Caché privada:** directorios `0700`, archivos `0600`, escritura atómica. Nota: los permisos POSIX
  se aplican en Linux/Mac; en Windows se ignora silenciosamente (protege la ruta con tu perfil).
- **Auditoría** JSONL de toda escritura/borrado; nunca guarda contenido, solo longitud y huella SHA-256.
- **Transporte:** `stdio` por defecto. `--transport streamable-http` solo enlaza a loopback; `--allow-remote`
  existe pero **no hay autenticación**: expondría tu sesión de Google. No lo uses sin un proxy con autenticación.
- `grep` es **literal por defecto**; con `literal=false` usa regex de Python (limitada a 300
  caracteres y con un presupuesto de ~5 s de escaneo: si la regex es patológica, se corta y devuelve
  `timed_out: true`, aunque una única línea con backtracking catastrófico puede seguir demorando).
- `export_markdown` con `directory` escribe donde le pidas (absoluto, o relativo a la carpeta de
  exportaciones del servidor): es una escritura local a cargo del agente, como el resto de `readonly`.

## Arquitectura

```
src/nlm_mcp/
  server.py     herramientas, recursos y prompts (registro condicionado por modo)
  backend.py    Protocol `Backend` + adaptador sobre notebooklm-py (único archivo que toca la librería;
                lecturas idempotentes con reintento y backoff ante red/5xx/cuota)
  state.py      concurrencia, timeouts, descarga única por fuente, sincronización
  cache.py      caché Markdown en disco (TTL, atómica, privada)
  mdtools.py    outline, secciones, paginación, grep, troceado
  bm25.py       búsqueda léxica local
  validators.py IDs, rutas de subida, regex
  errors.py     excepciones de la librería -> mensajes accionables
  config.py     configuración validada (inmutable)
  audit.py      auditoría JSONL
```

Todo el servidor depende de la interfaz `Backend`, no de `notebooklm-py`. Si Google rompe algo o quieres
otro origen de datos, solo se toca `backend.py`.

## Pruebas

```bash
uv sync --extra dev        # o: pip install -e ".[dev]"
uv run --extra dev pytest
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run --extra dev mypy
```

111 pruebas: unitarias (config, validadores, Markdown, BM25, caché), de servidor completo con un cliente MCP
en memoria (modos, gating de seguridad, paginación, búsqueda local en una o varias notebooks, grep con
contexto, exportación incremental y a una carpeta elegida, errores, escritura, borrado, recursos, prompts,
CLI, generaciones con y sin espera, cuota e historial) y del adaptador
contra los **modelos reales** de `notebooklm-py` (incluidos los reintentos ante errores transitorios).
`ruff check`, `ruff format --check` y `mypy` (configuración laxa, solo `src/`) y las pruebas corren en CI en cada push
(GitHub Actions, `.github/workflows/ci.yml`).

**Estado de verificación:** las pruebas no se ejecutan contra Google (requieren una cuenta real).
El adaptador se validó con instancias reales de las clases de la librería y un cliente simulado; la
primera ejecución real con tu cuenta puede revelar diferencias. Empieza con `notebooklm-mcp-pro --check`
y una lectura sobre un notebook de prueba.

## Solución de problemas

| Síntoma | Causa y solución |
|---|---|
| `auth: error` / «No hay sesión» | Ejecuta `notebooklm login` o define `NLM_STORAGE_PATH` |
| Sesión caducada | Repite `notebooklm login` y reinicia el cliente MCP |
| Servidor sin navegador / `playwright` no instala | Haz el login en tu equipo y copia `storage_state.json` (ver «Servidor sin navegador») |
| `source .venv/bin/activate` falla en fish | Usa `source .venv/bin/activate.fish` o `uv run` |
| «Google aplicó un límite» | Baja `NLM_MAX_CONCURRENCY` y espera unos minutos |
| `read_source` devuelve poco | Es una página: sigue con `next_offset` o usa `read_source_section` |
| Una fuente no aparece en búsquedas locales | Solo se descargan fuentes en estado `ready`; revisa `list_sources` |
| `read_report` dice que el artefacto «no es un informe» | Es un archivo, audio o presentación: solo los de tipo `report` tienen texto. Para fuentes usa `export_markdown`; para notas, `read_note` |
| `get_usage` devuelve `status: disabled` | La API interna de cuota no está habilitada para esa cuenta; el resto de herramientas funciona igual |
| `add_source_file` / `add_source_base64` dice «deshabilitada» | Define `NLM_UPLOAD_DIRS` (lo mismo anuncia `health_check.file_upload_hint`) y reinicia el cliente MCP |
| `text_density_warning` en una subida | Pocas palabras para el tamaño: el material es de imágenes/escaneado y puede quedar sin cubrir. Añade una fuente de texto complementaria |
| Falla tras actualizar `notebooklm-py` | API no oficial: mantén la versión fijada o ajusta `backend.py` |
