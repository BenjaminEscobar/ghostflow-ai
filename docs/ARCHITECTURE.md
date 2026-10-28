# Arquitectura — GF-001 / GF-002 / GF-003 / GF-004 / GF-005 / GF-006

## Principios

1. **La IA propone, el núcleo decide.** Ningún dato del modelo se ejecuta. El modelo devuelve JSON validado contra un esquema Pydantic y el llamador decide qué campos aplicar (hoy: solo texto descriptivo y el bloque `interpretation`, que ningún adaptador lee).
2. **Contratos cerrados.** Grabaciones, workflows y resultados son modelos Pydantic con `extra="forbid"`. El JSON Schema del DSL se genera desde el código (`schemas/workflow.schema.json`) y una prueba garantiza que esté actualizado.
3. **Una sola allowlist.** `security/urls.py` define qué orígenes existen para GhostFlow. Se aplica en la extensión, en la ingesta, al guardar workflows y en la red del navegador de ejecución.
4. **Monolito modular.** Un único servicio FastAPI con módulos internos; sin colas ni microservicios hasta que hagan falta.

## Módulos del backend

| Módulo | Responsabilidad | Depende de |
|---|---|---|
| `security` | Allowlist de orígenes, bloqueo de metadatos cloud, redacción | — |
| `dsl` | Workflow DSL v0.1, plantillas `{{inputs.x}}`, JSON Schema | `security` |
| `recording` | Eventos de la extensión, saneamiento, compilador determinista | `dsl`, `security` |
| `ai` | `ModelProvider` (mock / Meta Muse Spark), tarea de interpretación, CLI de comparación | `dsl`, `recording` (modelos), `security` |
| `execution` | `ExecutionAdapter` + adaptador `web` (Playwright), self-healing (`recovery.py`) | `dsl`, `security` |
| `storage` | Tablas `recordings`, `workflows`, `runs` (documentos JSON) | — |
| `evidence` | Evidencias de demo: modelo, construcción desde eventos y `RunResult`, saneamiento, etiquetas real/simulado y vivo/guardado | `dsl`, `execution` (modelos), `security` |
| `console` | Demo Console: estáticos + `GET /api/demo/*` de solo lectura | `evidence`, `storage` |
| `api` | Endpoints y composición (`create_app`) | todos |

`execution` no importa `ai`: define el contrato `RecoveryAdvisor` (datos de entrada y salida) y `ai/recovery.py` lo implementa sobre cualquier `ModelProvider`. La API los conecta. La IA nunca toca el motor ni el navegador.

## Workflow DSL v0.1

```json
{
  "dsl_version": "0.1",
  "name": "Registrar solicitud de cliente",
  "adapter": "web",
  "allowed_origins": ["http://localhost:8080"],
  "inputs": [
    {"name": "usuario", "label": "Usuario", "sensitive": false, "default": "demo"},
    {"name": "contrasena", "label": "Contraseña", "sensitive": true, "default": null}
  ],
  "steps": [
    {"id": "s1", "action": "navigate", "url": "http://localhost:8080/index.html"},
    {"id": "s2", "action": "fill", "value": "{{inputs.usuario}}",
     "target": {"description": "textbox \"Usuario\"", "strategies": [
        {"by": "test_id", "value": "login-username"},
        {"by": "role", "role": "textbox", "name": "Usuario"},
        {"by": "label", "value": "Usuario"},
        {"by": "css", "value": "#username"}]}},
    {"id": "s4", "action": "click", "target": {"strategies": [{"by": "role", "role": "button", "name": "Ingresar"}]}},
    {"id": "s5", "action": "wait_for_url", "path": "/dashboard.html"}
  ],
  "verify": [
    {"type": "text_visible", "text": "Solicitud registrada correctamente"},
    {"type": "url_path", "path": "/confirmation.html"}
  ]
}
```

Acciones: `navigate`, `click`, `fill`, `select`, `check`, `press` (Enter/Tab/Escape), `wait_for_url`.
Verificaciones: `text_visible`, `url_path`, `element_text`.
Estrategias: `test_id`, `role`, `label`, `placeholder`, `text`, `css`.

Reglas validadas: ids de paso únicos, inputs declarados, URLs dentro de `allowed_origins`, sin plantillas en URLs, inputs sensibles sin valor por defecto, máximo 200 pasos.

## Compilación grabación → workflow

- La primera navegación es `navigate`. Una navegación ocurrida ≤ 5 s después de una acción del usuario se considera causada por ella y se convierte en `wait_for_url`; si no, en `navigate`.
- Cada valor tecleado/seleccionado se convierte en un input con valor por defecto (el grabado). Los sensibles quedan sin valor.
- Ediciones repetidas del mismo campo se fusionan.
- Verificación: el último mensaje de estado observado tras la última acción (`text_visible`) y la ruta final (`url_path`).

## Ejecución y resiliencia

Para cada elemento, el adaptador web prueba las estrategias en orden y usa la primera que identifica **exactamente un** elemento (si hay varios, filtra por visibles). Si no es la primera, el paso queda marcado `healed: true` con la estrategia usada. Esto cubre: ids renombrados, test ids eliminados, cambios de estructura del DOM. No cubre: cambios de texto visible y de etiqueta a la vez, rediseños de flujo.

Cada ejecución usa un contexto de navegador nuevo, con service workers bloqueados, descargas deshabilitadas y `context.route("**/*")` filtrando por la allowlist del workflow (subconjunto de la del servidor).

## Puntos de extensión

- **Windows Adapter / API Adapter:** implementar `ExecutionAdapter` (`execution/base.py`), registrarlo en `default_adapters` bajo un nuevo valor de `Workflow.adapter` y añadir sus acciones al DSL como variantes nuevas de la unión discriminada. El API, el almacenamiento y la IA no cambian.
- **Meta AI:** nuevas tareas (p. ej. proponer verificaciones, explicar fallos) siguen el patrón de `ai/annotate.py`: payload reducido → respuesta validada → aplicación selectiva → revalidación del workflow completo → comparación de `execution_view`.

## Interpretación con Meta (GF-002)

```
Grabación saneada ──► compilador ──► Workflow (autoridad) ───────────────┐
        │                                │                                │ execution_view idéntica
        └─ títulos de página ──► annotation_payload (sin valores,         ▼
                                  textos con scrub_text) ──► MetaModelProvider ──► WorkflowAnnotation
                                                              (strict JSON Schema)   (extra="forbid")
                                                                                       │
                                     Workflow + interpretation (provider, model) ◄── apply_annotation
```

- **Protocolo:** Chat Completions de Meta Model API (`POST https://api.meta.ai/v1/chat/completions`), modelo `muse-spark-1.3` en modo Standard. `response_format.json_schema.strict=true`; `strict_json_schema` adapta el esquema Pydantic a las reglas del modo estricto (todo `required`, `additionalProperties: false`). La respuesta se vuelve a validar con Pydantic.
- **Mensajes:** el sistema lleva solo nuestra instrucción fija; la grabación viaja como JSON en el mensaje `user` y la instrucción declara que es dato no confiable.
- **Salida aplicada:** nombre, descripción, descripción por paso y etiqueta por input en sus campos del DSL, y el resto (propósito, resultado esperado, precondiciones, explicación y relevancia por paso, tipo y rol por input, notas de revisión) en `Workflow.interpretation`, que el validador del DSL ata a ids y nombres existentes.
- **Lo que el modelo no puede tocar:** acciones, objetivos y estrategias, valores/plantillas, URLs, `allowed_origins`, inputs (nombres, `sensitive`, `default`) y verificaciones. `apply_annotation` lo garantiza comparando `execution_view` antes y después.
- **Fallos:** cualquier error (HTTP, timeout, truncado, contrato, tope de peticiones) se convierte en `ModelProviderError`; la API guarda el workflow determinista y devuelve `ai_error`.
- **Proveedor elegible:** `GHOSTFLOW_AI_PROVIDER=mock|meta`. Las pruebas inyectan `httpx.MockTransport`; ninguna prueba sale a la red.

## Self-healing semántico (GF-003)

```
PlaywrightWebAdapter._run_step
  └─ _resolve(target) ── 0 coincidencias en todas las estrategias ──► ElementNotFound(tried, ambiguous)
        └─ Healer.attempt  (execution/recovery.py — autoridad)
             1. elegibilidad: habilitado, acción click/fill/select/check, no ambiguo, sin input sensible,
                presupuesto por ejecución
             2. PlaywrightCandidateSource.collect: COLLECT_JS/DESCRIBE_JS (constantes; sin valores ni
                campos password; ≤ max_candidates) → Candidate(c1..cN) + ElementHandle internos
             3. filtro por compatibilidad de acción y política de efectos secundarios
             4. RecoveryAdvisor.propose  (ai/recovery.py → ModelProvider: Meta o mock)
                  payload: intención, localizadores fallidos, ruta esperada, candidatos públicos
                  respuesta: RecoveryProposal {candidate_id, confidence, justification, rejection_reason}
             5. validación determinista (cada regla queda en `validation[]`)
             6. re-verificación en vivo del ElementHandle → una sola acción
        └─ StepResult.resolution = "ai_recovery", recovery = RecoveryAttempt (auditoría)
```

- **Separación:** el ejecutor nunca recibe del modelo nada ejecutable: solo un id que él mismo emitió. Los scripts de inspección son constantes del código fuente (`COLLECT_JS`, `DESCRIBE_JS`, `LIVE_JS`, `SUBMITS_JS`) y solo reciben un número.
- **Destino esperado:** si el paso siguiente es `wait_for_url`, un enlace recuperado debe llevar exactamente a esa ruta. Es la evidencia determinista más fuerte de equivalencia y bloquea elecciones manipuladas por la página.
- **Duplicados:** el adaptador recuerda si un clic o Enter fue de tipo envío (`SUBMITS_JS`) y lo da por confirmado solo cuando un `wait_for_url`/`navigate` posterior pasa. Mientras no esté confirmado, ningún control con efectos secundarios se recupera.
- **Compatibilidad:** `healed` conserva su significado de GF-001 (localizador grabado alternativo); `resolution` y `recovery` son campos nuevos opcionales del resultado JSON (sin migraciones).
- **Proveedor offline:** el mock solo recupera cuando hay un único enlace a la ruta esperada y lo declara como `provider: "mock"`; la evidencia nunca se atribuye a Meta si no hubo llamada real.

## Demo Console (GF-004)

```
 demo_healing (runner)                       backend FastAPI (make console)          navegador
 ─────────────────────                       ──────────────────────────────          ─────────
 PlaywrightWebAdapter.execute(observer=…)
   step_started / recovery_started /
   step_finished / verification_finished
        │ apply_event / finish_scenario
        ▼
 demo-evidence/<run>.json  (escritura atómica) ──► EvidenceStore ──► GET /api/demo/sources ──► /console/
 docs/evidence/*.json      (versionadas)      ──►  (re-saneo)    ──► GET /api/demo/sources/… (polling ≈1 s)
 tablas workflows/runs                          ──► bundle_from_runs (solo lectura)
```

- **Observador pasivo:** el adaptador emite eventos *después* de cada decisión. Los errores del observador se registran y se ignoran, de modo que no cambian el resultado, no repiten acciones ni añaden reintentos. `recovery_started` solo se emite si el self-healing está activado.
- **Modo visual:** `step_delay_ms` (entre pasos) y `final_hold_ms` (antes de cerrar el navegador, acotado a 30 s) valen 0 por defecto. La semántica de los pasos no cambia.
- **Evidencia como contrato:** `EvidenceBundle` (`ghostflow.demo-evidence/1`, `extra="forbid"`). El `RunResult` final es autoritativo sobre los eventos. Los valores sensibles se guardan como `***` y los textos libres pasan por `mask_secrets` al escribir y al leer.
- **Real frente a simulado:** `ai_badge` decide la etiqueta con lo que consta en la evidencia (`provider` y `requests_sent`), no con la configuración. `presentation_mode` nunca devuelve `live` para `docs/evidence` ni para un archivo que dejó de actualizarse (más de 20 s → `stale`).
- **Importación GF-003:** `import-gf003` exige el mismo SHA-256 del workflow, marca los estados derivados (`derived_steps`) y deja la procedencia en `provenance`.
- **Frontend sin build:** HTML, CSS y JS sin dependencias, servidos por FastAPI. El DOM se construye con `textContent`; la CSP es `default-src 'none'`, solo `'self'` para scripts, estilos y `connect`, y no hay recursos externos.
- **Auditoría ampliada:** `RecoveryAttempt.candidates` guarda el resumen público de los candidatos que vio el modelo (id, tipo, rol, nombre saneado y ruta), sin URLs completas ni valores. Es el único cambio en el modelo de GF-003 y es aditivo: las reglas de recuperación no cambiaron.
- **Vistas (GF-005):** la vista Demo y la vista Developer comparten los mismos datos y el mismo `render()`; solo cambia qué se muestra. El navegador de la vista Demo se reconstruye con `StepView.location` (dirección derivada de los pasos `navigate` y `wait_for_url`) y con la acción del paso; no es una captura. La mascota es un SVG en línea con estados de ánimo controlados por CSS (`data-mood`). No hay dependencias nuevas ni fuentes externas, y el tema es claro, con azul `#1877F2` y contraste AA verificado en las pruebas.

## Navegador integrado y Ghost 2.0 (GF-006)

```
 PlaywrightWebAdapter.execute(observer=…, capture=CaptureRecorder)
   snap() en 4 puntos ──► CaptureRecorder.take(page, step_id, phase, target)
        │                    ├─ privacidad ANTES de la imagen (paso sensible, página contaminada, origen)
        │                    ├─ page.screenshot(viewport, JPEG, mask=[password, cc-*])
        │                    └─ demo-evidence/captures/<bundle>/<id>.jpg   (ignorado por Git)
        ▼
 notify("capture", metadatos) ──► apply_event ──► ScenarioView.captures (≤ 80)
                                                     │
 consola (polling 900 ms del bundle) ◄───────────────┘
   <img src="/api/demo/captures/<bundle>/<id>">  ◄── GET de solo lectura: solo ids listados y no omitidos
```

- **Capturas por evento, sin streaming ni iframe.** Son imágenes JPEG del viewport de la página de Chromium que controla GhostFlow (`page.screenshot`), nunca de otras pestañas, ventanas ni del sistema operativo. No hay sesión remota: la consola no puede controlar ni inspeccionar el navegador.
- **Puntos de captura** (`phase`):
  - `after`: tras `navigate` / `wait_for_url` (página cargada);
  - `before`: tras resolver el elemento y justo antes de actuar, con la caja normalizada (0..1) del elemento usado;
  - `not_found`: cuando todos los localizadores grabados fallaron, antes de `recovery_started`. El healer ejecuta su candidato validado sin pasar por el adaptador, así que un paso recuperado no tiene imagen `before`, y no se fabrica;
  - `final`: después de las verificaciones (`step_id = null`).
- **Observación desacoplada:** la captura no desplaza la página, no cambia acciones ni reintentos, y conserva el caret. Solo lee la caja visible; un elemento fuera del viewport no recibe un recuadro inventado. Sus errores se registran sin volcar excepciones del navegador y se ignoran. No se añadieron esperas: el procesamiento de capturas sí consume tiempo real y forma parte de las duraciones medidas. La revisión también cerró una evasión de la allowlist por WebSocket, independientemente de que se activen capturas; no cambió el DSL, el modelo ni las reglas del healer.
- **Contrato del evento `capture`** (`CaptureRef`, `extra="forbid"`): `id` (`^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$`), `step_id`, `phase` (`before|after|not_found|final`), `taken_at`, `page` (origen + ruta, sin query ni fragmento), `width`, `height`, `bytes`, `box {x,y,w,h}`, `masked` y `skipped` (`sensitive_step|sensitive_page|off_origin|limit|rate|too_large|error`). Una captura omitida también se registra, para que la consola pueda explicar por qué no hay imagen.
- **Privacidad (antes de que exista la imagen):**
  - Un paso `fill` o `select` que usa una entrada `sensitive` (la contraseña, `s3`) nunca se captura.
  - Tampoco se captura nada más en ese documento hasta un `domcontentloaded` auténtico del navegador. Un `wait_for_url` ya satisfecho o un cambio SPA no limpia la protección.
  - Solo se capturan orígenes loopback (`localhost`, `127.0.0.1`, `::1`) que además estén en `allowed_origins` del workflow. El resto, incluidos IPs privadas, metadatos de nube, `file:`, `about:` y `chrome:`, se omite como `off_origin`.
  - Playwright enmascara campos de contraseña, pago, OTP, nombres/ids/ARIA sensibles, regiones `data-private` y los iframes completos durante el propio `screenshot`. La ayuda de credenciales ficticias del portal lleva `data-private`. No se editan imágenes después. Esto protege el portal sintético controlado; no implica detectar cualquier secreto arbitrario en texto, canvas, shadow DOM cerrado o páginas autenticadas ajenas.
- **Recursos:**
  - 40 capturas por escenario y 25 MB por ejecución (presupuesto compartido entre A, B y C);
  - 600 KB por imagen, como máximo 10 intentos de captura por segundo y escenario (también cuentan los fallidos), viewport de hasta 1440×900 píxeles CSS en JPEG con calidad 70;
  - presupuesto de bytes compartido y atómico; retención de las 5 carpetas más recientes, respetando locks de ejecuciones activas y sin seguir symlinks;
  - el endpoint rechaza archivos de más de 1 MB y dimensiones JPEG reales superiores a 1440×900;
  - caché DOM limitada a 3 imágenes grandes y 80 miniaturas, vaciada al cambiar de evidencia; las imágenes eliminadas por retención llevan a la reconstrucción.
- **Ubicación:** el runner (`demo_healing`) solo captura si `--evidence-dir` está fuera del repositorio o dentro de `demo-evidence/`, que está ignorado por Git. Nunca escribe en `docs/evidence`. `--no-captures` las desactiva.
- **Endpoint** `GET /api/demo/captures/{bundle}/{capture_id}`:
  - solo cubre la colección `runs`; las evidencias grabadas no tienen capturas;
  - el id debe figurar en ese bundle sin `skipped`. Cada componente se abre mediante descriptores con `O_NOFOLLOW`, incluso `captures/` y el directorio del bundle; se rechazan hardlinks y archivos especiales;
  - devuelve bytes ya leídos del descriptor validado, sin reabrir una ruta en `FileResponse`. Se limita tanto `fstat` como la lectura;
  - devuelve `image/jpeg`, `nosniff`, CSP, `Cross-Origin-Resource-Policy: same-origin` y `Cache-Control: no-store`. Rechaza peticiones de imagen cross-site/cross-origin. `/api/demo` requiere un Host loopback literal, mitigando DNS rebinding sobre estos endpoints;
  - no hay POST ni DELETE ni autenticación multiusuario. Solo es una consola local en una máquina de confianza; no exponer el backend;
  - nombres de ejecución aleatorios por defecto, nombres CLI validados y reserva exclusiva para impedir colisiones/sobrescrituras.
- **Procedencia en la consola:**
  - **Live capture:** ejecución en curso.
  - **Recorded capture:** imagen guardada durante esa ejecución.
  - **Reconstructed view:** sin capturas, por ejemplo la evidencia GF-003, que no tiene imágenes y no se le inventan.
  - Las etiquetas *Simulated AI* / *Real Meta* se mantienen.
  - Un *Replay* de capturas grabadas lleva la franja *REPLAY … not a live execution*.
  - Para cada paso se muestra la última captura tomada en ese paso o antes, nunca una posterior, con un recuadro sobre el elemento usado. Si el paso no tiene captura propia, la nota indica de qué paso viene la imagen y por qué falta la suya.
- **Ensayo integrado:** `make demo-rehearsal` y `make demo-live` ejecutan Chromium sin ventana (headless) por defecto, y `DEMO_HEADED=1` lo muestra para diagnosticar. Antes, cada escenario lanzaba un Chromium con ventana, y macOS lo traía al primer plano por encima de la consola.
- **Continuidad en vivo:**
  - mientras hay una ejecución en vivo, la consola consulta cada 500 ms;
  - el visor mantiene la última imagen ya cargada hasta que llega la siguiente, así que no hay cuadros en blanco;
  - cuando una consulta revela un evento que terminó entre dos consultas, incluso si la respuesta final ya dice `saved`, la consola lo presenta durante `MOMENT_MS` (1,6 s), en orden y en pasado. Admite hasta 4 eventos en espera más el evento visible. El runner nunca se frena; elegir escenario, evidencia, paso o miniatura cancela las pausas;
  - cambiar de fuente aborta la petición anterior y descarta respuestas de generaciones previas, también A→B→A. La elección manual desactiva Follow live runs; abrir una URL inicial conserva la preferencia del checkbox. Se conserva el foco del teclado durante el polling.
  - si un asesor tarda más de la ventana de actividad, la consola muestra la pérdida de actualizaciones sin afirmar que el motor sigue trabajando. El polling de fuentes vuelve a consultar ese mismo run cuando reanuda o termina; el historial seleccionado del backend también se refresca. Un fallo final de verificación tiene prioridad sobre una celebración retrospectiva.
  - No hay flujo de video: cada paso ya produce capturas reales.
- **Ghost 2.0 (estados derivados de eventos reales):**

  | Estado | Evento que lo activa |
  |---|---|
  | `working` | `step_started` |
  | `investigating` | `recovery_started`; el texto es *Recovery in progress*, sin afirmar qué hace el modelo |
  | `evaluating` | **solo en playback**, a partir de las validaciones grabadas y con la etiqueta *playback pace, not the real duration*; el backend no emite un evento de evaluación en vivo |
  | `proud` | `step.state == recovered`: un salto, saludo y destellos, sin repetirse; un fallo de verificación posterior tiene prioridad |
  | `happy` | completado |
  | `alert` | detenido de forma segura cuando consta en la evidencia; errores, interrupciones y verificación fallida se rotulan «review required» |
  | `cautious` | `recovery.outcome` es `rejected` o `error`; nunca celebra |

  Los estados se anuncian una sola vez en `#agent-status` (`role="status"`), siempre van acompañados de texto y se detienen con `prefers-reduced-motion`.
