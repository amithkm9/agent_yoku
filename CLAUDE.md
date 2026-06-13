# yoku — agent guide

Cross-source agent over a team's JIRA tickets, GitHub PRs, and Slack messages.
Ingests them into MongoDB, embeds them, and answers questions through a `deepagents` planning
agent (`gpt-5.4-mini`) behind a FastAPI + React app. Full overview: `README.md`.

## The one rule that saves you

This codebase is **schema-driven**. Three registries are the single source of
truth — the agent tools read everything from them, nothing about a collection is
hardcoded in the tool layer:

- **Pydantic models** (`yoku/schemas/`) — each collection's description
  (the model docstring) + its fields (`description` + `display`/`filterable`).
- **Source registry** (`yoku/agent/sources.py`) — one `SourceSpec` per
  source, key-routing facts only: `name`, `collection`, `label`, `key_pattern` +
  `key_example`, `sort_field` (recency), and `embeddable` (in the semantic index).
- **Relationship registry** (`yoku/schemas/relationships.yaml`) —
  cross-collection joins.

So: **to add or change a data source, edit the registries — never the tools or
prompts.** `filter` / `get` / `linked` / `semantic_search` / `list_collections`
all derive from the registries and cover a new source automatically. If you find
yourself editing `yoku/agent/tools/` to teach the agent about a
collection, stop — you're in the wrong layer.

To add a connector, follow the recipe: `docs/adding-a-connector.md`.

## Layout (the parts you'll touch)

- `yoku/connectors/<source>/` — one folder per external source (auto-discovered)
- `yoku/schemas/` — Pydantic DTOs = collection schemas (+ `relationships.yaml`)
- `yoku/agent/` — the deepagent: `tools/` (auto-discovered), `sources.py`,
  `schema_registry.py`, `agent.py`
- `yoku/pipeline/` — ingest → embed → unify → link → metrics + sync/scheduler
- `yoku/proactive/` — the gap engine + memory engine (`episodes.py`,
  `reply_understanding.py`, `beliefs.py`, `judge.py`, `orchestrator.py` …)
- `yoku/actions/` — proposal-gated write-back (propose → approve → execute → audit)
- `yoku/routers/` — FastAPI routes  ·  `yoku/db/mongo.py` — tenant-aware accessors
- `yoku/cli.py` — Click CLI (`yoku …`)
- `web/` — React (Vite + TS) frontend
- `tests/` — pytest

The memory engine (how yoku learns from replies and follows through) is its own
doc: `docs/memory-engine.md`.

## Commands

```bash
make install-dev     # install + dev deps
make fmt             # autoflake + ruff --fix + black  (run before committing)
make lint            # ruff + black --check
make test            # pytest
make pre-commit      # all pre-commit hooks
make agent-smoke     # 5-query agent regression suite (hits OpenAI — costs tokens)
make web-lint        # frontend TypeScript checks
make web-build       # frontend production build
```

## Before you commit

1. `make fmt && make lint && make test` must pass.
2. If you touched the agent's behavior, run `make agent-smoke`.
3. If you changed a Pydantic model, run `make schemas` (regenerates `./schemas/`).
4. Keep changes surgical — every changed line should trace to the task.

## Conventions

- **Immutable data** — build new objects, don't mutate.
- **Mongo is the source of truth.** No vector DB; embeddings live as an
  `embedding` field, cosine runs in-memory via numpy.
- **Tenancy is implicit** — set by `current_user` into a `ContextVar`; storage
  helpers read it. Don't thread a tenant string through call chains.
- **Secrets** via Pydantic Settings + `SecretStr`. Never hardcode tokens.
- **External HTTP** goes through `yoku.utils.http.make_retry` (tenacity).
- Tools return JSON-serializable data and surface errors as `{"error": "..."}`
  so the agent can adapt — they never raise into the agent loop.
