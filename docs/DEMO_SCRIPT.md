# Demo Script — GhostFlow AI

Target length: **180 seconds** (provisional). Before recording, confirm the official video limit in the submission form (see [`SUBMISSION_CHECKLIST.md`](SUBMISSION_CHECKLIST.md)). If it differs, use the emergency version or trim the "Learning" block first.

**Core message:** *Show GhostFlow a workflow once. It learns, understands, executes, and safely adapts when the interface changes.*

Setup and commands: [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md).

---

## Full script (≈180 s)

### 0:00–0:20 · Problem

> **VO (EN):** Every company has processes people repeat every day — log in, open a form, type the same fields, submit. Traditional automation helps, until someone renames a button. Then the script breaks, and a person has to fix it.

*Notas (ES):* Ritmo tranquilo. Sin demo todavía: el portal *Andina Servicios* en pantalla y el cursor quieto. Duración real ≈ 18 s.

### 0:20–1:00 · Learning

> **VO (EN):** With GhostFlow, you just do the task once. I start the recorder in Chrome, log in, open a new request, fill in the customer, category and description, and submit. GhostFlow captures meaning, not pixels: roles, labels and where each action leads. Passwords are never recorded — the field is marked as a secret input. When I stop, GhostFlow compiles the session into a structured workflow: fifteen steps, six parameters, two automatic checks.

*Notas (ES):* Graba en vivo con la extensión contra `make portal`. Ve rápido por el formulario: el público no necesita leerlo. Muestra el popup con *Grabación enviada*. Si el tiempo aprieta, corta la toma del formulario a la mitad (salto de edición). Ajusta los números (pasos, parámetros, verificaciones) a lo que muestre la consola para tu grabación.

### 1:00–1:30 · Intelligence

> **VO (EN):** Then Meta's Muse Spark model reads the workflow and explains it back: the business purpose, the expected outcome, preconditions, which steps are critical — like signing in and creating the request — and notes for a human reviewer. Muse Spark documents the process; it never writes executable code. The workflow itself stays deterministic and validated.

*Notas (ES):* Consola → panel **Meta Intelligence** con la insignia *Real* (workflow con interpretación real de Muse Spark). Señala *Purpose*, *Critical steps* y *Review notes*. Frase clave: "documents, never executes".

### 1:30–2:00 · Execution

> **VO (EN):** Now GhostFlow runs it by itself, with new data — a different customer, a different category. Playwright replays every step, and GhostFlow verifies the result: the confirmation message and the final page. Scenario A: passed.

*Notas (ES):* `make demo-rehearsal` o `make demo-live`, con Chromium sin ventana (pausa de 800 ms) y la consola en *Live* mostrando sus capturas reales (*Live capture*). Muestra *Input data for this run* (Lucía Méndez) y *Verifications 2/2*.

### 2:00–2:40 · Self-Healing

> **VO (EN):** Now the interface changes. The "New request" link is gone; there's a toolbar with "Create requirement" instead — new text, new id, new structure. Without self-healing, GhostFlow stops safely at step six: every recorded locator failed, and it doesn't guess. With self-healing on, GhostFlow extracts the real candidates on the page and asks Muse Spark which one matches the intent. Muse Spark picks "Crear requerimiento" with high confidence, because it leads to the same form. But the model doesn't get the final word: GhostFlow checks ten deterministic rules — allowed domain, expected destination, no side effects, still visible right before acting — and only then clicks, once. The process completes, and the workflow definition is unchanged.

*Notas (ES):* Escenario B: tarjeta roja *Failed at s6* y la lista de localizadores tachados. Escenario C: panel **Self-Healing** con "Nueva solicitud → Crear requerimiento", candidatos con el elegido resaltado, cita de Muse Spark, medidor de confianza y la lista de validaciones en verde. Si es evidencia grabada, deja visible la insignia *Recorded evidence* y no digas "ahora mismo". Si es ejecución real nueva, la insignia dice *Real Meta Model API · 1 request*.

### 2:40–3:00 · Close

> **VO (EN):** Task done, verified, with full evidence: which model was consulted, latency, tokens, and proof the workflow didn't change. GhostFlow keeps the AI where it helps — understanding and adapting — and keeps execution in validated contracts, inside allowed domains, with no secrets stored. Show GhostFlow a workflow once. It learns, understands, executes, and safely adapts when the interface changes.

*Notas (ES):* Panel **Verifiable evidence**: *DSL unchanged ✓*, *Meta consulted*, latencia y tokens. Termina en el logo de GhostFlow con la frase central en pantalla.

---

## Emergency short version (≈60 s)

> GhostFlow learns business workflows by watching you do them once in Chrome — no passwords recorded. Meta's Muse Spark explains the process: purpose, critical steps, review notes. GhostFlow replays it with new data and verifies the result. When the interface changes and every recorded locator fails, GhostFlow asks Muse Spark to pick the equivalent element among the page's real candidates — then checks ten deterministic rules before acting once. Same workflow, unchanged, completed. Show GhostFlow a workflow once. It learns, understands, executes, and safely adapts when the interface changes.

*Notas (ES):* Úsala si el formulario limita la duración o si falla una toma. Plano único: la consola en *Recorded evidence* (escenarios B → C) más 10 s del navegador.

---

## Shot list

| # | Time | Screen | What must be visible | Source |
|---|---|---|---|---|
| 1 | 0:00 | Portal *Andina Servicios*, login | Fictitious brand, demo credentials hint | `make portal` |
| 2 | 0:20 | Chrome + extension popup | *Iniciar grabación*, recording state | Extension |
| 3 | 0:30 | Portal form | Typing customer, category, description; password field typed (not recorded) | Live |
| 4 | 0:50 | Popup | *Grabación enviada: <id>* | Extension |
| 5 | 1:00 | Console · Meta Intelligence | Badge **Real**, purpose, critical steps, review notes, `meta · muse-spark-1.3` | `make console` |
| 6 | 1:30 | Console *Live* with real captures (headless Chromium) | Steps advancing; timeline Pending → Running → Completed; inputs with new data | `make demo-rehearsal` / `demo-live` |
| 7 | 1:55 | Console · scenario A | *Completed*, Verifications 2/2 | Console |
| 8 | 2:00 | Portal variant dashboard | Toolbar *Crear requerimiento* (no "Nueva solicitud") | Runner, scenario B |
| 9 | 2:08 | Console · scenario B | **Failed at s6**, 4 struck-out locators, *Not attempted* | Console |
| 10 | 2:15 | Console · scenario C · Self-Healing | Nueva solicitud → **Crear requerimiento**, candidates, Muse Spark quote, confidence *high* | Console |
| 11 | 2:30 | Console · validation list | 10 green checks, "The model proposed; GhostFlow verified…" | Console |
| 12 | 2:40 | Console · Verifiable evidence | PASSED, Meta consulted, latency/tokens, **DSL unchanged ✓** | Console |
| 13 | 2:55 | Title card | GhostFlow logo + core message | Edit |

Always keep the top-right badges in frame in shots 6–12: they state whether the run is *Live*, *Saved* or *Recorded* and whether the AI is *Real* or *Simulated*.

---

## Claims to avoid

- "Recovers any application" or "works on any website". Say: *recovers when an equivalent element exists and passes the checks*.
- "Fully autonomous" business actions. Say: *conservative policy — no buttons or submits are recovered by default, no repeated submissions*.
- Presenting a mock or recorded run as live Meta. The badges must match the narration.
- "Learns new selectors automatically". The DSL is **not** modified; there is no persistent selector learning.

---

## Submission blurb

**Short (≈280 characters):**
GhostFlow AI learns web workflows by watching a person do them once, uses Meta's Muse Spark to understand them, replays them with new data, and — when the UI changes — lets Muse Spark propose the equivalent element while deterministic checks decide.

**Long (≈150 words):**
GhostFlow AI turns a single human demonstration into a reusable, verifiable automation. A Chrome extension records the task semantically — roles, labels, destinations — without ever storing passwords. The backend compiles it into a validated workflow DSL, and Meta's Muse Spark (`muse-spark-1.3`) explains the process: purpose, expected outcome, preconditions, critical steps and review notes. Playwright replays the workflow with new data and verifies the outcome. When the interface changes and every recorded locator fails, GhostFlow extracts the page's real candidates and asks Muse Spark which one matches the step's intent; the executor then enforces ten deterministic rules — allowed origins, expected destination, uniqueness, no side effects, a live re-check — before acting once. Every run produces auditable evidence, visible in the Demo Console: which model was consulted, latency, tokens, and proof that the workflow definition did not change. Built solo for the Meta Global AI Developer Hackathon 2026 (Indie Developer).
