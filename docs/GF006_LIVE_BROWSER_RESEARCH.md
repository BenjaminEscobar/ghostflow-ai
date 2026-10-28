# GF-006 — Navegador continuo dentro de la consola

Investigación del 27 de octubre de 2026 (Guatemala). **Propuesta solamente:** no se activó screencast, no se agregaron endpoints de video, dependencias ni infraestructura. Las capturas por paso siguen siendo la implementación del producto.

## Decisión

Recomiendo **A: frames JPEG previamente enmascarados, muestreo adaptativo y entrega del último frame disponible**, como evolución conservadora después del hackathon. Mantendría las capturas por hitos como evidencia durable, separadas de los frames efímeros. Antes del hackathon conviene ampliar y presentar mejor el visor actual.

Para una etapa posterior en que la fluidez sea el objetivo principal, la opción nativa **D (`page.screencast`)** merece un prototipo antes de escribir un cliente CDP propio. Su API ya existe en Playwright 1.63 instalado; no hay que migrar de versión para investigarla. No ofrece las garantías de enmascaramiento de A y requiere una barrera de privacidad distinta. No recomiendo comenzar por WebRTC, HLS ni codificación de video.

Las estimaciones siguientes son objetivos de diseño y juicio arquitectónico, **no mediciones de FPS, CPU, RSS o latencia**. La misión verificó capturas por pasos y el navegador actual, no hizo un benchmark de streaming.

## Restricciones reales de GhostFlow

- El runner es un proceso separado de FastAPI. Comparten archivos; un broker en memoria del API por sí solo no recibe los frames del runner.
- El adaptador usa Playwright **síncrono**. Un temporizador en otro hilo no debe operar ese mismo `Page`. Una captura periódica en el hilo dueño tampoco puede interrumpir libremente un `click()` o `wait_for_url()` bloqueante.
- El bundle está acotado a 80 referencias por escenario y conserva evidencia durable. No debe convertirse en un catálogo infinito de frames a varios FPS.
- Los frames no son eventos semánticos: una imagen que llegó tarde no puede cambiar qué paso se considera ejecutado ni el resultado del healer.
- Una captura no incluye automáticamente el cursor del sistema. Movimiento, clic y escritura requieren eventos observados o decoraciones explícitas, identificadas como tales.

## Alternativas de adquisición

| Alternativa | Fluidez y latencia esperadas | CPU, memoria y ancho de banda | Integración, macOS y esfuerzo | Privacidad, estabilidad y plazo |
|---|---|---|---|---|
| **A. JPEG periódicos protegidos** | Objetivo 2–4 FPS cuando el propietario del navegador tenga un punto seguro de muestreo; aproximadamente 250–750 ms locales. Navegaciones rápidas pueden saltarse imágenes. Solo añadir hitos no produce video durante una llamada bloqueante. | Codificación JPEG repetida; CPU moderada y mayor que las capturas actuales. Búfer de 1–2 frames, sin acumulación. A 80 KB × 3 FPS: unos 240 KB/s, 1,92 Mbit/s antes de overhead. | Reutiliza política, encoder y visor; compatible con Chromium en macOS. Complejidad **media**, aproximadamente 3–5 días incluyendo privacidad y pruebas. Continuidad durante acciones bloqueantes requiere coordinación adicional en el hilo dueño o una evolución async aislada. | Mejor encaje con `screenshot(mask=...)`; cada imagen nace enmascarada. Riesgo bajo/medio de perturbar tiempos si se muestrea demasiado. **Después del hackathon**; antes, conservar hitos. |
| **B. CDP `Page.startScreencast`** | Objetivo de prototipo 8–15 FPS con descarte; no garantizar FPS constantes, porque depende de frames producidos, ACK y carga. Latencia local objetivo 100–300 ms. | Eventos JPEG/PNG comprimidos; JPEG base64 añade aproximadamente 1/3 al tamaño binario en el tramo CDP. CPU moderada, presión por callbacks y decodificación. ACK y cola acotada son esenciales. | Playwright permite `context.new_cdp_session(page)`; es una vía Chromium. macOS es compatible con Chromium. Complejidad **media/alta**, 5–10 días para una integración segura, incluyendo IPC runner/API. Protocolo experimental: comprobar capacidades en la versión instalada. | Los frames CDP **no heredan** `mask` de `page.screenshot`. Riesgo alto de imágenes intermedias sensibles y de interferir con tracing/video. **Después**. |
| **C. Video codificado y transporte continuo** | Depende de la fuente: JPEG/MJPEG conserva su cadencia; WebRTC podría ofrecer baja latencia; HLS añade segmentos y resulta poco apropiado para observar clics inmediatos. | JPEG continuo consume más ancho de banda; codificación de video añade CPU y búferes. Múltiples viewers multiplican salida. No hay cifras medidas en este proyecto. | FastAPI puede entregar bytes progresivos, pero un transporte no crea los frames ni soluciona el IPC. WebRTC/HLS necesitan una cadena de codificación y sincronización mayor. macOS viable, complejidad **alta**, al menos 1–2 semanas para una versión segura según transporte. | Autenticación, colas, reconexión, backpressure y retención adicionales. No mejora automáticamente la redacción. Riesgo alto por amplitud del cambio. **Después**. |
| **D. Playwright `page.screencast.start(on_frame=...)`** | Flujo de JPEG con timestamp y tamaño del viewport. Objetivo inicial de recepción 5–10 FPS con descarte; la fluidez real debe medirse. Decoraciones opcionales de acciones y cursor. | Evita escribir el cliente CDP y decodificar base64 en la aplicación. No elimina el coste de captura/encoding. Callback mínimo: validar metadatos, encolar si está permitido y retornar. | La firma local 1.63 expone `on_frame`, `quality`, `size`, `path`; `show_actions` incluye `cursor`. Compatibilidad a validar en Chromium/macOS con el producto; **no se inició esta API en la misión**. Complejidad **media**, aproximadamente 4–8 días con IPC y privacidad. | No tiene parámetro `mask` en esa firma. No habilitar `path`, acciones ni títulos automáticamente: podrían conservar o rotular información sensible. La configuración puede compartirse con tracing/video. **Después**; alternativa preferible a CDP propio si se prioriza fluidez. |

El enmascaramiento está documentado para `page.screenshot`, incluida la cobertura del bounding box del locator. Desactivar animaciones en una captura puede disparar eventos de transición; no lo recomiendo en el navegador que ejecuta el workflow. [API oficial de screenshots](https://playwright.dev/python/docs/api/class-page#page-screenshot).

CDP ofrece eventos de frames y reconocimiento mediante `screencastFrameAck`; sus métodos de screencast figuran como experimentales. La conexión desde Playwright está documentada, pero las capacidades de la página *tip-of-tree* no garantizan las del Chromium fijado. [CDP Page](https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-startScreencast), [CDPSession de Playwright](https://playwright.dev/python/docs/api/class-cdpsession).

La API nativa entrega JPEG a un callback y puede decorar acciones; su documentación actual también contiene opciones añadidas en 1.64. **No asumir que `fps` o el nuevo bloque `style` existen en 1.63**: la firma instalada se inspeccionó localmente y no los incluye. El cursor decorado anima el traslado entre puntos de acción; no demuestra una trayectoria física real. [Screencast de Playwright](https://playwright.dev/python/docs/api/class-screencast).

La grabación tradicional `record_video_dir` es útil para un artefacto al terminar; su disponibilidad al cerrar el contexto la hace una mala fuente para esta experiencia en vivo. [Videos de Playwright](https://playwright.dev/python/docs/videos).

## Transporte: una decisión separada de la fuente

| Transporte futuro | Ventajas | Costes y decisión |
|---|---|---|
| **HTTP: último JPEG + secuencia** | Encaja con FastAPI y `<img>`, fácil de cancelar y limitar; puede reutilizar la presentación actual. | Para A, comenzar aquí. Una solicitud por frame; 2–4 FPS son razonables como objetivo. No introducir caché persistente ni generar un archivo durable por frame. |
| **MJPEG por HTTP** | Un flujo de imágenes sin codificador de video; integración sencilla con respuesta progresiva. | Manejo de desconexiones, imágenes fuera de orden respecto a eventos y viewers lentos. Transportar etiquetas requiere otro canal. No aporta privacidad adicional. |
| **WebSocket binario** | Metadatos y JPEG ordenados por conexión; sirve para eventos + imágenes. | Requiere autenticación de conexión, validación de Origin, límites por viewer y descarte de frames antiguos. Útil si se migra también el canal de eventos; excesivo para el primer paso de A. |
| **SSE + HTTP JPEG** | SSE sirve para eventos y secuencias; un GET separado sirve la imagen. | Evitar JPEG base64 dentro de SSE: expansión y copias. No enviar referencias a imágenes que un viewer distinto pueda leer sin autorización. |
| **WebRTC / HLS** | Apropiados para necesidades de video más maduras. | Complejidad de encoder, señalización o segmentación. No recomendados para este hackathon ni para el primer prototipo. |

## Barrera de privacidad necesaria

Una máscara correcta en una captura puntual no autoriza un flujo. Contraseñas no son el único problema: tokens en inputs de texto, OTP, ayudas de acceso, correos personales, toasts, resultados de autenticación, iframes y sesiones persistentes también aparecen entre pasos.

1. **Autorizar la ejecución y la página.** Identificador impredecible no reemplaza autenticación. Una sesión del viewer debe tener permiso para ese run. Same-origin estricto, Host permitido, sin CORS abierto ni secretos en URLs. Loopback limita red; no demuestra que una página esté libre de secretos.
2. **Cerrar la captura antes de actuar sobre datos sensibles.** El evento anterior a `fill/select` debe cerrar la barrera en el productor, invalidar su generación y descartar frames en vuelo/colas. No basta con ocultar el `<img>` después. Si el productor no confirma el cierre, el modo visual se desactiva; nunca se continúa transmitiendo por optimismo.
3. **Mantener bloqueado el documento contaminado.** Un cambio de URL SPA no limpia secretos. Una nueva carga de documento tampoco prueba que la sesión no reflejó un secreto: reanudar requiere una política de página explícitamente aprobada. Para datos reales, declarar regiones/campos seguros y excluir las páginas que no se puedan validar.
4. **Aplicar protección en el origen.** En A, máscaras en cada screenshot y exclusión de páginas sensibles. En B/D, `mask` no existe: excluir completamente páginas privadas y detener el productor antes del paso sensible. Un CSS que se instala después de arrancar el screencast puede llegar tarde, cambiar el layout y ser retirado por la propia página. Si no se puede probar la ausencia de frames intermedios expuestos, no habilitar esa fuente.
5. **Descartar por generación, no solo por hora.** Cada frame llevaría `run_id`, `scenario_key`, `document_epoch`, `privacy_epoch`, `step_id`, `sequence`, timestamp de captura y dimensiones. Después de un bloqueo o cambio de evidencia, el servidor y el viewer rechazan generaciones viejas, incluso si llegan tras reconectar.
6. **Retención mínima.** Frames autorizados en memoria acotada y sin logs de payload; sin video bruto, traces automáticas, caché HTTP ni almacenamiento de frames descartados. Las capturas por hitos mantienen su retención separada. Invalidar viewers al cerrar permisos; un frame ya mostrado no se puede retirar de una grabación externa del usuario.
7. **Prevenir control remoto accidental.** El viewer recibe imágenes; no transmite clics, teclas, cookies, almacenamiento ni comandos CDP hacia el navegador. La conexión de DevTools nunca se expone al frontend.

## Propuesta de componentes, sin implementación

Para A, dividiría el trabajo futuro así:

- `execution/capture.py`: extraer una política común de privacidad, distinguir capturas durables de frames efímeros y producir frames solo en puntos seguros. Presupuesto de tiempo/carga, adaptación de frecuencia, descarte si no hay viewer y circuit breaker cuando falle la captura.
- `execution/playwright_web.py`: interfaz observacional de ciclo de página y paso; todas las operaciones de `Page` continúan en su hilo dueño. Diseñar y aprobar por separado el scheduler/async necesario para muestrear durante operaciones largas. Ningún sleep de presentación, reintento adicional ni decisión del modelo nueva.
- `demo_healing.py`: conectar el productor con FastAPI. Para mantener procesos separados, un canal IPC local autenticado (por ejemplo Unix socket con permisos restrictivos en macOS/Linux) entrega solo frames autorizados a un buzón de 1–2 frames por run. No agregar Redis ni un servicio externo. La portabilidad Windows requiere otra elección.
- `console/__init__.py`: futuro servicio de último frame y suscripción acotada, con autorización por run, Origin/Host y cancelación; no es parte de GF-006 actual.
- `console/static/console.js` y `.css`: visor con doble búfer, contador de secuencia, estado «visualización pausada por privacidad», indicador de retraso y eventos semánticos independientes. Movimiento/clic representados solo si provienen de acciones observadas y rotulados como decoraciones.
- `evidence.py`: conservar el contrato de hitos; los frames efímeros no se agregan ilimitadamente a `ScenarioView.captures` ni sustituyen la evidencia autoritativa.

El buzón conserva **el frame más reciente**, no una cola que reproduzca tarde toda la ejecución. Los eventos de recuperación requieren su cola semántica propia y una etiqueta de presentación retrospectiva. La salud del viewer nunca gobierna el resultado del workflow.

## Pruebas y criterios de aceptación futuros

- Ensayos A/B/C comparados con captura desactivada: mismas acciones, resoluciones, candidatos, verificaciones y SHA del DSL; cero acciones duplicadas y ningún retardo de presentación en el motor.
- Canarios sensibles y validación de **todos los frames transmitidos**, no solo el final: fill lento, password visible/oculto, token de texto, OTP, `data-private`, iframe, shadow DOM, toast, navegación en medio de una captura y SPA. Comprobar barrera antes del primer byte y descarte de frames de generaciones previas.
- Concurrencia de dos runs y dos viewers; intento de suscribirse al run ajeno, reuso de identificadores, DNS rebinding, conexión cross-origin, revocación, reconexión y cancelación A→B→A.
- Viewer lento/desconectado, pestaña oculta, captura fallida, productor muerto y cola llena: memoria limitada y automatización operativa. Límites efectivos de bytes, dimensiones, FPS, solicitudes y tiempo de retención.
- Benchmarks macOS con el Chromium fijado: latencia captura→pantalla p50/p95, CPU/RSS del runner/API/viewer y bytes/s con y sin movimiento. Objetivo provisional: p95 <750 ms para A, máximo 2 frames pendientes y ningún crecimiento sostenido de memoria; aprobar cifras solo tras medirlas.
- Teclado, lector de pantalla, `prefers-reduced-motion`, foco, legibilidad a 1440/1024/820 px, diferencia entre timestamp real y ritmo de presentación, y estado honesto cuando el stream está suspendido por privacidad.

**Riesgo principal:** emitir un solo frame intermedio con un secreto invalida la mejora. Si no se puede demostrar la barrera de privacidad, las capturas por pasos actuales son la decisión correcta para el hackathon.
