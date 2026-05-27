.PHONY: help install install-dev fmt lint test pre-commit web-lint web-build ingest-jira ingest-github ingest-users embed link refresh-all status ui api web web-install chat clean schemas agent-smoke retrieval-eval

POETRY := poetry run
PY := $(POETRY) python
CLI := $(PY) -m agent_yoku.cli

help:
	@echo "agent_yoku — common dev tasks"
	@echo
	@echo "Setup:"
	@echo "  make install         poetry install (runtime only)"
	@echo "  make install-dev     poetry install + pre-commit hook"
	@echo
	@echo "Quality:"
	@echo "  make fmt             black + ruff --fix + autoflake"
	@echo "  make lint            ruff + black --check"
	@echo "  make test            pytest"
	@echo "  make pre-commit      run all pre-commit hooks"
	@echo "  make web-lint        TypeScript static checks"
	@echo "  make web-build       production frontend build"
	@echo
	@echo "Data:"
	@echo "  make ingest-jira     pull JIRA tickets"
	@echo "  make ingest-github   pull GitHub PRs from all non-archived repos"
	@echo "  make ingest-users    pull JIRA + GitHub users"
	@echo "  make embed           embed any missing"
	@echo "  make link            link PRs <-> JIRA"
	@echo "  make refresh-all     full ingest + embed + link pipeline"
	@echo "  make status          mongo collection counts"
	@echo
	@echo "Run:"
	@echo "  make api             FastAPI backend on :8000 (--reload)"
	@echo "  make web             React dev server on :5173 (run web-install first)"
	@echo "  make web-install     npm install (one-time)"
	@echo "  make chat Q='...'    one-shot CLI query"
	@echo "  make agent-smoke     Run the agent regression suite"
	@echo "  make retrieval-eval TENANT=asato  Score retrieval on golden queries"

install:
	poetry install --no-root --without dev

install-dev:
	poetry install --no-root
	poetry run pre-commit install

fmt:
	$(PY) -m autoflake --in-place --recursive --remove-all-unused-imports --remove-unused-variables .
	$(PY) -m ruff check --fix .
	$(PY) -m black .

lint:
	$(PY) -m ruff check .
	$(PY) -m black --check .

test:
	$(PY) -m pytest

pre-commit:
	$(PY) -m pre_commit run --all-files

web-lint:
	cd web && npm run lint

web-build:
	cd web && npm run build

schemas:
	$(PY) scripts/dump_schemas.py

agent-smoke:
	$(PY) scripts/agent_smoke.py

retrieval-eval:
	$(PY) scripts/retrieval_eval.py --tenant $(TENANT)

ingest-jira:
	$(CLI) ingest jira

ingest-github:
	$(CLI) ingest github

ingest-users:
	$(CLI) ingest jira-users
	$(CLI) ingest github-users

embed:
	$(CLI) embed

link:
	$(CLI) link

refresh-all:
	$(CLI) refresh-all

status:
	$(CLI) status

api:
	$(CLI) api --reload

web-install:
	cd web && npm install

web:
	cd web && npm run dev

chat:
	@if [ -z "$(Q)" ]; then echo "usage: make chat Q='your question'"; exit 1; fi
	$(CLI) chat "$(Q)"

clean:
	rm -rf .pytest_cache .ruff_cache __pycache__ */__pycache__
	find . -name "*.pyc" -delete
