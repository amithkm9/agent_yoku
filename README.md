# agent-yoku

Cross-source agent over Asato's **JIRA tickets** + **GitHub PRs**. Ingests both
into MongoDB, indexes them with OpenAI embeddings, and exposes a deepagent
(LangChain `deepagents` + `gpt-5.4-mini`) through:

- **FastAPI** REST API with JWT auth + multi-tenant mongo isolation
- **React** (Vite + TypeScript) frontend
- **Click** CLI for ingest, embed, link, smoke-test, user management

```
  ┌──────────────┐    JWT      ┌────────────────────┐    invoke    ┌──────────────────┐
  │  React UI    │ ──────────▶ │  FastAPI           │ ───────────▶ │ deepagent        │
  │  (Vite :5173)│             │  (uvicorn :8000)   │              │ + 11 tools       │
  └──────────────┘             │  + tenant ContextVar│              │ + source registry│
                               └──────┬─────────────┘              └──────┬───────────┘
                                      │                                    │
                                      ▼ db-per-tenant                      ▼
                               ┌────────────────────────────────────────────────┐
                               │   MongoDB (single cluster)                     │
                               │   agent_yoku / agent_yoku_<tenant_id>        │
                               │   collections: jira_tickets, github_prs,       │
                               │   unified_users, chat_sessions, chat_messages, │
                               │   auth_users …                                 │
                               └────────────────────────────────────────────────┘
```

## Project layout

```
agent-yoku/
├── pyproject.toml  Makefile  Dockerfile  .dockerignore
├── README.md  .env.example  .gitignore  .python-version
├── .pre-commit-config.yaml
├── agent_yoku/                    ← installable Python package
│   ├── config.py                   # Pydantic Settings (JWT secret, Mongo, OpenAI, etc.)
│   ├── exceptions.py
│   ├── log.py                      # session_id ContextVar + optional Sentry
│   ├── cli.py                      # Click entry: `agent-yoku …`
│   ├── main.py                     # FastAPI app factory + lifespan
│   ├── deps.py                     # JWT, current_user, password hashing
│   ├── schemas.py                  # request/response Pydantic models
│   ├── scheduler.py                # APScheduler background sync loop
│   ├── sync_service.py             # per-tenant full pipeline orchestration
│   ├── routers/                    # FastAPI route handlers
│   │   ├── auth.py                 #   login/signup + JWT
│   │   ├── sessions.py             #   session CRUD
│   │   ├── chat.py                 #   SSE streaming chat
│   │   ├── connectors.py           #   on-demand sync + connector config
│   │   └── stats.py                #   analytics
│   ├── middleware/                 # FastAPI middlewares
│   │   ├── rate_limit.py           #   per-client rate limiting
│   │   └── request_context.py      #   request correlation IDs
│   ├── models/                     # Pydantic DTOs for stored entities
│   ├── connectors/                 # 🔌 drop a new source folder here
│   │   ├── base.py                 #   Connector contract + auto-discovery
│   │   ├── jira/   (client, ingest, users_*)
│   │   └── github/ (client, ingest, users_ingest)
│   ├── storage/
│   │   ├── mongo.py                #   tenant-aware collection accessors
│   │   ├── tenancy.py              #   ContextVar + db name routing
│   │   ├── sessions.py             #   conversation persistence
│   │   ├── unified_users.py        #   JIRA ↔ GitHub user join
│   │   ├── connector_configs.py    #   per-tenant connector credentials
│   │   ├── freshness.py            #   data freshness tracking
│   │   └── backfill.py             #   author_email backfill
│   ├── embeddings/embed.py
│   ├── linking/pr_to_jira.py
│   ├── analysis/consistency.py     # JIRA/GitHub data integrity checks
│   ├── eval/retrieval.py           # retrieval quality evaluation
│   ├── agent/
│   │   ├── agent.py                # deepagent factory
│   │   ├── chat.py                 # CLI ask/final_answer helpers
│   │   ├── sources.py              # source registry — one entry per connector
│   │   └── tools.py                # 11 tools (7 narrow + 4 mongo escape hatches)
│   └── utils/                      # cross-cutting helpers
│       ├── bson.py · jira_keys.py · http.py
├── web/                            # React + Vite frontend
│   ├── package.json  vite.config.ts  tsconfig.json
│   └── src/
│       ├── main.tsx · App.tsx · styles.css
│       ├── pages/Login.tsx  pages/Chat.tsx  pages/Settings.tsx
│       └── lib/api.ts              # typed fetch client
├── scripts/
│   ├── dump_schemas.py             # Pydantic → JSON Schema
│   ├── agent_smoke.py              # agent regression suite
│   └── retrieval_eval.py           # score retrieval quality on golden set
├── tests/                          # 37 test files: unit + integration
└── schemas/                        # generated JSON Schemas (gitignored)
```

## Quickstart

```bash
# 1. Mongo running locally
brew services start mongodb-community

# 2. Install
poetry install
poetry run pre-commit install      # one-time

# 3. Configure
cp .env.example .env               # JIRA_TOKEN, GITHUB_TOKEN, OPENAI_API_KEY,
                                    # JWT_SECRET (rotate in prod!)
                                    # falls back to ~/Desktop/.env

# 4. Pull data + index + link
make refresh-all                   # ~10 min on first run, idempotent

# 5. Bootstrap an admin user
poetry run agent-yoku auth create-user --email you@asato.ai --tenant asato --admin

# 6. Run
make api                           # http://localhost:8000  (FastAPI + Swagger /docs)
make web-install                   # one-time: npm install in web/
make web                           # http://localhost:5173  (React UI)
make web-lint                      # TypeScript static checks for the React app
```

Sign in at `http://localhost:5173` with the exact tenant name you created.

### Auto-sync

The API runs a background scheduler that re-runs the full refresh pipeline
(ingest → embed → unify → link → backfill) for **every** tenant with a
configured connector, using each tenant's own stored credentials. It starts
with the API and is controlled by env:

- `AUTO_SYNC_ENABLED` (default `true`) — set `false` to turn it off.
- `SYNC_INTERVAL_MINUTES` (default `60`) — cadence between runs.

The first run fires one interval after boot (not at startup), and runs are
non-overlapping. Auto-sync is always off under `ENV=test`/`ci`. On-demand
`POST /api/connectors/{name}/sync` still works regardless.

## CLI

```
$ agent-yoku --help

Commands:
  ingest             Pull source data into mongo
    jira             …  AS-project tickets
    jira-users       …  JIRA user directory
    github           …  AsatoCorp PRs (last 365d, all non-archived repos)
    github-users     …  GitHub org members
  embed              Generate embeddings for any docs with embedding=null
  link               Reverse-link PRs onto their referenced JIRA tickets
  unify-users        Build unified_users by joining JIRA + GitHub users
  backfill-pr-emails Backfill author_email from unified_users
  refresh-all        Full pipeline: ingest → embed → unify → link → backfill
  list-connectors    Enumerate registered data connectors
  status             Mongo collection counts (current tenant)
  consistency        Report JIRA/GitHub inconsistencies (done-no-PR, merged-no-ticket)
  chat <Q…>          One-shot agent query
  api                Launch FastAPI on :8000
  auth               User management
    create-user      …  Create an account in a tenant
    list-users       …  List users in a tenant
```

## Auth + multi-tenancy

One mongo cluster, one db per tenant:
- every tenant gets its own db: `agent_yoku_<tenant_id>`
- the db is auto-created on first signup for that tenant

Tenant flow:
1. **Signup** at `POST /api/auth/signup?tenant=<id>` → first user becomes admin.
2. JWT carries `sub`, `email`, `tenant_id`, `is_admin`, `exp`.
3. **Every** authenticated request sets `tenancy.current_tenant_var` in
   `current_user` dependency → every `storage/mongo.py::*_collection()` call
   routes to that tenant's db. Cross-tenant access is impossible by construction.

Passwords are bcrypt-hashed (72-byte cap enforced).
JWT secret comes from `settings.jwt_secret` — **rotate before any non-local deploy.**

## Adding a new connector

1. Copy `agent_yoku/connectors/jira/` → `agent_yoku/connectors/<source>/`.
2. Replace `client.py` with your auth/REST helpers — use `@make_retry("yourname", log)` from `agent_yoku.utils`.
3. Adapt `ingest.py` to upsert into a new mongo collection.
4. In `__init__.py` declare `META: ConnectorMeta = {...}`.
5. Optional `users_ingest.py` for member directories.
6. Add a CLI subcommand in `agent_yoku/cli.py`.
7. Register the collection in `agent_yoku/storage/mongo.py::ALLOWED_COLLECTIONS`.
8. Add a `SourceSpec` to `agent_yoku/agent/sources.py::SOURCES` — `filter` / `get` / `linked` / `semantic_search` then cover the source automatically, no new tools needed.

`agent-yoku list-connectors` will auto-discover it.

## Architecture decisions

- **Mongo is the source of truth.** No vector DB — embeddings live as a
  `embedding` field on each doc; cosine runs in-memory via numpy.
- **Single planning agent.** One deepagent plans with `write_todos` and answers
  over a source-agnostic toolkit (no sub-agents).
- **Source registry + generic escape hatch.** Per-source knowledge (collection,
  key shape, filterable fields, links) lives in `agent_yoku/agent/sources.py`, so
  the tools are source-agnostic: `get` / `linked` auto-route by key shape across
  every source, `filter(source, …)` does exact filters with alias resolution,
  alongside `semantic_search`, `resolve_user`, `data_freshness`, `who_knows`.
  `mongo_query` / `mongo_count` / `describe_collection` / `list_collections`
  cover analytics the narrow tools don't. Tool errors return `{"error": "..."}`
  so the agent can adapt rather than crash.
- **Cross-source link.** PRs auto-extract `AS-XXXX` from branch / title / body
  into `jira_keys`. Reverse pass writes `linked_prs` onto each JIRA ticket.
- **Sessions** persist in mongo (`chat_sessions`, `chat_messages`); compact
  history (user + final-AI per past turn) replays to the agent on follow-ups.
- **Tenant routing via ContextVar.** Set by `current_user` in FastAPI; storage
  helpers read it transparently. No tenant string passed through call chains.
- **Pydantic Settings** centralizes config; `SecretStr` keeps tokens out of logs.
- **Tenacity retries** on every external HTTP call (shared `utils/http.make_retry`).

## Observability

- Rotating file log at `logs/agent_yoku.log` + stderr.
- Per-session correlation: every record carries `[sid=<8-char>]`.
- `LOG_JSON=1` switches the file handler to JSON-line output.
- `SENTRY_DSN=…` auto-attaches the sentry SDK (no-op if package missing).

## Quality gates

CI runs on every push to `main` and every pull request via GitHub Actions.

```
make fmt          # autoflake + ruff --fix + black
make lint         # ruff + black --check
make test         # pytest
make pre-commit   # all pre-commit hooks
make web-lint     # TypeScript static checks
make web-build    # React production build
make schemas      # regenerate JSON Schema docs under ./schemas/
make agent-smoke  # 5-query agent regression suite (hits OpenAI)
```

Pre-commit hooks: autoflake, ruff, black, standard hygiene.

## Docker

```bash
docker build -t agent-yoku .
docker run --rm -p 8000:8000 --env-file .env agent-yoku
# API on http://localhost:8000  /docs for Swagger
```

For batch ingest:
```bash
docker run --rm --env-file .env agent-yoku python -m agent_yoku.cli refresh-all
```

## Data model

| Collection        | Holds                                                  |
|-------------------|--------------------------------------------------------|
| `jira_tickets`    | AS-project tickets + embedding + `linked_prs`          |
| `users`           | JIRA user directory                                    |
| `github_prs`      | AsatoCorp PRs + embedding + `jira_keys` + `author_email`|
| `github_users`    | AsatoCorp org members                                  |
| `unified_users`   | JIRA ↔ GitHub cross-walk                               |
| `chat_sessions`   | One row per New Session click                          |
| `chat_messages`   | Every persisted agent message                          |
| `auth_users`      | Login users (per tenant, bcrypt-hashed passwords)      |

JSON Schemas live in `./schemas/` after `make schemas`.

## License

Internal — Asato.
