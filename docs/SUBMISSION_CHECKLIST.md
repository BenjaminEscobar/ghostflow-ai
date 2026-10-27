# Submission Checklist — Meta Global AI Developer Hackathon 2026

**Proyecto:** GhostFlow AI · **Categoría:** Indie Developer · **Tema:** Build for Your World
**Participante responsable:** único participante, propietario del repositorio (GitHub `BenjaminEscobar`). Escribe tu nombre tal como figura en la inscripción.
**Fecha límite:** miércoles 4 de noviembre de 2026, 9:00 p. m. ET (8:00 p. m. Guatemala)

## 1. Estado de las reglas (revisado el 2026-10-27)

| Punto | Fuente oficial | Estado |
|---|---|---|
| Fecha límite: 4 nov, 9 PM ET | Página del evento en Cerebral Valley | **Confirmado** (el organizador indica que las fechas pueden cambiar) |
| Track Indie: "creativity, technical ambition, and whether it works" | Página del evento | **Confirmado** (sin ponderaciones publicadas) |
| Juzgamiento 4–8 nov, cierre 9 nov | Página del evento | Confirmado |
| Duración del video (~3 min) | Solo fuentes no oficiales | **Sin confirmar**: verificar en el formulario de entrega |
| Formato y alojamiento del video | — | **Sin confirmar** |
| Repositorio público, licencia | Solo fuentes no oficiales ("public repo") | **Sin confirmar** |
| Uso obligatorio de Meta Model API / modelos abiertos | Solo fuentes no oficiales | Sin confirmar, aunque GhostFlow lo cumple (Muse Spark real en GF-002 y GF-003) |
| "Solo trabajo hecho durante el evento" | Solo fuentes no oficiales | Sin confirmar. GhostFlow empezó desde cero el 26-10-2026 (historial Git) |
| Declaración del uso de asistentes de programación | — | **Sin confirmar**: si el formulario lo pide, usar la sección 4 |

La página oficial decía que las *Official Rules* se publicarían antes del 26 de octubre y que los seleccionados debían aceptarlas. No pude leer el texto de esas reglas desde aquí (la página de *waiver* no muestra contenido sin sesión). **Antes de enviar, abre las Official Rules y el formulario con tu cuenta y actualiza esta tabla.** No tomes como requisito lo que aparece aquí como "sin confirmar".

## 2. Entregables

- [ ] Repositorio actualizado en `main`, con todos los commits de GF-001 a GF-004 revisados y subidos (no subir antes de la revisión final).
- [ ] Visibilidad del repositorio según las reglas (pública si se exige). Antes de hacerlo público: `gitleaks git .` sin hallazgos.
- [ ] Licencia elegida por el propietario, si las reglas la piden (está en el backlog; es decisión tuya).
- [ ] Video de la demo (duración según el formulario; guion en [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md)).
- [ ] Texto de presentación (versión corta y larga en `DEMO_SCRIPT.md` → *Submission blurb*).
- [ ] Track: **Indie Developer**.
- [ ] Enlace al repositorio y al video en el formulario.
- [ ] Capturas, si el formulario las pide: consola en escenario C (Self-Healing) y panel Meta Intelligence.

## 3. Verificación técnica antes de enviar

- [ ] `make setup && make test`: 231 aprobadas.
- [ ] `make test-e2e`: 16 aprobadas (Chromium en macOS).
- [ ] `git status` limpio; `git diff --check` sin errores.
- [ ] `gitleaks git .` y `gitleaks dir .` sin hallazgos. `backend/.env` existe solo en local y está ignorado.
- [ ] Desde un clon limpio en otra carpeta: `make setup`, `make test`, `make console` y `make demo-rehearsal` funcionan sin editar código.
- [ ] La consola muestra *Recorded evidence* de GF-003 con `Real Meta Model API · 1 request`.
- [ ] El video no muestra claves, `.env`, notificaciones ni datos personales.
- [ ] Las etiquetas de la consola (*Live / Saved / Recorded*, *Real / Simulated*) coinciden con la narración.

## 4. Autoría y uso de herramientas de IA

- **Participante:** un solo participante responsable. No agregar colaboradores ni atribuir código a personas que no participaron.
- **IA dentro del producto:** Meta Model API, modelo `muse-spark-1.3` en modo Standard (sin entrenamiento con los datos), para interpretar procesos (GF-002) y proponer elementos equivalentes en el self-healing (GF-003).
- **Asistente de programación:** el desarrollo se hizo con ayuda de Claude Code (Anthropic) como asistente de programación, bajo la dirección, revisión y decisiones del participante. Los commits llevan el trailer `Co-Authored-By: Claude`, que identifica a la herramienta, no a una persona. Si el formulario pregunta por herramientas de IA, decláralo así.
- **Código previo:** no se reutilizó código de proyectos privados (Ginger, YAP ni otros). El portal "Andina Servicios" y todos sus datos son ficticios.

## 5. Consumo de Meta registrado

| Fase | Solicitudes reales | Detalle |
|---|---|---|
| GF-002 | Según el informe de GF-002 | Interpretación de workflows |
| GF-003 | 1 | Validación A/B/C del 2026-10-27: 751 tokens de entrada / 482 de salida (423 de razonamiento), 3,4 s |
| GF-004 | **0** | Desarrollo y pruebas solo con mock, fixtures y la evidencia guardada |
| Video final | 0 o 1 | 1 solo si decides usar `make demo-live` (requiere tu autorización) |

## 6. Plan de los últimos días

| Fecha | Tarea |
|---|---|
| 28–29 oct | Revisar GF-004, `make test-e2e` en Mac, ensayos con `make demo-rehearsal` |
| 30–31 oct | Confirmar reglas y formulario; grabar el video (2 o 3 tomas) |
| 1–2 nov | Edición, revisión de seguridad final y repositorio |
| 3 nov | Envío anticipado (un día de margen) |
| 4 nov | Solo correcciones de emergencia antes de las 8:00 p. m. (Guatemala) |
