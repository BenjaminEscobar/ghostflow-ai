# Backlog

Mejoras identificadas durante GF-001 y dejadas fuera de alcance a propósito.

## Producto
- Consola web en **Angular** para listar grabaciones, revisar/editar workflows, lanzar ejecuciones e inspeccionar resultados (hoy: Swagger UI y curl).
- Aserciones definidas por el usuario durante la grabación (p. ej. Alt+clic sobre un texto para "verificar esto").
- Grabación por pestaña en lugar de global.
- Edición del nombre de los inputs desde la extensión o la consola.

## IA (Meta Model API)
- Validar endpoint, modelo y salida estructurada de la Llama API con una cuenta real y presupuesto aprobado.
- Tareas nuevas: nombrar inputs semánticamente, proponer verificaciones adicionales, explicar fallos de ejecución, sugerir estrategias de localización alternativas tras un fallo (siempre como propuesta validada, nunca como código).
- Registro de uso/costos por llamada y límites de presupuesto.

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
