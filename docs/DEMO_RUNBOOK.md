# Demo Runbook — GhostFlow AI (GF-004)

Procedimiento para preparar, ensayar y grabar la demostración desde un clon limpio. Todo corre en local (macOS probado) y **no consume Meta** salvo en los dos comandos marcados como *REAL*.

## 1. Requisitos

| Requisito | Uso |
|---|---|
| Python 3.11+ (probado con 3.14) | Backend, runner de la demo y pruebas |
| Google Chrome | Cargar la extensión y grabar la tarea humana |
| Chromium de Playwright 1.63 (`make setup` lo instala) | Reproducción automática |
| `make` | Comandos documentados |
| Opcional: clave de Meta Model API | Solo para la interpretación real y para el escenario C real |

No hace falta Docker, PostgreSQL, Redis ni ningún servicio externo: la demo usa SQLite y servidores estáticos locales.

## 2. Preparación desde un clon limpio

```bash
git clone https://github.com/BenjaminEscobar/ghostflow-ai.git
cd ghostflow-ai
make setup           # venv, dependencias, Chromium de Playwright
make test            # 279 pruebas offline (sin navegador, sin red)
make test-e2e        # 16 pruebas con Chromium real (extensión, replay, self-healing, consola)
```

### Credenciales

- **Portal demo:** `demo` / `ghostflow-demo`. Son ficticias y el propio portal las muestra en su pantalla de ingreso.
- **Meta Model API:** solo en `backend/.env` (ignorado por Git) o como variable exportada:

  ```bash
  # backend/.env — nunca en Git, en la extensión, en capturas ni en el video
  GHOSTFLOW_META_API_KEY=<tu clave>
  GHOSTFLOW_META_MODEL=muse-spark-1.3
  ```

  La clave no aparece en logs, evidencias ni en la consola. Antes de grabar pantalla, cierra cualquier editor que tenga `backend/.env` abierto.

## 3. Puertos

| Puerto | Proceso | Comando |
|---|---|---|
| 8080 | Portal demo "Andina Servicios", el origen de la grabación | `make portal` (grabación) **o** el runner A/B/C (lo levanta él mismo) |
| 8010 | API + **Demo Console** (`/console/`) | `make console` (igual que `make backend`) |

El runner A/B/C necesita el **8080 libre**: detén `make portal` antes de ejecutarlo. Los dos usan el mismo origen para que el workflow grabado sea válido sin cambios.

## 4. Mock frente a Meta real

| Comando | IA en el escenario C | Solicitudes a Meta | Etiqueta en la consola |
|---|---|---|---|
| `make demo-rehearsal` | Mock offline (determinista) | **0** | `Recovery · Simulated AI · offline mock · 0 Meta requests` (franja amarilla) |
| `make demo-live` | **Meta Muse Spark real** | **Máx. 1** (sin reintentos, tope duro 1). Pide escribir `meta` para confirmar | `Recovery · Real Meta Model API · muse-spark-1.3 · 1 request` (azul) |
| `make demo-healing` / `make demo-healing-meta` | Igual que los anteriores, sin modo visual (GF-003) | 0 / máx. 1 | Igual |
| `make console` | Ninguna: solo lee | 0 | — |
| Compilar una grabación con `GHOSTFLOW_AI_PROVIDER=meta` | Interpretación GF-002 | 1 por compilación | `Interpretation · Meta Muse Spark · muse-spark-1.3 · recorded` y panel *Meta Intelligence* → `Real` |

Cada etapa de IA lleva **su propia** etiqueta, derivada de su propio registro: la interpretación (guardada en el workflow al compilarlo) y la recuperación (guardada en el paso). No hay una etiqueta global de IA: un ensayo con mock sobre el workflow interpretado por Meta muestra *Interpretation · Meta Muse Spark · recorded* y *Recovery · Simulated AI* a la vez, y ambas son correctas.

Los escenarios A y B nunca consultan a Meta. La consola **nunca** presenta una evidencia guardada como ejecución en vivo: `Live` solo aparece mientras el runner está actualizando el archivo. La evidencia versionada se muestra como `Recorded evidence · replay of a saved run, not live`, y el botón *Replay* añade una franja `REPLAY of saved evidence · not a live execution`.

## 5. Arranque y parada

```bash
# Terminal 1: API + consola (solo lectura)
make console                 # http://127.0.0.1:8010/console/

# Terminal 2: según el momento de la demo
make portal                  # para grabar con la extensión (Ctrl+C al terminar)
make demo-rehearsal          # ensayo A/B/C visual con mock (requiere 8080 libre)
make demo-live               # A/B/C visual con Meta real (máx. 1 solicitud, confirma con "meta")
```

Para detener: `Ctrl+C` en cada terminal. El runner cierra el navegador y el servidor del portal al terminar cada escenario, también si falla. Un `Ctrl+C` a mitad de ejecución deja la evidencia marcada como `Interrupted`.

### Modo visual

| Variable | Defecto | Demo |
|---|---|---|
| `GHOSTFLOW_STEP_DELAY_MS` / `--delay-ms` | `0` | `800` (`DEMO_DELAY_MS` en el Makefile) |
| `GHOSTFLOW_FINAL_HOLD_MS` / `--hold-ms` | `0` | `4000` (`DEMO_HOLD_MS`), máx. 30000 |

Para cambiarlos no hace falta editar código: `make demo-rehearsal DEMO_DELAY_MS=1200 DEMO_HOLD_MS=6000`. La pausa se aplica *entre* pasos y no repite acciones ni añade reintentos. Las pruebas usan los valores por defecto (`0`), así que no se ralentizan.

## 6. Cómo ejecutar la demo visual

1. `make console` y abre `http://127.0.0.1:8010/console/` en Chrome (ventana a 1920×1080 si vas a grabar).
2. Deja marcado **Follow live runs**: la consola cambia sola a la ejecución nueva en cuanto aparece.
3. En otra terminal: `make demo-rehearsal` (o `make demo-live`).
4. Chromium se ejecuta **sin ventana** (headless): lo único visible es la consola, que muestra sus capturas reales. Ejecuta **A** (portal original), **B** (portal rediseñado, self-healing apagado: falla en `s6`) y **C** (portal rediseñado, self-healing encendido: recupera `s6` y termina). La consola sigue cada paso: Pending → Running → Completed / Failed / Recovered. Para diagnosticar con la ventana de Chromium: `make demo-rehearsal DEMO_HEADED=1`. En macOS, cada escenario abre una ventana nueva que pasa al primer plano.
5. Al terminar, la evidencia queda en `demo-evidence/<fecha>-<proveedor>.json` (ignorada por Git) y aparece en el selector como *Saved run*.

El workflow por defecto es `docs/examples/workflow-meta-interpreted.json`: el workflow grabado el 2026-10-27 con interpretación **real** de Muse Spark (GF-002). Se exportó sin los valores personales de la grabación y sin parámetros `utm_*`. El runner lo ejecuta con datos nuevos y ficticios (`Lucía Méndez`, `lucia.mendez@ejemplo.test`, categoría `soporte`), definidos en `DEMO_INPUTS` del Makefile.

Para regenerar ese archivo desde tu base local:

```bash
cd backend && .venv/bin/python -m ghostflow.evidence export-workflow --id <id> --out ../docs/examples/workflow-meta-interpreted.json
```

## 7. Cómo reproducir la recuperación

- **Sin gastar Meta:** `make demo-rehearsal`. El mock elige el único enlace que lleva a la ruta esperada (`/new-request.html`), y las mismas validaciones deterministas de GF-003 deciden.
- **Con Meta real (máx. 1 solicitud):** `make demo-live` y escribe `meta` cuando lo pida. Sin la confirmación no se crea el proveedor de Meta.
- **Evidencia real ya obtenida, sin nueva solicitud:** en la consola elige *Recorded evidence → A/B/C self-healing · Meta Muse Spark · GF-003 validation*. Es la ejecución real del 2026-10-27 (1 solicitud, 751/482 tokens, 3,4 s), importada con `make demo-evidence`. Pulsa **Replay evidence** para animarla; la franja amarilla indica que es una reproducción.
- **Con la extensión y la API (flujo completo):** graba contra `make portal` y compila. Luego detén el portal y lanza `make portal-variant` (mismo origen), reinicia el backend con `GHOSTFLOW_HEALING_ENABLED=true` y ejecuta `POST /api/workflows/{id}/runs`. La consola muestra ese workflow en *Backend workflows* con sus ejecuciones guardadas.

## 8. Cómo verificar las evidencias

En la consola (panel **Verifiable evidence**):

- **Workflow result** y **Final verifications**: texto `Solicitud registrada correctamente` y ruta `/confirmation.html`.
- **Meta consulted**: `Yes · real Meta Model API request` solo si el intento lo hizo el proveedor `meta`. Con mock dice `No · simulated by mock`.
- **Latency · tokens** del intento y **Meta requests (demo)**: total de solicitudes del runner y su tope.
- **DSL unchanged**: SHA-256 del workflow antes y después de las tres ejecuciones.

En el panel **Self-Healing**: localizadores agotados, candidatos extraídos (con el elegido resaltado), justificación y confianza del modelo, y las 10 validaciones deterministas.

En archivo:

```bash
python3 -m json.tool demo-evidence/<archivo>.json | less
python3 -m json.tool docs/evidence/gf003-meta-recovery.json | less
```

Por API (solo lectura): `GET /api/demo/sources`, `GET /api/demo/sources/{recorded|runs|workflows}/{nombre}`.

## 9. Pruebas

La revisión independiente y sus resultados finales están en [GF006_INDEPENDENT_REVIEW.md](GF006_INDEPENDENT_REVIEW.md). Las cifras iniciales de GF-006 que siguen son la baseline previa a esa revisión.

```bash
make test        # 279 offline (273 de la baseline ae4d519 + 6 del pulido final: insignias y chips por etapa, procedencia, diálogo ampliado, jerarquía de la vista Demo y paleta sobria con contraste AA)
make test-e2e    # 36 con Chromium (27 de la baseline + 9 del pulido final: visor ampliado a 1440/1024/820, GF-003 reconstruida, procedencia Demo/Developer, ensayo A/B/C con el visor ampliado y composición "navegador primero" medida a 1440/1024/820)
git diff --check
```

Las pruebas ignoran `backend/.env` y bloquean toda red externa. Solo se permiten los puertos loopback que abre cada prueba, así que nunca consumen Meta.

## 10. Comprobación visual de la consola en macOS (GF-005)

1. `make console` y abre `http://127.0.0.1:8010/console/#recorded%2Fgf003-meta-recovery` en Chrome.
2. **Vista Demo** (por defecto), de arriba abajo:
   - **Barra superior:** marca GhostFlow AI, selector *Demo / Developer* y el estado *Online*. La configuración actual del backend (*Current backend settings · Default AI · Self-healing*) solo se muestra en Developer, para que no se confunda con la evidencia elegida.
   - **Barra lateral:** fuente de evidencia, con dos líneas discretas (*Recorded / Saved / Live* y *Real / Simulated*), y los escenarios con títulos breves: *A — Original / Completed*, *B — Changed UI / Stopped safely*, *C — Changed UI / Recovered*. A 1024 y 820 px se convierte en una franja sobre el contenido.
   - **Franja superior compacta (~130 px):** la mascota (88 px, con su estado), *Learned workflow · nombre · N steps*, *Automation that adapts* con una sola frase, la **fila de procedencia** (*Interpretation · …* y *Recovery · …*, para el escenario elegido) y **Replay evidence**.
   - **Una línea de escenario** (*Scenario C — Changed UI · self-healing on*, resultado, tiempo total si la evidencia lo guarda y estado del DSL) y las **páginas del flujo** (*Home → Dashboard → New request → Confirmation*), en una barra baja.
   - **Navegador** (~75 % del ancho, empieza hacia los 300 px a 1440×900): en su barra, la etiqueta *Live / Recorded capture / Reconstructed view*, la dirección, el conmutador *Real capture / Reconstructed* y **Expand browser**. Debajo, el paso en foco, el pie de la captura y las miniaturas.
   - **Columna derecha (~25 %), alineada con el borde superior del navegador:** el estado de GhostFlow, el panel **Recovery** (*Recovered / Stopped safely / Not needed*, elemento original tachado, elemento elegido, *10/10 passed*, fuente *Meta / Mock / None* y *View details*), **Verification** (*2/2 passed* y cada comprobación) y, debajo, los pasos agrupados por página.
   - Candidatos, latencia, tokens, confianza, hashes y validaciones detalladas siguen en **Developer** (*Meta Intelligence*, *AI provenance by stage*, *Self-Healing audit* y *Verifiable evidence*), igual que la lista de eventos, que también aparece en el visor ampliado.
3. Recorre los escenarios con el teclado: `Tab` hasta un escenario y luego las flechas, `Inicio` y `Fin`. La mascota cambia: orgullosa en C (recuperado), alerta en B (detenido de forma segura), feliz en A.
4. Haz clic en una página del flujo o en un paso de la lista para inspeccionarlo. Vuelve a hacer clic para volver al paso clave.
5. Pulsa **Replay evidence**: aparece la franja azul clara *REPLAY of saved evidence · not a live execution*.
6. Cambia a **Developer** (o abre `…/console/?view=developer#recorded%2Fgf003-meta-recovery`). Están las auditorías completas: localizadores agotados, candidatos, las 10 validaciones, tokens, latencia, proveedor y modelo, SHA-256 completo y procedencia. **Raw JSON** abre la evidencia tal como la sirve el backend.
7. Ancho adaptable: con las DevTools (`⌥⌘I` → *Toggle device toolbar*) prueba 1440, 1024 y 820 px. No debe aparecer scroll horizontal ni texto comprimido en columnas estrechas (la E2E `test_demo_and_developer_views_are_usable_on_desktop_and_tablet` lo comprueba en esos tres anchos).
8. Accesibilidad: en *Configuración del Sistema → Accesibilidad → Pantalla → Reducir movimiento* las animaciones se detienen. Con VoiceOver (`⌘F5`), la mascota se anuncia con su estado (p. ej. *GhostFlow mascot: recovered safely*) y cada cambio de estado se anuncia una sola vez (*GhostFlow: Investigating… recovery in progress*).
9. Con una ejecución nueva (`make demo-rehearsal`), la consola cambia sola a *Live* y muestra las capturas reales (*Live capture*); la línea *Recovery · Simulated AI* de la barra lateral y el chip *Recovery · Mock* siguen visibles. Ver las secciones 10.1, 10.2 y 10.3.

### 10.1 Navegador integrado con capturas reales (GF-006)

1. Terminal 1: `make console`. Abre `http://127.0.0.1:8010/console/` con **Follow live runs** marcado.
2. Terminal 2: `make demo-rehearsal` (mock, 0 solicitudes a Meta). El runner guarda las capturas en `demo-evidence/captures/<fecha>-mock/` (ignorado por Git) y lo indica al arrancar. Usa `--no-captures` para desactivarlas.
3. Mientras corre:
   - el centro de la tarjeta muestra la captura real de Chromium con la etiqueta **Live capture**;
   - el recuadro azul marca el elemento que se va a usar;
   - debajo aparece una tira de miniaturas que se va llenando.
   - En B, paso `s6`: la consola se detiene un momento en la captura del panel sin el elemento (*Recorded element not found here · stopped safely without acting*), con la mascota en *Stopped safely*.
   - En C, paso `s6`: la captura muestra el panel sin el elemento grabado y la mascota pasa a **Recovery started** (o *Investigating… recovery in progress*, si la consola alcanza a ver la recuperación en curso). Luego aparece **Recovered safely** con *GhostFlow then chose "Crear requerimiento"*, y la captura siguiente (`s7`) es la página a la que llevó el elemento elegido.
   - Con el mock, la recuperación dura milisegundos y ocurre entre dos consultas de la consola. Cuando una consulta revela un evento ya terminado (recuperación, detención segura o escenario completado), la consola se detiene unos 1,6 s en ese evento real, en orden y con el texto en pasado, y luego sigue a la ejecución, que nunca se frena. Si eliges un escenario o un paso, esa pausa se cancela.
4. Al terminar, la misma ejecución aparece como *Saved run* con la etiqueta **Recorded capture**. Haz clic en el paso `s3` (contraseña): la imagen es la del paso anterior y la nota dice *a sensitive input is typed here, so it is never captured*. Comprueba que en la carpeta no hay archivos `*-s3-*` ni `*-s4-*`.
5. **Reconstructed** (conmutador sobre el navegador) vuelve a la vista reconstruida de GF-005. En *Recorded evidence → GF-003* no hay capturas: la etiqueta es **Reconstructed view** y el conmutador no aparece.
6. **Replay evidence** en C reproduce *investigating → evaluating (Playback · reviewing recorded checks) → recovered* a ritmo de presentación. La franja indica *playback pace, not the real duration*.
7. Developer → *Verifiable evidence → Browser captures*: número de capturas, MB y omitidas por motivo.
8. Pruebas en tu Mac:
   ```bash
   cd backend && .venv/bin/pytest -m e2e tests/test_e2e_captures.py -v
   make test-all   # 279 offline + 36 E2E
   ```

### 10.2 Visor ampliado y procedencia de la IA por etapa (pulido final)

Sin llamadas a Meta. Usa `make console` en una terminal.

1. **Procedencia por etapa.** Abre `http://127.0.0.1:8010/console/#recorded%2Fgf003-meta-recovery`. En la franja superior, la fila de procedencia del escenario C dice *Interpretation · None* y *Recovery · Meta real · recorded* (azul claro: Meta real, grabado); el panel Recovery indica la fuente *Meta Muse Spark · recorded · 1 request*. En B, *Recovery · Off* y el panel dice *Self-healing off*. Los estados posibles son *Recovery · Meta real · live* (azul sólido, solo durante una ejecución real en curso), *Meta real · recorded* (azul claro), *Mock* (neutro discontinuo; el panel dice *Mock · simulated · 0 Meta requests*), *… · in progress*, *… · playback*, *Not needed*, *Not attempted* y *Off*.
   - **Developer → Meta Intelligence → AI provenance by stage** conserva el detalle: *1 · Workflow interpretation* (*No interpretation stored*) y *2 · Self-Healing recovery · scenario C* (*Meta Muse Spark · muse-spark-1.3*, *Real model call, recorded evidence: shown from the file, not a new request*, *1 real Meta request recorded in this evidence (cap 1)* y el resultado).
2. **Ensayo con mock.** Terminal 2: `make demo-rehearsal` (0 solicitudes). En la vista Demo, para C: *Interpretation · Meta recorded* y *Recovery · Mock*; mientras se recupera, *Recovery · Mock · in progress*. En Developer, *AI provenance by stage* muestra la fecha de compilación de la interpretación, *The browser run itself is real: Playwright drove Chromium through the portal* y la nota *Two independent AI stages, not one execution*, que dice que ninguna etapa hizo una solicitud nueva a Meta.
3. **Visor ampliado.** Pulsa **⤢ Expand browser** (sobre el navegador). El mismo visor pasa a un diálogo que ocupa casi toda la pantalla:
   - la imagen se muestra a su tamaño real como máximo (nunca ampliada por encima de sus píxeles) y cabe entera en pantalla;
   - se mantienen la etiqueta *Live capture / Recorded capture / Reconstructed view*, el pie de la captura, la nota de los pasos sensibles y la tira de miniaturas (a la derecha);
   - la cabecera muestra el tipo de evidencia (*Live*, *Saved run*, *Recorded evidence* o *Replay of saved evidence · playback pace*), *Step N of M*, el estado del paso y una barra de progreso por paso;
   - la columna derecha muestra el estado de GhostFlow, las dos etapas de procedencia y los eventos.
   Con el foco en el diálogo, **←/→** recorren los pasos, **Esc** o *Exit expanded view* vuelven a la distribución normal y el foco regresa al botón *Expand browser*. Mientras está abierto, el resto de la página queda inerte.
4. **En vivo.** Abre el visor ampliado **antes** de lanzar `make demo-rehearsal`: la consola cambia sola a la ejecución nueva y el diálogo sigue A, B y C con *Live capture*, sin cerrarse.
5. **Sin capturas.** En *Recorded evidence → GF-003*, el visor ampliado muestra la reconstrucción más grande, con la etiqueta *Reconstructed view* y *not a screenshot*.
6. **Anchos.** Con las DevTools prueba 1440×900, 1024×1366 y 820×1180: sin scroll horizontal; dentro del diálogo, a 1024 y 820 la columna derecha pasa debajo de la imagen, y a 820 el diálogo ocupa toda la pantalla.
7. **Replay.** Pulsa *Replay evidence* y luego *Expand browser*: la reproducción continúa dentro del diálogo, rotulada como *Replay of saved evidence · playback pace*, y la recuperación figura como *playback* hasta llegar a `s6`.
8. Pruebas en tu Mac:
   ```bash
   cd backend && .venv/bin/pytest -m e2e tests/test_e2e_presentation.py -v
   ```

### 10.3 Composición final de la vista Demo (navegador primero)

Sin llamadas a Meta. Con `make console`, abre una evidencia con capturas (por ejemplo, la última de `make demo-rehearsal`) y la grabada GF-003.

1. **1440×900** (ventana de Chrome a ese tamaño o DevTools): el borde superior del navegador queda hacia los 300 px (la E2E exige ≤ 350) y la captura se ve sin desplazarse. El panel derecho (estado de GhostFlow → Recovery → Verification) empieza a la misma altura que el navegador.
2. **1024** (1024×768 o 1024×1366): la barra lateral pasa a una franja; navegador y panel siguen lado a lado y alineados arriba. El botón *Expand browser* queda como icono ⤢ (mismo nombre accesible).
3. **820×1180:** el panel y la lista de pasos pasan debajo del navegador, en dos columnas. Sin scroll horizontal ni texto en columnas estrechas.
4. **Paleta:** neutros (#F5F7FA, blanco, bordes #E2E8F0), azul #1877F2 en botones, navegación, selección y progreso; el violeta solo en el marcador de recuperación (punto de C, nodo del paso recuperado) y en la mascota. La E2E mide en la captura de pantalla que el violeta ocupa ≤ 5 % (≈ 0,1–0,3 % medido).
5. **Escenarios:** *Original / Completed*, *Changed UI / Stopped safely*, *Changed UI / Recovered*. Con VoiceOver se anuncian como *Scenario C · Changed UI · Recovered (Recovered s6 · completed)*.
6. **Recovery → View details** abre Developer en la auditoría (*Deterministic validation by GhostFlow*).
7. Pruebas en tu Mac:
   ```bash
   cd backend && .venv/bin/pytest -m e2e tests/test_e2e_presentation.py tests/test_e2e_console.py -v
   ```

## 11. Procedimiento para grabar el video final

1. Cierra notificaciones (*No molestar*), pestañas personales y cualquier archivo `.env`. Pon el navegador a 1920×1080 y el zoom de la consola al 100 % (o al 110 % si el texto se ve pequeño en el video).
2. Ensayo completo con `make demo-rehearsal` y grabación de prueba de 3 minutos.
3. Decide si el escenario C del video usa:
   - **la ejecución real ya guardada** (*Recorded evidence*, 0 solicitudes nuevas), presentándola como evidencia grabada, o
   - **una ejecución real nueva**: `make demo-live`, 1 solicitud. Requiere tu autorización explícita y la clave en `backend/.env`.
4. Graba solo la consola: `make demo-rehearsal` (o `demo-live`) ejecuta Chromium sin ventana y la consola muestra sus capturas reales. Para que el navegador se lea en el video, pulsa **Expand browser** antes de lanzar el runner (sección 10.2). Antes de las tomas de C, señala la fila de procedencia de la franja superior: interpretación de Meta grabada y recuperación simulada (ensayo) o real (evidencia grabada o `demo-live`). Si además quieres tomas de la ventana de Chromium, usa `DEMO_HEADED=1`. En macOS cada escenario abre una ventana nueva en primer plano, así que tendrás que alternar entre ellas.
5. No muestres terminales con variables de entorno ni el contenido de `backend/.env`.
6. Revisa el video: la etiqueta *Simulated* no debe aparecer en una toma narrada como Meta real, y viceversa. Narra cada etapa con su propia etiqueta: *Interpretation* y *Recovery* pueden tener fuentes distintas.

## 12. Limitaciones conocidas

- Seguridad y cambios posteriores a la implementación inicial: [revisión GF-006](GF006_INDEPENDENT_REVIEW.md). Las capturas se sirven sin caché persistente, se rechazan symlinks en todos sus componentes y la retención respeta ensayos activos. El API de demo admite únicamente Host `localhost`, `127.0.0.1` o `[::1]`; no es un servicio autenticado para usuarios remotos.

- La consola es de **solo lectura**: no inicia ejecuciones. Se lanzan con `make demo-*`, con la API o con Swagger.
- La actualización en vivo usa *polling* (≈1 s) de un archivo JSON que escribe el runner. No hay WebSocket. Las capturas se cargan una vez por id (caché del navegador y de la página).
- Las capturas son imágenes del viewport, no una sesión interactiva ni video. Solo existen para ejecuciones nuevas del runner sobre el portal local; la evidencia GF-003 grabada no tiene imágenes. El estado *Evaluating* solo aparece en el playback de una evidencia guardada, porque el backend no emite un evento de evaluación en vivo.
- La evidencia de GF-003 importada no guardaba la lista de candidatos ni los tiempos por paso. La consola lo indica, y los estados de los pasos previos al fallo se derivan de las reglas del ejecutor (`derived_steps`). Las ejecuciones nuevas sí guardan ambos.
- El runner requiere el puerto 8080 libre (origen de la grabación).
- Sin capturas (evidencia GF-003, `--no-captures` o workflows del backend), el navegador de la vista Demo es una **reconstrucción** a partir de la evidencia y lo indica (*Reconstructed view*).
- Probado en macOS con Chrome; no se probó en Windows ni Linux en esta fase.
- Las reglas oficiales de presentación del hackathon no se pudieron confirmar desde fuentes oficiales (ver [`SUBMISSION_CHECKLIST.md`](SUBMISSION_CHECKLIST.md)).
