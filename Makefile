VENV := backend/.venv
BIN := $(VENV)/bin

.PHONY: setup test test-e2e test-all portal backend schema up down

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

backend:  ## Run the API on http://localhost:8000 (SQLite, mock AI)
	cd backend && .venv/bin/uvicorn ghostflow.main:app --host 127.0.0.1 --port 8010

schema:  ## Regenerate schemas/workflow.schema.json from the Pydantic models
	$(BIN)/python -m ghostflow.dsl.schema > schemas/workflow.schema.json

up:  ## Full stack in Docker (PostgreSQL + portal + backend)
	docker compose up --build

down:
	docker compose down
