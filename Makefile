VENV := backend/.venv
BIN := $(VENV)/bin

.PHONY: setup test test-e2e test-all portal portal-variant backend console schema up down compare-ai demo-healing demo-healing-meta \
	demo-rehearsal demo-live demo-evidence

setup:  ## Create the virtualenv, install the backend and Playwright's Chromium
	python3 -m venv $(VENV)
	$(BIN)/pip install -e "backend[dev]"
	$(BIN)/playwright install chromium

test:  ## Fast tests (no browser)
	cd backend && .venv/bin/pytest -m "not e2e"

test-e2e:  ## Browser tests: replay, resilience, network guard, full extension flow
	cd backend && .venv/bin/pytest -m e2e

test-all:
	cd backend && .venv/bin/pytest

portal:  ## Serve the demo portal on http://localhost:8080
	python3 -m http.server 8080 --bind 127.0.0.1 --directory demo-portal

portal-variant:  ## Serve the GF-003 "crear-requerimiento" variant on http://localhost:8080
	rm -rf .portal-variant && cp -R demo-portal .portal-variant && rm -rf .portal-variant/variants
	cp demo-portal/variants/crear-requerimiento/*.html .portal-variant/
	python3 -m http.server 8080 --bind 127.0.0.1 --directory .portal-variant

backend:  ## Run the API on http://localhost:8010 (SQLite; AI provider from GHOSTFLOW_AI_PROVIDER, mock by default)
	cd backend && .venv/bin/uvicorn ghostflow.main:app --host 127.0.0.1 --port 8010

schema:  ## Regenerate schemas/workflow.schema.json from the Pydantic models
	$(BIN)/python -m ghostflow.dsl.schema > schemas/workflow.schema.json

up:  ## Full stack in Docker (PostgreSQL + portal + backend)
	docker compose up --build

down:
	docker compose down

compare-ai:  ## Mock vs configured provider on the example recording (1 Meta request at most)
	cd backend && .venv/bin/python -m ghostflow.ai.compare ../docs/examples/demo-recording.json

demo-healing:  ## GF-003 scenarios A/B/C with the offline mock (no Meta request). Needs port 8080 free
	cd backend && .venv/bin/python -m ghostflow.demo_healing --out ../gf003-evidence-mock.json

demo-healing-meta:  ## GF-003 scenarios A/B/C; scenario C sends at most ONE request to Meta (asks to confirm)
	cd backend && .venv/bin/python -m ghostflow.demo_healing --provider meta --out ../gf003-evidence-meta.json

# -- GF-004 Demo Console -----------------------------------------------------------------
# Visual mode for filming: pause between steps and keep the final page on screen.
DEMO_DELAY_MS ?= 800
DEMO_HOLD_MS ?= 4000
DEMO_WORKFLOW ?= ../docs/examples/workflow-meta-interpreted.json
# New data for the replay (the workflow was recorded with other values). Fictitious.
DEMO_INPUTS = --input usuario=demo --input contrasena=ghostflow-demo \
	--input "nombre_del_cliente=Lucía Méndez" --input correo_del_cliente=lucia.mendez@ejemplo.test \
	--input categoria=soporte --input "descripcion=Actualizar el contacto de facturación"
DEMO_RUN = cd backend && .venv/bin/python -m ghostflow.demo_healing --headed \
	--delay-ms $(DEMO_DELAY_MS) --hold-ms $(DEMO_HOLD_MS) --workflow-file $(DEMO_WORKFLOW) $(DEMO_INPUTS)

console:  ## Backend + Demo Console on http://127.0.0.1:8010/console/ (read-only views)
	@echo "Demo Console: http://127.0.0.1:8010/console/"
	cd backend && .venv/bin/uvicorn ghostflow.main:app --host 127.0.0.1 --port 8010

demo-rehearsal:  ## A/B/C in a visible browser, offline mock for C (SIMULATED, 0 Meta requests). Port 8080 free
	$(DEMO_RUN) --provider mock

demo-live:  ## A/B/C in a visible browser; C asks the REAL Meta API: max 1 request, after typing 'meta'
	$(DEMO_RUN) --provider meta

demo-evidence:  ## Rebuild docs/evidence/gf003-meta-recovery.json from the local GF-003 Meta evidence (no AI call)
	cd backend && .venv/bin/python -m ghostflow.evidence import-gf003 ../gf003-evidence-meta.json \
		--out ../docs/evidence/gf003-meta-recovery.json
