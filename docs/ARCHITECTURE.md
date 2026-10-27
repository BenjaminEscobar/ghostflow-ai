# Arquitectura — GF-001 / GF-002

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
| `execution` | `ExecutionAdapter` + adaptador `web` (Playwright) | `dsl`, `security` |
| `storage` | Tablas `recordings`, `workflows`, `runs` (documentos JSON) | — |
| `api` | Endpoints y composición (`create_app`) | todos |

`ai` y `execution` no se conocen entre sí: la IA nunca toca el motor.

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
