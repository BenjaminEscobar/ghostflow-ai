# GhostFlow AI

GhostFlow aprende procesos empresariales observando a una persona trabajar en una aplicación web y los convierte en **workflows estructurados, reutilizables y verificables** que se reproducen con Playwright y sobreviven a ciertos cambios de interfaz.

> Proyecto para el Meta Global AI Developer Hackathon 2026 — categoría Indie Developer, temática *Build for Your World*. Desarrollado desde cero a partir del 26 de octubre de 2026.

## Cómo funciona

```
 Chrome Extension (MV3)          Backend FastAPI                          Navegador Playwright
 ───────────────────────         ───────────────────────────────          ─────────────────────
 Captura semántica      ──POST──► Saneamiento (allowlist, secretos)
 (clics, campos,                   │
  selecciones, navegación,         ▼
  mensajes de estado)             Compilador determinista ──► Workflow DSL (JSON, validado)
                                   │                              │
                                   ▼                              ▼
                                  IA (mock / Muse Spark)         Adaptador "web" ──► Portal demo
                                  interpreta, no ejecuta         guardia de red      + verificación
```

1. La **extensión** graba acciones solo en dominios autorizados. Nunca envía valores de contraseñas ni campos sensibles.
2. El **backend** vuelve a aplicar las reglas (allowlist y redacción), guarda la grabación y la **compila** a un workflow del DSL: los valores tecleados se vuelven *inputs* parametrizables; las contraseñas se vuelven *inputs sensibles* que se piden al ejecutar y nunca se guardan.
3. La **IA** (Meta Model API con `muse-spark-1.3`, o un mock offline) **interpreta** el proceso: propósito de negocio, resultado esperado, precondiciones, descripción y relevancia de cada paso, y qué es cada input (tipo, si es parámetro, constante o credencial). Recibe una vista semántica sin valores y solo puede escribir documentación: no puede cambiar acciones, URLs, inputs ni verificaciones (ver [Interpretación con Meta](#interpretación-con-meta-gf-002)).
4. El **motor Playwright** ejecuta el workflow: cada elemento tiene varias estrategias de localización en orden de prioridad (test id → rol+nombre → etiqueta → placeholder → id → name → texto → ruta CSS). Si la primera falla, usa la siguiente (`resolution: "deterministic_fallback"`, `healed: true`). Si **todas** fallan y el self-healing está activado, Muse Spark elige uno de los candidatos reales de la página y GhostFlow lo valida antes de actuar (`resolution: "ai_recovery"`, ver [Self-healing](#self-healing-semántico-con-meta-gf-003)).
5. Al final **verifica** el resultado (texto de estado visible y ruta final) y bloquea cualquier petición del navegador fuera de la allowlist.

## Estructura

```
backend/            FastAPI + compilador + DSL + motor Playwright + pruebas
  ghostflow/
    api.py          Endpoints REST
    dsl/            Workflow DSL v0.1 (Pydantic) y exportación JSON Schema
    recording/      Modelo de eventos, saneamiento y compilador grabación → workflow
    ai/             Contrato de proveedor, mock, proveedor Meta (Muse Spark), interpretación, CLI de comparación
    execution/      Contrato ExecutionAdapter + adaptador web (Playwright) + self-healing
    evidence.py     Evidencias de demo (GF-004): modelo, saneamiento, importación
    console/        Demo Console: página estática + vistas JSON de solo lectura
    demo_healing.py Runner de la demo A/B/C (modo visual, evidencia en vivo)
    security/       Allowlist de orígenes (anti-SSRF) y redacción de secretos
    storage.py      SQLAlchemy (PostgreSQL / SQLite)
  tests/            Unitarias, API y E2E (marcadas `e2e`)
extension/          Chrome Extension Manifest V3 (sin build)
demo-portal/        Portal ficticio "Andina Servicios" (HTML/JS estático, datos sintéticos)
schemas/            workflow.schema.json generado desde el DSL
infra/              Configuración nginx del portal
docs/               Arquitectura, backlog, runbook/guion/checklist de la demo, ejemplos y evidencias
```

## Requisitos

- Python 3.11+ (probado con 3.14)
- Google Chrome o Chromium para cargar la extensión
- Opcional: Docker Desktop para el stack completo con PostgreSQL

## Instalación y ejecución local (recomendada para la demo)

```bash
make setup        # venv, dependencias y Chromium de Playwright 1.63
make test         # pruebas rápidas (sin navegador)
make test-e2e     # pruebas con navegador real, incluida la extensión
```

En dos terminales:

```bash
make portal       # portal demo en http://localhost:8080
make backend      # API en http://localhost:8010 (SQLite + IA mock); docs en /docs
```

Carga la extensión: `chrome://extensions` → activar *Modo de desarrollador* → *Cargar descomprimida* → carpeta `extension/`.

## Instrucciones de la demostración

1. Abre `http://localhost:8080/index.html`.
2. Abre el popup de **GhostFlow Recorder**, escribe un nombre y pulsa **Iniciar grabación**.
3. En el portal: ingresa con `demo` / `ghostflow-demo` (credenciales ficticias que muestra el propio portal) → **Nueva solicitud** → completa el formulario → **Crear solicitud** → espera el mensaje *Solicitud registrada correctamente*.
4. En el popup pulsa **Detener y enviar**. Verás `Grabación enviada: <id>`.
5. Compila y ejecuta (o usa la UI Swagger en `http://localhost:8010/docs`):

```bash
REC=<id mostrado en el popup>
WF=$(curl -s -X POST localhost:8010/api/recordings/$REC/workflow -H 'content-type: application/json' -d '{}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
curl -s localhost:8010/api/workflows/$WF | python3 -m json.tool          # el workflow DSL
curl -s -X POST localhost:8010/api/workflows/$WF/runs -H 'content-type: application/json' \
     -d '{"inputs": {"contrasena": "ghostflow-demo"}}' | python3 -m json.tool
```

La ejecución devuelve `status: "passed"`, el resultado de cada paso (estrategia usada, `healed`) y de cada verificación. Puedes cambiar los datos sin volver a grabar, por ejemplo `"inputs": {"contrasena": "ghostflow-demo", "nombre_del_cliente": "Luis Rojas"}`.

## Interpretación con Meta (GF-002)

Por defecto el backend usa el proveedor **mock** (offline, determinista, sin costos): toda la suite de pruebas corre así. Para usar **Meta Model API / Muse Spark**:

```bash
# backend/.env (ignorado por Git) o variables exportadas; la clave nunca va a la extensión
GHOSTFLOW_AI_PROVIDER=meta
GHOSTFLOW_META_API_KEY=<tu clave de https://dev.meta.ai>
GHOSTFLOW_META_MAX_REQUESTS=5          # opcional: tope duro para una sesión de validación
```

Al compilar una grabación (`POST /api/recordings/{id}/workflow`) el backend envía **una** petición a `POST https://api.meta.ai/v1/chat/completions` con `response_format` JSON Schema estricto y guarda el resultado en `definition.interpretation` con su procedencia (`provider`, `model`, `generated_at`). `GET /api/health` muestra el proveedor y el modelo activos. Si Meta falla (error HTTP, timeout, salida inválida o tope alcanzado), el workflow se guarda igual, sin interpretación, y la respuesta trae `ai_error` con el motivo.

**Qué ve el modelo** (`ai/annotate.py:annotation_payload`): nombre del proceso, host, rutas y títulos de página, cada paso (acción + descripción del elemento), los inputs con su etiqueta y la **forma** del valor (`email`, `date`, `text (1-3 words)`, `withheld`...), y las verificaciones. Nunca recibe valores tecleados, contraseñas, query strings ni cookies. Los textos de página pasan por `scrub_text` (enmascara correos, números largos y valores con forma de token).

**Qué puede hacer el modelo:** solo rellenar campos de documentación de un esquema cerrado (`extra="forbid"`, longitudes y enums acotados). GhostFlow ignora ids de pasos o inputs desconocidos, limpia saltos de línea y `{{ }}`, vuelve a validar el workflow completo y **compara su parte ejecutable** (`execution_view`) con la del compilador: si cambiara algo, la respuesta se rechaza. El contenido de páginas y grabaciones viaja como dato en el mensaje `user`, nunca como instrucción de sistema.

**Controles de costo y seguridad:** timeout (60 s), `max_completion_tokens` (4000, incluye razonamiento), `reasoning_effort=low`, reintentos solo para 429/500/503 (1 por defecto, backoff ≤ 5 s), tope de peticiones por proceso (`GHOSTFLOW_META_MAX_REQUESTS`, 50 por defecto), payload ≤ 60 000 caracteres y solo HTTPS. Los modelos `-contributor` (Meta puede entrenar con los datos) se rechazan salvo autorización explícita. Los logs `ghostflow.ai` registran proveedor, modelo, estado, latencia y tokens; nunca prompts, respuestas, cuerpos de error ni la clave.

### Comparar mock vs Meta

```bash
cd backend
.venv/bin/python -m ghostflow.ai.compare ../docs/examples/demo-recording.json --out ../gf002-evidence.json
```

Siempre ejecuta el mock; si `GHOSTFLOW_AI_PROVIDER=meta` y hay clave, envía **exactamente una** petición a Meta (sin reintentos) y muestra ambas interpretaciones lado a lado, con tokens usados y costo estimado (precios Standard de `muse-spark-1.3`: USD 1,25 / 4,25 por millón de tokens de entrada / salida). También acepta una grabación real exportada desde `GET /api/recordings/{id}`. La diferencia esperada: el mock repite la interfaz (*"Completar textbox \"Nombre del cliente\""*, sin precondiciones, todo `supporting`), mientras que Muse Spark explica el propósito de negocio, marca los pasos críticos (autenticación, envío), clasifica inputs (`person_name`, `email`, parámetro vs constante) y deja notas de revisión (p. ej. la prioridad fija en la grabación).

## Self-healing semántico con Meta (GF-003)

Cuando **todos** los localizadores grabados de un paso no encuentran nada, GhostFlow puede pedir a Muse Spark que identifique el elemento equivalente en la página actual. Está **desactivado por defecto** (`GHOSTFLOW_HEALING_ENABLED=false`).

```
localizadores grabados → 0 coincidencias ─► ¿elegible? ─► candidatos de la página (script fijo, ≤40, sin valores)
   ─► Muse Spark elige un id ─► validación determinista ─► re-verificación en vivo ─► una sola acción
```

- **Cuándo se activa:** solo `click`, `fill`, `select` y `check`, solo si ningún localizador encontró elementos (si hay varios, es ambigüedad y se falla), nunca para inputs sensibles (credenciales), como máximo una vez por paso y `GHOSTFLOW_HEALING_MAX_ATTEMPTS_PER_RUN` por ejecución. Los errores que no son de localización (timeouts de navegación, verificaciones) nunca activan IA.
- **Qué ve el modelo:** intención del paso y del workflow, el elemento esperado, los localizadores que fallaron, la ruta que el workflow espera a continuación y una lista numerada de candidatos (`c1`, `c2`…) con tipo, rol, nombre visible, región, encabezado, estado habilitado y ruta del enlace. Nunca valores de formularios, contraseñas (los campos `password` ni se listan), cookies, URLs completas ni query strings. Los textos de la página se tratan como datos no confiables y pasan por `scrub_text`.
- **Qué puede responder:** `candidate_id` (vacío si no hay equivalente), `confidence` (`high`/`medium`/`low`/`none`), `justification` y `rejection_reason`. No existe ningún campo para selectores, XPath, scripts ni URLs; los campos extra se rechazan.
- **Validación determinista (autoridad final):** el id existe; confianza ≥ `GHOSTFLOW_HEALING_MIN_CONFIDENCE` (`high` por defecto); tipo de elemento compatible con la acción; mismo rol ARIA que el grabado; visible y habilitado; no hay otro candidato indistinguible; si es un enlace: destino dentro de `allowed_origins`, sin nueva pestaña ni descarga, y **la misma ruta que el workflow espera a continuación**; política de efectos secundarios; y re-verificación en vivo (sigue conectado, visible, habilitado y con el mismo destino).
- **Efectos secundarios:** botones, envíos y controles desconocidos no se recuperan (ni siquiera se ofrecen al modelo) salvo `GHOSTFLOW_HEALING_ALLOW_SIDE_EFFECTS=true`, y aun así nunca después de un envío anterior cuyo resultado no confirmó una navegación posterior (sin duplicar operaciones).
- **El DSL no cambia:** la recuperación solo vale para esa ejecución; el workflow guardado no se modifica (no hay aprendizaje automático de selectores).
- **Costo:** usa el mismo proveedor que GF-002, con el mismo tope de peticiones (`GHOSTFLOW_META_MAX_REQUESTS`, contando reintentos), timeout y límite de tokens. Con la variante demo, una recuperación envía ~1 K tokens.

**Evidencia en cada ejecución** (`result.steps[*]`, compatible con GF-001/002):

| Campo | Significado |
|---|---|
| `resolution` | `primary`, `deterministic_fallback` (otro localizador grabado) o `ai_recovery` |
| `healed` | Igual que en GF-001: se usó un localizador grabado alternativo. **Nunca** indica recuperación por IA |
| `recovery` | Intento de self-healing: `exhausted_strategies`, `consulted_model`, `provider`, `model`, `latency_ms`, tokens, `candidates_considered`, `proposed` (id, tipo, rol, nombre, ruta), `confidence`, `justification`, `validation[]` (cada regla con `passed`), `outcome` (`recovered`/`rejected`/`skipped`/`error`) y `reason` |

Los logs `ghostflow.healing` registran paso, resultado, proveedor, modelo, id del candidato, confianza, latencia y tokens (sin nombres de la página, valores ni la clave).

### Demostración A / B / C

La variante `demo-portal/variants/crear-requerimiento/` cambia **Nueva solicitud** por **Crear requerimiento** (otro test id, id, texto y estructura HTML; mismo destino). Los cuatro localizadores grabados de ese paso fallan.

```bash
# Detén `make portal` si está corriendo: la demo usa http://localhost:8080 (origen de la grabación).
make demo-healing        # A, B y C con el mock offline (0 solicitudes a Meta)
make demo-healing-meta   # A y B igual; C consulta a Muse Spark (máximo 1 solicitud)
```

Ambas compilan **una vez** `docs/examples/demo-recording.json` y ejecutan el mismo workflow (su SHA-256 se imprime antes y después): **A** pasa con los localizadores grabados; **B** falla en `s6` con `recovery.outcome: "skipped"`; **C** recupera `s6` con `resolution: "ai_recovery"` y completa las verificaciones. Opciones: `--headed --delay-ms 800` para filmarla, `--only C`.

Con la extensión y la API: graba y compila contra `make portal`; luego detén el portal y ejecuta `make portal-variant` (mismo origen); reinicia el backend con `GHOSTFLOW_HEALING_ENABLED=true` (y `GHOSTFLOW_AI_PROVIDER=meta` para Muse Spark) y vuelve a lanzar `POST /api/workflows/{id}/runs`.

## Demo Console (GF-004)

Página web de solo lectura en `http://127.0.0.1:8010/console/` (`make console`), con dos vistas. La **vista Demo**, minimalista y pensada para presentar, incluye: Learn / Understand / Recover, escenarios A/B/C, el flujo de ejecución, un navegador reconstruido a partir de la evidencia (no es una captura), un resumen de self-healing y la mascota de GhostFlow, que reacciona al estado. La **vista Developer** (`?view=developer`) conserva toda la auditoría. En conjunto, la consola muestra el workflow, la ejecución paso a paso (Pending / Running / Completed / Failed / Recovered), la interpretación de Muse Spark, el panel de self-healing (elemento original → elemento nuevo, localizadores agotados, candidatos, justificación, confianza y validaciones) y la evidencia verificable (resultado, verificaciones, proveedor, latencia, tokens y SHA-256 del DSL antes y después).

```bash
make console          # terminal 1: API + consola
make demo-rehearsal   # terminal 2: A/B/C en navegador visible, mock (SIMULADO, 0 solicitudes a Meta)
make demo-live        # A/B/C con Meta real en C: máx. 1 solicitud, pide escribir "meta" para confirmar
```

- **Fuentes:** ejecuciones del runner (`demo-evidence/`, ignorado por Git, en vivo mientras se escriben), evidencia versionada (`docs/evidence/`, p. ej. la validación real de GF-003 con Muse Spark) y los workflows y ejecuciones guardados en el backend.
- **Sin ambigüedad:** cada vista indica *Live*, *Saved run*, *Recorded evidence* o *Stored runs*, y si la IA fue *Real Meta Model API* o *Simulated (mock)*. Una evidencia guardada nunca se presenta como ejecución en vivo; el *Replay* lleva una franja que lo dice.
- **Seguridad:** la consola solo hace `GET` a `/api/demo/*`, construye el DOM con `textContent` (sin HTML de las evidencias), tiene una CSP estricta sin recursos externos y vuelve a sanear cada evidencia al leerla.
- **Modo visual:** `GHOSTFLOW_STEP_DELAY_MS` y `GHOSTFLOW_FINAL_HOLD_MS` (o `--delay-ms` / `--hold-ms`). Ambos valen 0 por defecto.

Guía completa: [`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md) · guion: [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) · entrega: [`docs/SUBMISSION_CHECKLIST.md`](docs/SUBMISSION_CHECKLIST.md).

## Stack completo con Docker

```bash
cp .env.example .env    # define POSTGRES_PASSWORD
make up                 # PostgreSQL + portal (8080) + backend (8000)
```

El backend comparte el espacio de red del contenedor del portal, de modo que `http://localhost:8080` significa lo mismo para el navegador del host (donde graba la extensión) y para el Chromium del backend.

## API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/health` | Estado y proveedor de IA activo |
| GET | `/api/schema/workflow` | JSON Schema del DSL |
| POST | `/api/recordings` | Recibe una grabación (se sanea antes de guardar) |
| GET | `/api/recordings`, `/api/recordings/{id}` | Consulta |
| POST | `/api/recordings/{id}/workflow` | Compila (y anota con IA) un workflow |
| POST | `/api/workflows` | Crea un workflow desde JSON del DSL (validado) |
| GET | `/api/workflows`, `/api/workflows/{id}` | Consulta |
| POST | `/api/workflows/{id}/runs` | Ejecuta con Playwright y verifica |
| GET | `/api/workflows/{id}/runs`, `/api/runs/{id}` | Historial de ejecuciones |
| GET | `/api/demo/sources` | Evidencias de demo y workflows disponibles para la consola (solo lectura) |
| GET | `/api/demo/sources/{recorded\|runs\|workflows}/{nombre}` | Una evidencia normalizada y sus etiquetas (en vivo, guardada, real o simulada) |
| GET | `/console/` | Demo Console (estática) |

## Configuración

Variables `GHOSTFLOW_*` (ver `backend/ghostflow/config.py`):

| Variable | Defecto | Uso |
|---|---|---|
| `GHOSTFLOW_DATABASE_URL` | `sqlite:///./ghostflow.db` | PostgreSQL en Compose |
| `GHOSTFLOW_ALLOWED_ORIGINS` | `http://localhost:8080,http://127.0.0.1:8080` | Únicos orígenes grabables/ejecutables |
| `GHOSTFLOW_AI_PROVIDER` | `mock` | `meta` requiere `GHOSTFLOW_META_API_KEY` |
| `GHOSTFLOW_META_API_KEY` | — | Solo backend; nunca en Git ni en la extensión |
| `GHOSTFLOW_META_API_BASE_URL` | `https://api.meta.ai/v1` | Solo HTTPS |
| `GHOSTFLOW_META_MODEL` | `muse-spark-1.3` | Modo Standard (sin entrenamiento con los datos) |
| `GHOSTFLOW_META_TIMEOUT_S` | `60` | Timeout por petición |
| `GHOSTFLOW_META_MAX_OUTPUT_TOKENS` | `4000` | `max_completion_tokens` (razonamiento + salida) |
| `GHOSTFLOW_META_REASONING_EFFORT` | `low` | `minimal`, `low`, `medium`, `high` |
| `GHOSTFLOW_META_MAX_RETRIES` | `1` | Solo 429/500/503 |
| `GHOSTFLOW_META_MAX_REQUESTS` | `50` | Tope duro por proceso del backend |
| `GHOSTFLOW_META_ALLOW_CONTRIBUTOR_MODE` | `false` | `true` solo con autorización explícita |
| `GHOSTFLOW_HEALING_ENABLED` | `false` | Self-healing semántico (GF-003) |
| `GHOSTFLOW_HEALING_MIN_CONFIDENCE` | `high` | `high` o `medium` |
| `GHOSTFLOW_HEALING_ALLOW_SIDE_EFFECTS` | `false` | Permite recuperar botones/envíos (nunca tras un envío no confirmado) |
| `GHOSTFLOW_HEALING_MAX_CANDIDATES` | `40` | Candidatos extraídos por intento (5–100) |
| `GHOSTFLOW_HEALING_MAX_ATTEMPTS_PER_RUN` | `2` | Intentos por ejecución (uno por paso como máximo) |
| `GHOSTFLOW_STEP_DELAY_MS` | `0` | Pausa visual entre pasos para demos |
| `GHOSTFLOW_FINAL_HOLD_MS` | `0` | Mantiene visible la pantalla final antes de cerrar el navegador (máx. 30000) |
| `GHOSTFLOW_DEMO_EVIDENCE_DIR` | `../demo-evidence` | Evidencias que escribe el runner de la demo (relativo a `backend/`) |
| `GHOSTFLOW_DEMO_RECORDED_DIR` | `../docs/evidence` | Evidencias versionadas que muestra la consola |
| `GHOSTFLOW_STEP_TIMEOUT_MS` | `10000` | Tiempo máximo por paso |
| `GHOSTFLOW_BROWSER_HEADLESS` | `true` | `false` para ver la reproducción |
| `GHOSTFLOW_MAX_CONCURRENT_RUNS` | `2` | Límite de ejecuciones simultáneas |

Para autorizar otro dominio hay que cambiarlo en tres lugares, a propósito: `GHOSTFLOW_ALLOWED_ORIGINS`, `extension/config.js` y `extension/manifest.json` (`matches` y `host_permissions`). `test_extension_static.py` comprueba que la extensión quede coherente.

## Seguridad

- **Sin código arbitrario:** el DSL es un conjunto cerrado de acciones (`navigate`, `click`, `fill`, `select`, `check`, `press`, `wait_for_url`) con `extra="forbid"`. No existe `evaluate`; los selectores CSS se fuerzan con prefijo `css=`; las plantillas solo admiten `{{inputs.nombre}}` y no se evalúan.
- **IA separada del motor:** el modelo solo devuelve documentación validada contra un esquema cerrado; el documento resultante se vuelve a validar completo y su parte ejecutable debe ser idéntica a la del compilador. El modelo no recibe valores de inputs y el contenido de las páginas se le entrega como dato no confiable.
- **Dominios autorizados:** allowlist por origen exacto (esquema, host y puerto) aplicada en la extensión, al recibir grabaciones, al guardar workflows y en **cada petición** del navegador de ejecución (`context.route`). Los endpoints de metadatos cloud (`169.254.169.254`, etc.) y las IP link-local se bloquean siempre.
- **Sin secretos registrados:** campos `password`, `autocomplete` sensibles, nombres como *token/contraseña/api key/tarjeta* y valores con forma de JWT o token se registran sin valor, en la extensión y otra vez en el servidor. Los inputs sensibles no admiten valor por defecto, se piden en cada ejecución, se guardan como `***` y se eliminan de los mensajes de error.
- **Self-healing acotado:** el modelo solo elige un id entre candidatos extraídos por un script fijo de GhostFlow; el ejecutor valida origen, destino, rol, unicidad, visibilidad y efectos secundarios antes de actuar una sola vez. Nunca redirige credenciales ni repite un envío incierto.
- **Pruebas sin red:** `tests/conftest.py` ignora `.env`/`GHOSTFLOW_*` y bloquea cualquier petición httpx real, así que la suite nunca consume Meta aunque exista una clave local.
- **Sin credenciales en Git:** `.env` está ignorado; el único "secreto" del repo es la contraseña ficticia del portal demo, visible en su propia pantalla de ingreso.

## Limitaciones conocidas

- Solo aplicaciones web multipágina simples; la navegación SPA (`history.pushState`) no se captura como paso.
- Sin soporte de iframes, shadow DOM, carga de archivos, drag & drop, doble clic ni teclas distintas de Enter.
- La grabación es global para todas las pestañas de dominios autorizados (no por pestaña).
- La verificación automática se infiere del último mensaje de estado (`role=status/alert/aria-live`) y de la ruta final; no hay aserciones definidas por el usuario.
- Las ejecuciones son síncronas dentro de la petición HTTP (sin cola).
- Sin autenticación en la API: pensada para uso local. No exponer públicamente.
- Las tablas se crean al iniciar; no hay migraciones (Alembic en backlog).
- Demo Console: solo lectura, con *polling* (sin WebSocket); la evidencia importada de GF-003 no incluye la lista de candidatos ni los tiempos por paso (se indica en pantalla).
- Self-healing: solo marco principal (sin iframes ni shadow DOM), solo elementos visibles e interactivos estándar, y sin aprendizaje persistente; un rediseño que cambie el destino de un enlace no se recupera (la regla de destino esperado lo impide a propósito).
- La validación contra la API real de Meta depende de una clave configurada por el propietario (ver informe de GF-002).
- La interpretación con Meta es una sola llamada síncrona dentro de la compilación (hasta el timeout); no hay re-interpretación de workflows existentes.
- El costo por llamada es una estimación local a partir de `usage`; la facturación de Meta es la referencia.
- La allowlist es por nombre de host: no protege contra DNS rebinding de un dominio autorizado.

Más detalles en [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) y mejoras pendientes en [`docs/BACKLOG.md`](docs/BACKLOG.md).
