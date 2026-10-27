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
make test            # 226 pruebas offline (sin navegador, sin red)
make test-e2e        # 13 pruebas con Chromium real (extensión, replay, self-healing, consola)
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
| `make demo-rehearsal` | Mock offline (determinista) | **0** | `Simulated AI · offline mock · no Meta request` (franja amarilla) |
| `make demo-live` | **Meta Muse Spark real** | **Máx. 1** (sin reintentos, tope duro 1). Pide escribir `meta` para confirmar | `Real Meta Model API · muse-spark-1.3 · 1 request` (azul) |
| `make demo-healing` / `make demo-healing-meta` | Igual que los anteriores, sin modo visual (GF-003) | 0 / máx. 1 | Igual |
| `make console` | Ninguna: solo lee | 0 | — |
| Compilar una grabación con `GHOSTFLOW_AI_PROVIDER=meta` | Interpretación GF-002 | 1 por compilación | Panel *Meta Intelligence* → `Real` |

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
4. Chromium se abre y ejecuta **A** (portal original), **B** (portal rediseñado, self-healing apagado: falla en `s6`) y **C** (portal rediseñado, self-healing encendido: recupera `s6` y termina). La consola sigue cada paso: Pending → Running → Completed / Failed / Recovered.
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

```bash
make test        # 226 offline: incluye 28 de la consola (sanitización, real vs simulado, evidencias, API, CSP)
make test-e2e    # 13 con Chromium: incluye 3 de GF-004 (modo visual, observador, consola en vivo y grabada)
git diff --check
```

Las pruebas ignoran `backend/.env` y bloquean toda red externa. Solo se permiten los puertos loopback que abre cada prueba, así que nunca consumen Meta.

## 10. Procedimiento para grabar el video final

1. Cierra notificaciones (*No molestar*), pestañas personales y cualquier archivo `.env`. Pon el navegador a 1920×1080 y el zoom de la consola al 100 % (o al 110 % si el texto se ve pequeño en el video).
2. Ensayo completo con `make demo-rehearsal` y grabación de prueba de 3 minutos.
3. Decide si el escenario C del video usa:
   - **la ejecución real ya guardada** (*Recorded evidence*, 0 solicitudes nuevas), presentándola como evidencia grabada, o
   - **una ejecución real nueva**: `make demo-live`, 1 solicitud. Requiere tu autorización explícita y la clave en `backend/.env`.
4. Para la toma del navegador usa el runner con `--headed` (ya incluido). Graba la ventana de Chromium y la consola lado a lado, o alterna entre ellas siguiendo el shot list de [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md).
5. No muestres terminales con variables de entorno ni el contenido de `backend/.env`.
6. Revisa el video: la etiqueta *Simulated* no debe aparecer en una toma narrada como Meta real, y viceversa.

## 11. Limitaciones conocidas

- La consola es de **solo lectura**: no inicia ejecuciones. Se lanzan con `make demo-*`, con la API o con Swagger.
- La actualización en vivo usa *polling* (≈1 s) de un archivo JSON que escribe el runner. No hay WebSocket.
- La evidencia de GF-003 importada no guardaba la lista de candidatos ni los tiempos por paso. La consola lo indica, y los estados de los pasos previos al fallo se derivan de las reglas del ejecutor (`derived_steps`). Las ejecuciones nuevas sí guardan ambos.
- El runner requiere el puerto 8080 libre (origen de la grabación).
- La consola no muestra capturas de pantalla del navegador; la toma visual es la ventana de Chromium.
- Probado en macOS con Chrome; no se probó en Windows ni Linux en esta fase.
- Las reglas oficiales de presentación del hackathon no se pudieron confirmar desde fuentes oficiales (ver [`SUBMISSION_CHECKLIST.md`](SUBMISSION_CHECKLIST.md)).
