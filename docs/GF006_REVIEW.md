# GF-006: guía para la revisión independiente

**Revisión independiente ejecutada:** ver [informe de seguridad, arquitectura, UX y movimiento](GF006_INDEPENDENT_REVIEW.md) y [estudio de navegador continuo](GF006_LIVE_BROWSER_RESEARCH.md). Las notas de implementación y resultados iniciales que siguen describen la entrega previa a esa revisión; el informe independiente documenta las correcciones y la verificación final. No se implementó streaming.

Base: `0140007` (GF-005 estable: 231 pruebas offline + 16 E2E). Objetivo: mostrar en la consola capturas reales del Chromium que controla GhostFlow, y un fantasma que reacciona solo a eventos reales.

## Archivos

| Archivo | Cambio |
|---|---|
| `backend/ghostflow/execution/capture.py` | **Nuevo.** Contiene: `CaptureRecorder` (privacidad antes de capturar, límites, escritura atómica), `CapturePolicy`, `CaptureBudget`, `is_capturable_url`, `sensitive_steps` y `prune_captures`. |
| `backend/ghostflow/execution/playwright_web.py` | Añade el parámetro opcional `capture=` (protocolo `StepCapture`) y `snap()` en 4 puntos. Con `capture=None` el comportamiento es idéntico al de GF-005. |
| `backend/ghostflow/evidence.py` | Añade `CaptureBox`, `CaptureRef`, `ScenarioView.captures` (≤ 80), el evento `capture` en `apply_event`, el saneo de `page` y `EvidenceStore.capture_file`. |
| `backend/ghostflow/console/__init__.py` | Añade `GET /api/demo/captures/{name}/{capture_id}` y caché privada solo para las imágenes servidas. |
| `backend/ghostflow/demo_healing.py` | Añade `--no-captures`, `captures_allowed` (nunca rutas versionadas), un presupuesto compartido entre escenarios y retención de 5 ejecuciones. |
| `backend/ghostflow/console/static/*` | Añade el visor de capturas, la tira de miniaturas, el conmutador *Real capture / Reconstructed*, las etiquetas de procedencia, el SVG de Ghost 2.0, los estados nuevos, las etapas del playback y `#agent-status`. |
| `backend/tests/test_captures.py` | **Nuevo.** 22 pruebas offline. |
| `backend/tests/test_e2e_captures.py` | **Nuevo.** 2 pruebas E2E en Chromium. |
| `docs/*`, `README.md` | Arquitectura, runbook (§10.1), backlog, checklist y esta guía. |

No cambiaron ni el DSL, ni `recovery.py` / `Healer`, ni la configuración ni los secretos. Tampoco hay dependencias nuevas ni migraciones.

## Contrato del evento `capture`

El adaptador llama a `observer("capture", meta)`, y `meta` se valida como `CaptureRef` (`extra="forbid"`):

```json
{"id": "C-007-s8-before", "step_id": "s8", "phase": "before", "taken_at": "…Z",
 "page": "http://127.0.0.1:8080/new-request.html", "width": 1280, "height": 720,
 "bytes": 84211, "box": {"x": 0.21, "y": 0.33, "w": 0.4, "h": 0.05}, "masked": 0, "skipped": null}
```

- `phase` toma uno de estos valores: `after` (tras `navigate` o `wait_for_url`), `before` (elemento resuelto, antes de actuar), `not_found` (localizadores agotados, antes de `recovery_started`) o `final` (tras las verificaciones).
- `skipped` vale `sensitive_step`, `sensitive_page`, `off_origin`, `limit`, `rate`, `too_large` o `error`. En ese caso no existe imagen y el endpoint responde 404.

## Privacidad: puntos a revisar

1. La decisión se toma **antes** del `screenshot`, en `CaptureRecorder.take`, y en este orden:
   1. paso sensible;
   2. página contaminada (*taint*), que solo se limpia en `after` de `navigate` o `wait_for_url`;
   3. límite de capturas;
   4. frecuencia;
   5. origen loopback que además figure en `allowed_origins`.
2. `page.screenshot(mask=[password, autocomplete password, cc-*])` actúa sobre el viewport recortado. No se capturan otras pestañas, ventanas ni el sistema operativo.
3. Las imágenes se guardan en `demo-evidence/captures/` (ignorado por Git). `captures_allowed` impide escribir en `docs/evidence` o en cualquier otra ruta del repositorio.
4. El endpoint:
   - solo sirve la colección `runs`;
   - exige un id listado sin `skipped`;
   - comprueba con `resolve()` que la ruta queda dentro de `captures/<bundle>/`, así que rechaza traversal y enlaces simbólicos hacia fuera;
   - limita el tamaño a 1 MB y solo acepta GET.
5. La consola crea `<img>` desde ids ya validados, sin `innerHTML`, iframe, WebSocket ni CDN. La CSP no cambió (`img-src 'self' data:`).

## Riesgos conocidos y decisiones

- `snap("before")` llama a `scroll_into_view_if_needed` y `bounding_box` sobre el elemento ya resuelto. Desplaza la página igual que lo haría Playwright al actuar; no hace clic ni escribe nada.
- Un paso **recuperado** no tiene imagen `before`, porque el healer actúa sin pasar por el adaptador y GF-006 no lo modifica. Su imagen es la `not_found`, y la siguiente (`s7`) muestra el resultado. No se fabrica un recuadro sobre el elemento elegido; queda en el backlog.
- *Evaluating* solo existe en el playback de evidencia guardada y lleva la etiqueta *playback pace, not the real duration*. En vivo, el estado es *Investigating… recovery in progress*, porque el backend no emite eventos internos del modelo.
- Cada petición de imagen vuelve a leer el bundle (≤ 2 MB) para comprobar la lista. Es aceptable con ≤ 40 capturas y caché del navegador. Si se escala, convendría cachear el índice.
- La frecuencia se limita con una ventana deslizante de 10 capturas por segundo; nunca se añaden esperas. Si una captura se omite por frecuencia, el motivo queda visible en *Browser captures* (vista Developer).
- La evidencia GF-003 grabada no tiene imágenes, se sigue mostrando como *Reconstructed view* y no se le generan capturas.
- **Cambio de evidencia (corrección tras la primera E2E en macOS).** La consola no escuchaba `hashchange`: al navegar a otro `#colección%2Fnombre`, seguía mostrando la evidencia anterior. Ahora:
  - un cambio de hash selecciona la fuente;
  - `select()` vuelve a *Real capture* en cada evidencia;
  - `loadDetail()` descarta las respuestas de una fuente que el usuario ya abandonó.
  La E2E alterna dos veces entre evidencia con y sin capturas.
- **Ensayo integrado (corrección tras `make demo-rehearsal` en macOS).** El Makefile pasaba `--headed`, y el runner lanza un Chromium nuevo por escenario, que macOS traía al primer plano por encima de la consola. Cambios:
  - ahora es headless por defecto, y `DEMO_HEADED=1` sirve para diagnosticar;
  - la consola se detiene brevemente en los eventos reales que terminaron entre dos consultas (ver `ARCHITECTURE.md`);
  - consulta cada 500 ms mientras hay una ejecución en vivo y usa doble búfer de imágenes.
  La nueva E2E `test_integrated_rehearsal_runs_headless_and_the_console_follows_a_b_c` ejecuta el runner real con el mock y comprueba que la consola sigue A, B y C en vivo. No hay imagen `before` del elemento recuperado.

## Pruebas

- **Ejecutadas en el sandbox:** `pytest -m "not e2e"` con 255 aprobadas (231 previas, sin modificar, más 24 nuevas). Además, comprobaciones de render con jsdom de las evidencias grabada, mock y mock con capturas sintéticas (sin errores de JS) y la secuencia de estados del replay.
- **Pendientes en macOS** (Chromium no arranca en el sandbox): `pytest -m e2e`, con 18 pruebas (16 previas más `test_e2e_captures.py`). Validan las imágenes JPEG reales por paso, que no haya archivos de `s3`/`s4`, *Recorded capture* en la consola y *Reconstructed view* en GF-003.
- **Visual:** `docs/DEMO_RUNBOOK.md` §10.1.

## Demo reproducible (sin Meta)

```bash
make console                    # terminal 1
make demo-rehearsal             # terminal 2: A/B/C con mock y capturas en demo-evidence/captures/
open http://127.0.0.1:8010/console/
```
