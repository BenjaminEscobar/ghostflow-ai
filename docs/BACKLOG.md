# Backlog

Mejoras identificadas durante GF-001 y dejadas fuera de alcance a propósito.

## Producto
- Consola web en **Angular** para listar grabaciones, revisar/editar workflows, lanzar ejecuciones e inspeccionar resultados (hoy: Swagger UI y curl).
- Aserciones definidas por el usuario durante la grabación (p. ej. Alt+clic sobre un texto para "verificar esto").
- Grabación por pestaña en lugar de global.
- Edición del nombre de los inputs desde la extensión o la consola.

## IA (Meta Model API)
- Tareas nuevas: proponer verificaciones adicionales, explicar fallos de ejecución, sugerir estrategias de localización alternativas tras un fallo (siempre como propuesta validada, nunca como código).
- Renombrar inputs con nombres semánticos propuestos por el modelo (requiere reescribir plantillas de forma controlada y aprobación humana).
- Promover a `sensitive` un input que el modelo marque como credencial, con aprobación humana (hoy solo genera una nota de revisión).
- Re-interpretar un workflow existente (`POST /api/workflows/{id}/interpret`) y comparar versiones.
- Persistir uso de tokens y costo por llamada (hoy solo en logs y en la CLI de comparación) y presupuesto acumulado entre reinicios.
- Evaluar Responses API (razonamiento entre turnos), prompt caching (`prompt_cache_key`) y `safety_identifier` cuando haya usuarios.
- Mostrar la interpretación en la extensión o en la consola Angular.

## Captura y ejecución
- Navegación SPA (`history.pushState`, `hashchange`).
- iframes del mismo origen, shadow DOM, carga de archivos, teclas adicionales, doble clic, hover.
- Capturas de pantalla / traza de Playwright por ejecución (con redacción).
- Cola de ejecuciones asíncrona (Redis) y reintentos.
- Persistir la estrategia "curada" tras una ejecución con `healed` para promoverla a primaria (con aprobación humana).

## Plataforma
- Autenticación de la API y de la extensión (token por instalación).
- Migraciones con Alembic.
- CI (GitHub Actions) ejecutando `make test` y `make test-e2e`.
- Mitigación de DNS rebinding (resolver y fijar IP por ejecución).
- Elegir licencia del repositorio público (decisión del propietario).
- Execution Adapter para Windows y para APIs HTTP.
