# GF-006 — Revisión independiente de seguridad, arquitectura, UX y movimiento

Fecha: 27 de octubre de 2026, Guatemala; algunas ejecuciones y capturas llevan fecha UTC del 28. Base Git verificada: **`0140007`**. GF-006 ya estaba implementada sin commits al comenzar. Esta revisión conserva esa entrega y añade correcciones puntuales; no reconstruye la aplicación.

**Veredicto:** preparada técnicamente para un ensayo y una presentación **locales, con datos sintéticos**. Antes de grabar, priorizar la legibilidad del navegador y la claridad de la procedencia de Meta. No es un servicio autenticado multiusuario ni está preparada para exponer el backend a Internet. Streaming queda como investigación, con [propuesta separada](GF006_LIVE_BROWSER_RESEARCH.md).

## Método y alcance

Se leyeron primero `GF006_REVIEW.md`, arquitectura, guion/runbook, cambios pendientes y pruebas. Se revisaron productor de imágenes, almacén/endpoint, runner, DSL, políticas de recuperación, frontend y CSS/SVG. Se reprodujo la baseline de **255 offline + 19 E2E**. Chromium requirió ejecución fuera del sandbox de macOS. Las fixtures desactivan `.env`, fuerzan proveedores de prueba y bloquean HTTP externo; **no se accedió a valores de `backend/.env` ni se realizaron llamadas reales a Meta**.

La evaluación visual usa JPEG reales del portal, pantallas de Demo a 1920, 1440, 1024 y 820 px, Developer a 1440 px, el ensayo A/B/C con mock y fotogramas de Investigating durante un replay de una recuperación real del mock. Se examinaron los PNG generados; no se calificó la interfaz solo por pasar aserciones DOM. La interpretación y la recuperación Meta guardadas se revisaron mediante sus contratos y E2E existentes, sin volver a consultar el modelo.

No se hicieron pruebas de penetración sobre servicios externos, benchmarks de streaming, mediciones de rendimiento con profiler, auditoría completa de accesibilidad con VoiceOver ni una nueva validación manual de Meta. No hay afirmaciones de que esas comprobaciones hayan pasado.

## A. Hallazgos técnicos

No se identificó un hallazgo **crítico** dentro del alcance y modelo de despliegue local. Los hallazgos altos corregidos y los medios/bajos se describen abajo. La severidad considera el impacto potencial; los ataques a archivos requieren poder alterar el árbol local de evidencias y los de red requieren una página no confiable.

| ID / severidad | Problema observado y consecuencia | Corrección y evidencia |
|---|---|---|
| S01 **Alta** | `capture_file` resolvía `captures/<bundle>` como base confiable: un symlink de ese directorio, o de `captures/`, podía apuntar fuera. `FileResponse` reabría después una ruta ya comprobada. `prune_captures` seguía directorios enlazados y podía eliminar JPEG externos. | Apertura por descriptor de cada componente con `O_NOFOLLOW`, archivos regulares sin hardlinks, lectura limitada desde el descriptor y respuesta con bytes ya validados. Retención con `lstat`/`dir_fd`, sin seguir enlaces. Pruebas sobre enlaces de archivo, bundle y ambos directorios; escritor y retención adversariales. |
| S02 **Alta** | Un `wait_for_url` ya satisfecho o una navegación SPA limpiaba el taint aunque el mismo documento aún contuviera un valor sensible, posiblemente reflejado como texto. | La protección solo se limpia con un nuevo `domcontentloaded` del navegador. El mismo documento permanece bloqueado, incluido `final`. Prueba del caso SPA sin generar una sola imagen. Sigue siendo necesario controlar qué muestra la nueva página; nueva carga no garantiza por sí sola ausencia de secretos. |
| S03 **Alta**, previa a GF-006 | La allowlist HTTP no interceptaba WebSocket. Un script del portal alcanzó un servidor loopback no permitido: se reprodujeron cuatro handshakes antes de corregirlo. | `context.route_web_socket` valida el origen HTTP(S) equivalente de WS(S) antes de conectar. Pruebas reales de destino bloqueado y permitido. No se agregó un endpoint de streaming. |
| S04 **Alta**, exposición condicional | `/api/demo` aceptaba Host arbitrario. Un dominio que llegara por DNS rebinding al servicio local no encontraba una barrera de Host para leer evidencias. El endpoint de imagen tampoco rechazaba consumidores web de otros orígenes. | Host loopback obligatorio en `/api/demo`; rechazo de Origin ajeno y `Sec-Fetch-Site: cross-site` en imágenes, más CORP same-origin. Pruebas de cabeceras host/origen; no se montó un ataque DNS externo. Esto no agrega autenticación ni protege todos los otros endpoints del backend. |
| S05 **Media** | El nombre CLI admitía traversal y el nombre por segundo podía colisionar. Dos recorders podían reemplazar el mismo JPEG, mezclando evidencia y caché. | Nombre plano validado, sufijo aleatorio por defecto, reserva de bundle con creación exclusiva y publicación de JPEG que no reemplaza un id existente. Casos de nombres inseguros y escritura duplicada cubiertos. |
| S06 **Media** | La retención podía borrar imágenes de otro ensayo activo y un presupuesto de bytes compartido no era atómico. | Lease mediante `flock` por ejecución; retención respeta locks, incluso entre procesos. Presupuesto protegido con lock. Prueba de captura concurrente con presupuesto para una sola imagen y prueba de retención de run activo. |
| S07 **Media** | Los intentos fallidos no consumían el límite de frecuencia; comprobar el tamaño comprimido no acotaba las dimensiones de un JPEG reemplazado. | Fallos cuentan en la ventana de 10 intentos/s. Tamaño de lectura limitado y parser de dimensiones JPEG previo a servir: máximo 1440×900. Pruebas de datos inválidos/truncados y dimensiones excesivas. |
| S08 **Media** | `exc_info=True` podía volcar texto de errores del navegador/observer a logs, incluidos datos de una página o una URL. | Logs operativos sin texto arbitrario de excepción en captura y observer. URLs bloqueadas sin credenciales/query, saneadas con los secretos conocidos y lista acotada a 50 entradas. Canario en excepción comprueba que no aparezca en logs. |
| S09 **Media** | La captura hacía `scroll_into_view_if_needed` antes de la acción. Podía disparar lazy loading, sticky UI o handlers de scroll que cambiasen la página observada. | Eliminado el desplazamiento de presentación. Solo se consulta la caja; recuadro recortado al viewport y ninguno para un elemento totalmente fuera de él. El scroll que necesite la acción sigue perteneciendo a Playwright. |
| S10 **Media** | Peticiones de detalle de una fuente anterior seguían activas; comparar solo el nombre no cubría A→B→A. Elegir un paso no cancelaba toda la reproducción retrospectiva. El último poll `saved` descartaba eventos recién terminados. Tras superar los 20 s de actividad, un run podía quedar congelado aunque después se actualizara. | AbortController + generación de petición, limpieza de timers, cancelación por paso/miniatura y procesamiento de la transición final. El polling de fuentes vuelve a cargar el mismo run si reanuda o termina y refresca historial del backend. E2E cubre respuestas fuera de orden, cola auténtica y reanudación tras `stale`. |
| S11 **Media** | Caché DOM sin límite entre fuentes y precarga potencial de muchas imágenes grandes; al aplicar retención, una imagen ausente dejaba un visor roto. | Máximo 3 imágenes grandes y 80 miniaturas, limpieza por fuente y precarga del próximo frame. Fallos recordados durante esa selección para evitar bucles; fallback a reconstrucción con aviso. No se afirma un límite exacto de RSS del navegador. |
| S12 **Media**, UX/accesibilidad | Rerender del polling retiraba el botón enfocado; varias regiones live se reconstruían aunque nada hubiese cambiado. Un hash mal codificado producía error y `#app` podía interpretarse como evidencia. | Claves de foco estables, menú de fuente estable mientras tiene foco, regiones de estado actualizadas al cambiar, canal de estado del agente y hash validado. Teclado real, poll completo, hash inválido, enlace de salto y reduced motion cubiertos. |
| S13 **Baja**, privacidad de demo | La ayuda del propio login mostraba la contraseña ficticia en texto: enmascarar solo el input no la ocultaba. También faltaban campos OTP/tokens e iframes en la máscara inicial. | Ayuda marcada `data-private`; máscara ampliada a atributos sensibles, autocomplete compuesto, regiones privadas e iframes completos. E2E lee píxeles del JPEG codificado en seis regiones protegidas, sin depender de OCR. |
| S14 **Baja**, presentación | El cartel rojo de recuperación se superponía a la página; texto tenue y botones tenían contraste mejorable. Investigating oscilaba sin pausas. | Cartel en el pie de la imagen, sin cubrirla; texto secundario y controles principales con tinta más oscura. Mirada/lupa con desplazamientos pequeños y pausas de atención. E2E verifica que el cartel quede fuera del JPEG y que cambien los transforms. |
| S15 **Media**, integridad de presentación | Una recuperación confirmada podía dejar al fantasma orgulloso aunque después fallara la verificación final. Algunos errores genéricos se describían como una detención segura sin tener prueba suficiente; una fuente `stale` podía conservar el gesto de trabajo en curso. | Un fallo de verificación domina emociones y eventos retrospectivos; errores, interrupciones y pérdida de actualizaciones llevan «review required». Se conserva «Stopped safely» para rechazos/detenciones confirmados. E2E ejecuta una recuperación real del mock seguida de una verificación deliberadamente fallida y comprueba que no celebre. |

### Límites de privacidad e integridad

La protección actual se aplica **antes de codificar** la captura: exclusión del paso/documento sensible y máscaras de Playwright. No se guarda una imagen sin proteger para editarla después. Solo se fotografía el viewport del `Page` del runner, en orígenes loopback autorizados; no hay captura del escritorio ni acceso del frontend a DevTools. Los iframes completos se ocultan en vez de intentar confiar en sus campos internos.

Las cotas actuales son 40 imágenes por escenario, 25 MB por run, 600 KB por imagen del productor, 10 intentos/s por escenario, 1440×900 píxeles CSS, timeout de screenshot de 2 s y hasta 80 referencias de metadatos por escenario. El servidor limita además cada lectura a 1 MB y valida las dimensiones JPEG reales. Cinco carpetas recientes se conservan, más las que deban protegerse por actividad; errores de retención son best effort y no abortan el workflow. No se eliminan los JSON históricos. El navegador recibe `no-store`; su caché DOM se limita por separado.

No hay autenticación por usuario/run: cualquier cliente local de confianza puede consultar las evidencias que publica este backend. No sería correcto afirmar aislamiento multiusuario o autorización de capturas ajenas. La pertenencia id→bundle impide servir una imagen no listada, no identifica a una persona. Mantener los puertos en loopback y usar datos sintéticos; una publicación remota necesita una fase de autenticación/autorización y aislamiento propia.

La allowlist valida destinos HTTP(S) y ahora WS(S). No constituye un firewall de salida del proceso: WebRTC, WebTransport, workers, DNS rebinding general y redirecciones requieren una auditoría de egreso más amplia antes de aceptar portales arbitrarios. El portal de demo controlado no necesita esos canales. Se registró el trabajo en backlog, sin inventar una garantía universal de aislamiento.

Enmascaramiento por selectores tampoco puede reconocer cualquier secreto en texto, canvas, shadow DOM cerrado o una página autenticada que refleje un valor de una sesión anterior. Las imágenes antiguas, exportaciones o grabaciones externas no se pueden revocar retroactivamente. Para páginas reales no aprobadas, desactivar capturas.

### Separación del motor

No hay cambios en `dsl/`, `execution/recovery.py`, los contratos del modelo ni `ai/`. Se mantienen orden de localizadores, candidatos, validaciones, política de efectos secundarios, decisión final y acción única del healer. Las verificaciones siguen siendo autoritativas. El endurecimiento WS corrige un canal que el filtro anterior no cubría: es un cambio de seguridad explícito, independiente de la presentación.

Los cuatro puntos de `snap()` y los eventos de evidencia solo observan. No se añadió ningún sleep al motor. Los delays y hold ya existentes de la demo no se aumentaron. Capturar sí añade coste de trabajo real y sus milisegundos forman parte de la medición; no se confunden con los 1,6 s de presentación ni con el ritmo del replay. Una captura fallida, un directorio de imágenes no disponible o un fallo de retención no alteran el resultado de negocio.

Playwright 1.63 ejecuta el callback de WebSocket sobre su dispatcher: llamar al cierre síncrono allí se bloqueó en la prueba adversarial. La corrección devuelve el cierre async del mismo objeto, sin hilo ni conexión adicionales. El puente usa `_impl_obj`, queda cubierto por E2E y se registra como deuda acotada a la versión fijada; revisarlo al actualizar Playwright. No se instalaron dependencias.

Los descriptores `O_NOFOLLOW` y los leases `flock` asumen un sistema POSIX. Se verificó macOS; no se certificó Windows ni se ejecutó esta misión en Linux. La portabilidad del almacenamiento de capturas necesita un diseño equivalente antes de anunciar soporte Windows.

## B. Evaluación UX/UI

### Demo View

La identidad es coherente: superficies claras, azul para ejecución, violeta para recuperación, estados con texto e iconos y una mascota reconocible. La frase inicial explica el objetivo y el contraste B→C demuestra el beneficio. La recuperación muestra una alternativa concreta y sus verificaciones; no se limita a un indicador abstracto de IA.

La debilidad principal es la **densidad alrededor de la imagen**. A 1440 px, sidebar, pasos y auditoría dejan un navegador pequeño: texto del portal que es legible a 1280 px se vuelve diminuto al reducirse a pocos cientos. El hero y How it works consumen altura antes del momento de valor. A 820 px no hay overflow horizontal, pero el navegador aparece después de una lista larga de pasos; es una experiencia correcta para inspeccionar, lenta para presentar. Las tarjetas de capacidades pueden partir palabras largas en espacios estrechos. Los títulos de escenarios repiten detalles técnicos y el estado B puede revelar nombres de variables de configuración en la vista de producto.

El visor ya diferencia screenshot/reconstrucción y live/recorded; al seleccionar un paso sensible conserva una imagen anterior identificada. La franja retrospectiva no promete duración real. Se corrigieron foco, cancelación, hash, imágenes eliminadas y superposición. Las miniaturas todavía exigen explorar horizontalmente; mostrar más estados no equivale a hacer más comprensible la historia.

### Developer View

La auditoría es sólida: localizadores agotados, candidatos reales, propuesta separada de validación, ruta esperada, revisión en vivo, tokens/latencia y hashes. Se conserva el rigor al declarar datos no disponibles en evidencia antigua; configuración actual e histórico tienen rótulos diferentes.

Hay fricción de lectura: tres columnas distribuyen datos relacionados; capturas se resumen como cantidad/MB sin un enlace directo al paso; el mock puede mostrar `?` para tokens donde sería más claro «no aplica». La latencia del asesor no es el tiempo completo del paso, y las duraciones importadas ausentes deben seguir sin rellenarse. El estado global «Simulated AI» puede entenderse como toda la inteligencia del workflow, aunque la interpretación guardada provenga realmente de Meta. Conviene separar esas dos procedencias en la primera pantalla.

### Diez recomendaciones priorizadas para revisión

Estas propuestas son mejoras de producto pendientes, además de los defectos ya corregidos. No se implementó un rediseño.

| # | Problema observado | Impacto | Solución propuesta | Prioridad | Esfuerzo | Momento |
|---|---|---|---|---|---|---|
| 1 | Navegador reducido entre pasos y panel lateral a 1440 px. | El juez no puede leer el cambio de interfaz. | Botón accesible «Ampliar captura» a resolución útil; en presentación, compactar pasos secundarios y dar más ancho al visor. | **P1** | Medio | **Antes** |
| 2 | Badge genérico de IA mientras interpretación y recuperación pueden tener proveedores distintos. | Puede parecer que una interpretación Meta real fue simulada, o que el mock de recuperación fue Meta. | Dos etiquetas explícitas: «Interpretation: Meta · recorded» y «Recovery: mock · 0 Meta requests», derivadas de cada evidencia. | **P1** | Bajo | **Antes** |
| 3 | A/B/C tienen títulos largos y detalles de implementación. | El contraste útil tarda en comprenderse. | Etiquetas breves «Original», «Changed UI · stops», «Changed UI · recovers»; nombres de variante/env al detalle Developer. Mantener estado accesible completo. | **P1** | Bajo | **Antes** |
| 4 | La razón decisiva de elegir c1 está dispersa entre explicación y auditoría. | Se percibe un acierto del modelo, pero no la seguridad del motor. | Junto a la alternativa, una línea verificable: «Same destination /new-request.html · 10/10 checks · acted once». No convertir confianza categórica en porcentaje. | **P1** | Bajo | **Antes** |
| 5 | Hero, How it works y lista completa retrasan el primer plano del navegador, especialmente en tablet. | Consume segundos del video y requiere mucho scroll. | Composición compacta durante ejecución/replay y resumen de páginas completadas plegable. Validar con una toma de tres minutos antes de adoptar el cambio. | **P1** | Medio | **Antes**, si queda tiempo de QA |
| 6 | En el paso privado se muestra correctamente una imagen anterior, pero la causa puede pasar desapercibida. | Parece un visor atrasado o congelado. | Indicador persistente «Private step · previous safe capture» y explicación breve, ligado al motivo real de omisión. | **P1** | Bajo | **Antes** |
| 7 | Developer muestra tokens desconocidos del mock y exige cambiar de vista para relacionar captura/paso. | Datos que parecen incompletos y búsqueda manual. | «N/A · offline mock» cuando corresponda; enlace «Inspect capture for s6» que lleve a Demo con ese paso fijado. | **P2** | Bajo | Antes, si hay margen; después aceptable |
| 8 | Tarjetas de capacidades y miniaturas compiten con el contenido; algunas palabras largas se parten. | Aspecto menos pulido y esfuerzo extra al escanear. | Ajustar padding/columnas internas de las capacidades, miniaturas con fase legible y contador; no añadir nuevas superficies ni adornos. | **P2** | Bajo | **Antes** |
| 9 | Las imágenes iniciales de un escenario pueden tardar brevemente en cargar. | Un espacio vacío durante el cambio puede confundirse con ausencia de evidencia. | Estado de carga textual y, si falla, fallback existente; conservar doble búfer solo dentro del mismo contexto, sin mostrar una captura de otro escenario. | **P2** | Bajo | Después; antes solo si es visible en el ensayo |
| 10 | Mezcla de inglés de producto, español del portal y códigos técnicos. | La narración debe traducir mientras demuestra. | Unificar el idioma de la toma y ofrecer después ES/EN sin cambiar datos originales; mantener locators/rutas originales en Developer. | **P2** | Medio | **Después** |

### Las siete preguntas de un juez

1. **¿Qué problema resuelve?** Sí: el texto inicial y B→C muestran automatización que sobrevive a una interfaz cambiada. El contraste debe aparecer antes que una explicación extensa de módulos.
2. **¿Cuándo interviene Muse Spark?** Está documentado y atribuido; en una toma breve necesita las dos etiquetas de procedencia. La interpretación GF-002 y la recuperación GF-003 son llamadas/etapas diferentes.
3. **¿Se ve Self-Healing?** Sí: elemento ausente, Investigating, alternativa y resultado. El mock puede resolver entre polls; la pausa retrospectiva conserva un evento real y debe narrarse en pasado.
4. **¿Por qué esa alternativa?** Developer lo prueba con destino esperado, compatibilidad y revalidación. En Demo conviene una sola línea con esa razón determinista junto al elemento elegido.
5. **¿IA real frente a mock?** Se distinguen por evidencia y contador de requests, no por configuración. No narrar el ensayo offline como una consulta nueva de Meta; la evidencia Meta guardada sigue siendo guardada.
6. **¿Qué distrae?** Repetición de How it works, textos largos de variantes, milisegundos en cada fila y detalles de configuración mientras el navegador queda pequeño. La mascota ayuda si sus gestos no reemplazan el mensaje.
7. **¿Mayor impacto visual?** Ampliar el navegador, compactar lo secundario y dejar en un solo foco «element missing → equivalent chosen → checks passed → continued».

Para la toma de tres minutos: 0–20 s problema; 20–45 s demostrar aprendizaje; 45–65 s interpretación Meta guardada con procedencia; 65–90 s A verificado; 90–110 s B detenido; 110–155 s C y motivo de selección; 155–180 s comprobaciones, resultado y DSL sin cambios. Usar evidencia Meta ya guardada para atribuir una recuperación a Muse Spark sin gastar créditos; si se muestra el mock, decirlo expresamente. Confirmar por separado el límite oficial del video.

## C. Ghost Personality 2.0

| Estado | Evaluación independiente |
|---|---|
| Idle | Flotación, sombra y parpadeo discretos. Buena presencia sin reclamar una acción del agente. |
| Working | Cejas de atención, boca neutra y anillo activo corresponden a un paso en ejecución. Se distingue de una recuperación. |
| Investigating | Lupa y mirada comunican búsqueda con claridad. Se suavizó la oscilación a un ciclo de 3,2 s con pausas y movimientos menores. La captura visual del replay muestra concentración reconocible. |
| Evaluating | Solo playback de validaciones grabadas, con rótulo explícito; no se inventa un evento interno del modelo en vivo. Los ticks comunican revisión. |
| Recovered | Salto y saludo limitados, luego estado tranquilo. Solo después de recuperación confirmada, con prioridad del fallo final si ocurre después; navegar otra vez a una recuperación histórica puede iniciar una nueva presentación del gesto. |
| Completed | Sonrisa y saludo breve al completar. No se usa como sustituto de las verificaciones finales. |
| Stopped safely | Expresión preocupada y anillo suave, sin confeti. Un error o verificación fallida usa la misma prudencia visual con «review required», sin prometer una detención segura no demostrada. |
| Proposal rejected | Expresión cauta y anillo ámbar; no celebra. Sigue ligada al resultado auditado de rechazo/error. |

Las animaciones usan SVG/CSS, principalmente transforms y opacidad; no se añadió una librería ni un bucle JS de animación. `prefers-reduced-motion` elimina animaciones/transiciones; cada emoción tiene texto y un estado accesible del agente. Se mantuvieron el personaje, paleta y anillo azul-violeta. La tira de estado se sacó del área del JPEG para no ocultar evidencia. No se midió FPS con profiler ni se hizo una sesión manual de VoiceOver: la cobertura de accesibilidad ejecutada es DOM/teclado/reduced motion y control de actualizaciones.

Tres mejoras artísticas futuras: **(1)** coordinar sutilmente brazo y lupa para que se muevan como una unidad; **(2)** añadir una transición de ojos/cejas de 150–220 ms entre concentración y alivio, siempre desactivable; **(3)** concentrar la celebración en una sola transición por evento y luego una postura orgullosa calmada, incluso cuando se repasa repetidamente la misma evidencia. Ninguna debería ampliar la duración real del motor ni generar eventos ficticios.

La personalidad profesional se comunica mejor con atención, prudencia y alivio que con actividad constante. La lupa identifica el trabajo de recuperación; las comprobaciones y la alternativa siguen siendo el contenido principal. No hacen falta voz, partículas adicionales ni textos que atribuyan pensamientos internos al modelo.

## D. Navegador como video

Se compararon JPEG periódicos, CDP, transportes de imagen/video y la API nativa `page.screencast` que ya contiene Playwright 1.63. La recomendación conservadora es evolucionar JPEG protegidos y entrega del último frame **después del hackathon**; para mayor fluidez, prototipar primero la API nativa antes que CDP propio, con una barrera de privacidad específica. [Comparación, costes, componentes, seguridad y pruebas](GF006_LIVE_BROWSER_RESEARCH.md).

No se implementó ninguna de esas alternativas. No se habilitó grabación de video, no se agregó un endpoint ni se cambió el transporte de la consola. Aumentar FPS sin cerrar el productor antes de un paso sensible puede exponer frames que las capturas puntuales nunca habrían generado.

## E. Calidad y estado de entrega

Baseline reproducida: **274/274**. Resultado final ejecutado: **300 aprobadas (273 offline + 27 E2E)**, en macOS con Chromium real, en 139,24 s. La suite completa pasó después de las últimas correcciones; no es solo un conteo de colección.

Pruebas nuevas: 26 casos de revisión independiente, incluidos píxeles JPEG protegidos, navegación/foco y replay con Chromium, recuperación seguida de fallo de verificación, reanudación del polling, WebSocket permitido/bloqueado y respuestas A→B→A. Las pruebas iniciales de GF-006 siguen presentes. Se corrigió una aserción temporal que exigía siempre JPEG final: una máquina rápida puede consumir legítimamente los 10 intentos/s; ahora exige el evento final y acepta solo una omisión explicada por `rate`/`limit`. No se relajó la política para hacer pasar la prueba.

La primera corrida offline encontró un proxy SOCKS heredado que requería una dependencia opcional antes de que actuara el guard de red. La fixture ahora elimina proxies heredados además de las variables GhostFlow; no se instaló `socksio` ni se cambió la configuración productiva. Durante desarrollo, las pruebas detectaron el bloqueo síncrono de WS y una aserción auxiliar incompatible con CSP; ambos se corrigieron sin debilitar la CSP. La suite conserva una advertencia deprecatoria Starlette/HTTPX; no se actualizaron dependencias para silenciarla.

Archivos de producto cambiados por esta revisión, sobre la entrega local inicial:

- `execution/capture.py`: privacidad, cotas, escritor y retención seguros, locks y cajas sin scroll.
- `security/files.py` (nuevo): acceso limitado por descriptor y lectura de dimensiones JPEG.
- `execution/playwright_web.py`: logs seguros, filtro WS y registros de bloqueo acotados.
- `evidence.py`, `console/__init__.py`: lectura segura, respuesta de imagen y cabeceras/barreras locales.
- `demo_healing.py`: nombres exclusivos, reserva y lease de capturas.
- `console/static/console.js`, `.css`, `index.html`: carreras, caché, foco/estado, fallback, contraste, pie de imagen y movimiento.
- `demo-portal/index.html`: región de credenciales ficticias privada.
- `tests/conftest.py`, `test_captures.py`, `test_e2e_captures.py`, `test_demo_console.py`, `test_gf006_review.py`: aislamiento de entorno y regresiones verificables.
- Documentación: esta revisión, investigación, arquitectura, guía inicial, runbook y backlog. Los otros cambios pendientes que ya existían al empezar se conservaron.

Las capturas de revisión son temporales de pytest, con datos sintéticos; no se agregaron imágenes a rutas versionadas. `git diff --check`, sintaxis Python/JavaScript y escaneo de secretos del árbol publicable se registran en el cierre. El escaneo es heurístico, no una certificación ni una lectura de `.env`.

### Registro de cierre

| Comprobación ejecutada | Resultado |
|---|---|
| `backend/.venv/bin/pytest -c backend/pyproject.toml backend/tests --tb=short` | **300 passed**, 1 advertencia deprecatoria, 139,24 s. Incluye **273 offline + 27 E2E**. |
| `backend/.venv/bin/python -m compileall -q backend/ghostflow backend/tests` | Aprobada. |
| `node --check backend/ghostflow/console/static/console.js` | Aprobada. |
| `git diff --check` | Sin errores. Se comprobaron además espacios finales en archivos nuevos publicables. |
| Escaneo heurístico de los 92 archivos de texto publicables actuales | Sin coincidencias pendientes de claves privadas, tokens de proveedor, JWT o asignaciones de credenciales. Detectores comprobados con canarios sintéticos; sin leer `.env` y sin imprimir valores. No equivale a una auditoría de todo el historial Git. |
| Diff de `dsl/`, `ai/`, `execution/recovery.py` y `pyproject.toml` | Sin cambios. No se modificaron modelo, reglas de recuperación ni dependencias. |
| HEAD y publicación | **`0140007`**, sin commits ni push. |

No se detectaron regresiones en las fases existentes durante la suite. Quedan los límites de despliegue local, la deuda acotada del puente de Playwright, la validación manual de VoiceOver y las recomendaciones de producto documentadas. El siguiente paso de mayor impacto para el hackathon es revisar la ampliación del navegador y las dos etiquetas de procedencia; streaming permanece sin implementar.
