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

## Self-healing (GF-003)
- Proponer al usuario promover una recuperación exitosa a localizador grabado (con aprobación humana; hoy no hay aprendizaje persistente).
- Candidatos dentro de iframes del mismo origen y shadow DOM abierto.
- Recuperación de verificaciones `element_text` (hoy solo pasos de acción).
- Captura de pantalla redactada del momento de la recuperación como evidencia adicional.
- Exponer el estado del self-healing en un endpoint de configuración (no se agregó a `/api/health` para mantener su contrato de GF-002).
- Panel de auditoría de recuperaciones por workflow (consola Angular).

## Demo Console (GF-004)
- Eventos en vivo por Server-Sent Events en lugar de *polling* de archivos.
- Lanzar ejecuciones desde la consola (requiere autenticación y confirmación explícita; hoy es solo lectura a propósito).
- Captura de pantalla redactada por paso y vista lado a lado navegador/consola.
- Exportar una evidencia como informe PDF o Markdown firmado (hash del bundle).
- Comparar dos ejecuciones (p. ej. A y C) paso a paso.
- Selector de idioma ES/EN en la consola.
- Registrar la lista de candidatos también en la importación de evidencias antiguas (no es posible con la evidencia GF-003 actual).

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
