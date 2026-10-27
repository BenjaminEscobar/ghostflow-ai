# GhostFlow AI

GhostFlow aprende procesos empresariales observando a una persona trabajar en una aplicación web y los convierte en **workflows estructurados, reutilizables y verificables** que se reproducen con Playwright y sobreviven a ciertos cambios de interfaz.

> Proyecto para el Meta Global AI Developer Hackathon 2026 — categoría Indie Developer, temática *Build for Your World*. Desarrollado desde cero a partir del 26 de octubre de 2026.

## Cómo funciona (GF-001)

```
 Chrome Extension (MV3)          Backend FastAPI                          Navegador Playwright
 ───────────────────────         ───────────────────────────────          ─────────────────────
 Captura semántica      ──POST──► Saneamiento (allowlist, secretos)
 (clics, campos,                   │
  selecciones, navegación,         ▼
  mensajes de estado)             Compilador determinista ──► Workflow DSL (JSON, validado)
                                   │                              │
                                   ▼                              ▼
                                  IA (mock / Meta Model API)     Adaptador "web" ──► Portal demo
                                  solo anota metadatos           guardia de red      + verificación
```

1. La **extensión** graba acciones solo en dominios autorizados. Nunca envía valores de contraseñas ni campos sensibles.
2. El **backend** vuelve a aplicar las reglas (allowlist y redacción), guarda la grabación y la **compila** a un workflow del DSL: los valores tecleados se vuelven *inputs* parametrizables; las contraseñas se vuelven *inputs sensibles* que se piden al ejecutar y nunca se guardan.
3. La **IA** (por ahora un mock; Meta Model API preparado) solo propone nombre, descripción y etiquetas. No puede cambiar acciones, URLs ni verificaciones.
4. El **motor Playwright** ejecuta el workflow: cada elemento tiene varias estrategias de localización en orden de prioridad (test id → rol+nombre → etiqueta → placeholder → id → name → texto → ruta CSS). Si la primera falla, usa la siguiente y lo marca como `healed`.
5. Al final **verifica** el resultado (texto de estado visible y ruta final) y bloquea cualquier petición del navegador fuera de la allowlist.

## Estructura

```
backend/            FastAPI + compilador + DSL + motor Playwright + pruebas
  ghostflow/
    api.py          Endpoints REST
    dsl/            Workflow DSL v0.1 (Pydantic) y exportación JSON Schema
    recording/      Modelo de eventos, saneamiento y compilador grabación → workflow
    ai/             Contrato de proveedor, mock, adaptador Meta Model API, anotación
    execution/      Contrato ExecutionAdapter + adaptador web (Playwright)
    security/       Allowlist de orígenes (anti-SSRF) y redacción de secretos
    storage.py      SQLAlchemy (PostgreSQL / SQLite)
  tests/            Unitarias, API y E2E (marcadas `e2e`)
extension/          Chrome Extension Manifest V3 (sin build)
demo-portal/        Portal ficticio "Andina Servicios" (HTML/JS estático, datos sintéticos)
schemas/            workflow.schema.json generado desde el DSL
infra/              Configuración nginx del portal
docs/               Arquitectura y backlog
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
make backend      # API en http://localhost:8000 (SQLite + IA mock); docs en /docs
```

Carga la extensión: `chrome://extensions` → activar *Modo de desarrollador* → *Cargar descomprimida* → carpeta `extension/`.

## Instrucciones de la demostración

1. Abre `http://localhost:8080/index.html`.
2. Abre el popup de **GhostFlow Recorder**, escribe un nombre y pulsa **Iniciar grabación**.
3. En el portal: ingresa con `demo` / `ghostflow-demo` (credenciales ficticias que muestra el propio portal) → **Nueva solicitud** → completa el formulario → **Crear solicitud** → espera el mensaje *Solicitud registrada correctamente*.
4. En el popup pulsa **Detener y enviar**. Verás `Grabación enviada: <id>`.
5. Compila y ejecuta (o usa la UI Swagger en `http://localhost:8000/docs`):

```bash
REC=<id mostrado en el popup>
WF=$(curl -s -X POST localhost:8000/api/recordings/$REC/workflow -H 'content-type: application/json' -d '{}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
curl -s localhost:8000/api/workflows/$WF | python3 -m json.tool          # el workflow DSL
curl -s -X POST localhost:8000/api/workflows/$WF/runs -H 'content-type: application/json' \
     -d '{"inputs": {"contrasena": "ghostflow-demo"}}' | python3 -m json.tool
```

La ejecución devuelve `status: "passed"`, el resultado de cada paso (estrategia usada, `healed`) y de cada verificación. Puedes cambiar los datos sin volver a grabar, por ejemplo `"inputs": {"contrasena": "ghostflow-demo", "nombre_del_cliente": "Luis Rojas"}`.

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

## Configuración

Variables `GHOSTFLOW_*` (ver `backend/ghostflow/config.py`):

| Variable | Defecto | Uso |
|---|---|---|
| `GHOSTFLOW_DATABASE_URL` | `sqlite:///./ghostflow.db` | PostgreSQL en Compose |
| `GHOSTFLOW_ALLOWED_ORIGINS` | `http://localhost:8080,http://127.0.0.1:8080` | Únicos orígenes grabables/ejecutables |
| `GHOSTFLOW_AI_PROVIDER` | `mock` | `meta` requiere `GHOSTFLOW_META_API_KEY` |
| `GHOSTFLOW_STEP_TIMEOUT_MS` | `10000` | Tiempo máximo por paso |
| `GHOSTFLOW_BROWSER_HEADLESS` | `true` | `false` para ver la reproducción |
| `GHOSTFLOW_MAX_CONCURRENT_RUNS` | `2` | Límite de ejecuciones simultáneas |

Para autorizar otro dominio hay que cambiarlo en tres lugares, a propósito: `GHOSTFLOW_ALLOWED_ORIGINS`, `extension/config.js` y `extension/manifest.json` (`matches` y `host_permissions`). `test_extension_static.py` comprueba que la extensión quede coherente.

## Seguridad

- **Sin código arbitrario:** el DSL es un conjunto cerrado de acciones (`navigate`, `click`, `fill`, `select`, `check`, `press`, `wait_for_url`) con `extra="forbid"`. No existe `evaluate`; los selectores CSS se fuerzan con prefijo `css=`; las plantillas solo admiten `{{inputs.nombre}}` y no se evalúan.
- **IA separada del motor:** el modelo solo devuelve metadatos validados contra un esquema; el documento resultante se vuelve a validar completo. El modelo no recibe valores de inputs.
- **Dominios autorizados:** allowlist por origen exacto (esquema, host y puerto) aplicada en la extensión, al recibir grabaciones, al guardar workflows y en **cada petición** del navegador de ejecución (`context.route`). Los endpoints de metadatos cloud (`169.254.169.254`, etc.) y las IP link-local se bloquean siempre.
- **Sin secretos registrados:** campos `password`, `autocomplete` sensibles, nombres como *token/contraseña/api key/tarjeta* y valores con forma de JWT o token se registran sin valor, en la extensión y otra vez en el servidor. Los inputs sensibles no admiten valor por defecto, se piden en cada ejecución, se guardan como `***` y se eliminan de los mensajes de error.
- **Sin credenciales en Git:** `.env` está ignorado; el único "secreto" del repo es la contraseña ficticia del portal demo, visible en su propia pantalla de ingreso.

## Limitaciones conocidas

- Solo aplicaciones web multipágina simples; la navegación SPA (`history.pushState`) no se captura como paso.
- Sin soporte de iframes, shadow DOM, carga de archivos, drag & drop, doble clic ni teclas distintas de Enter.
- La grabación es global para todas las pestañas de dominios autorizados (no por pestaña).
- La verificación automática se infiere del último mensaje de estado (`role=status/alert/aria-live`) y de la ruta final; no hay aserciones definidas por el usuario.
- Las ejecuciones son síncronas dentro de la petición HTTP (sin cola).
- Sin autenticación en la API: pensada para uso local. No exponer públicamente.
- Las tablas se crean al iniciar; no hay migraciones (Alembic en backlog).
- El adaptador Meta Model API está probado solo contra respuestas simuladas; endpoint y modelo deben confirmarse antes de activarlo.
- La allowlist es por nombre de host: no protege contra DNS rebinding de un dominio autorizado.

Más detalles en [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) y mejoras pendientes en [`docs/BACKLOG.md`](docs/BACKLOG.md).
