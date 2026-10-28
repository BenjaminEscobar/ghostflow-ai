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
- Recomendaciones UX priorizadas de la revisión GF-006: [informe independiente](GF006_INDEPENDENT_REVIEW.md). Priorizar ampliación del navegador y etiquetas separadas de interpretación/recuperación para el hackathon; las demás mejoras requieren revisión visual humana.
- Navegador continuo: [comparación y propuesta GF-006](GF006_LIVE_BROWSER_RESEARCH.md). Sin implementación ni endpoints nuevos en esta misión; considerar `page.screencast` nativo además de CDP.
- Eventos en vivo por Server-Sent Events en lugar de *polling* de archivos.
- Lanzar ejecuciones desde la consola (requiere autenticación y confirmación explícita; hoy es solo lectura a propósito).
- Evento real de evaluación de candidatos desde el healer (hoy *Evaluating* solo existe en el playback).
- Resaltar en la captura el elemento elegido por la recuperación. Hoy el healer actúa sin pasar por el adaptador, así que el paso recuperado tiene la imagen `not_found` y no `before`. Requiere un hook en `Healer.attempt`, que GF-006 no modificó.
- Exportar una evidencia como informe PDF o Markdown firmado (hash del bundle).
- Comparar dos ejecuciones (p. ej. A y C) paso a paso.
- Selector de idioma ES/EN en la consola.
- Registrar la lista de candidatos también en la importación de evidencias antiguas (no es posible con la evidencia GF-003 actual).

## Captura y ejecución
- Navegación SPA (`history.pushState`, `hashchange`).
- iframes del mismo origen, shadow DOM, carga de archivos, teclas adicionales, doble clic, hover.
- Traza de Playwright por ejecución (con redacción) y capturas en workflows del backend (`POST /runs`), con la misma política de GF-006.
- Cola de ejecuciones asíncrona (Redis) y reintentos.
- Persistir la estrategia "curada" tras una ejecución con `healed` para promoverla a primaria (con aprobación humana).

## Plataforma
- Aislamiento de red completo para páginas no confiables: auditar WebRTC, WebTransport, workers y redirecciones además del filtro HTTP/WS; la allowlist del navegador no reemplaza un firewall de salida.
- Retirar el puente async de cierre de WebSocket al actualizar Playwright, después de verificar que el callback síncrono ya no se bloquea (versión fijada: 1.63).
- Paginación/caché del índice de evidencias y límites HTTP si el volumen deja de ser el de una demo local; la retención actual elimina imágenes, no bundles JSON históricos.
- Autenticación de la API y de la extensión (token por instalación).
- Migraciones con Alembic.
- CI (GitHub Actions) ejecutando `make test` y `make test-e2e`.
- Mitigación de DNS rebinding (resolver y fijar IP por ejecución).
- Elegir licencia del repositorio público (decisión del propietario).
- Execution Adapter para Windows y para APIs HTTP.
