# agent_yoku

Cross-source agent over Asato's **JIRA tickets** + **GitHub PRs**. Ingests both
into MongoDB, indexes them with OpenAI embeddings, and exposes a deepagent
(LangChain `deepagents` + `gpt-5.4-mini`) through:

- **FastAPI** REST API with JWT auth + multi-tenant mongo isolation
- **React** (Vite + TypeScript) frontend
- **Streamlit** legacy UI (kept around; will be retired)
- **Click** CLI for ingest, embed, link, smoke-test, user management

```
  ┌──────────────┐    JWT      ┌────────────────────┐    invoke    ┌──────────────────┐
  │  React UI    │ ──────────▶ │  FastAPI           │ ───────────▶ │ deepagent        │
  │  (Vite :5173)│             │  (uvicorn :8000)   │              │ + 14 tools       │
  └──────────────┘             │  + tenant ContextVar│              │ + 2 sub-agents   │
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
agent_yoku/
├── pyproject.toml  Makefile  Dockerfile  .dockerignore
├── README.md  .env.example  .gitignore  .python-version
├── .pre-commit-config.yaml
├── agent_yoku/                    ← installable Python package
│   ├── config.py                   # Pydantic Settings (incl. jwt_secret, default_tenant_id)
│   ├── exceptions.py
│   ├── log.py                      # session_id ContextVar + optional Sentry
│   ├── cli.py                      # Click entry: `agent-yoku …`
│   ├── app.py                      # Streamlit UI (legacy)
│   ├── api/                        # FastAPI surface
│   │   ├── app.py                  #   create_app() + CORS + routers
│   │   ├── deps.py                 #   JWT, current_user, password hashing
│   │   ├── schemas.py              #   request/response Pydantic models
│   │   └── routes/                 #   auth, sessions, chat, stats
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
│   │   └── backfill.py             #   author_email backfill
│   ├── embeddings/embed.py
│   ├── linking/pr_to_jira.py
│   ├── agent/
│   │   ├── agent.py                # deepagent factory
│   │   ├── chat.py                 # CLI ask/final_answer helpers
│   │   └── tools.py                # 14 tools (narrow + generic mongo)
│   └── utils/                      # cross-cutting helpers
│       ├── bson.py · jira_keys.py · http.py
├── web/                            # React + Vite frontend
│   ├── package.json  vite.config.ts  tsconfig.json
│   └── src/
│       ├── main.tsx · App.tsx · styles.css
│       ├── pages/Login.tsx  pages/Chat.tsx
│       └── lib/api.ts              # typed fetch client
├── scripts/
│   ├── dump_schemas.py             # Pydantic → JSON Schema
│   └── agent_smoke.py              # agent regression suite
├── tests/                          # 71 tests: unit + integration
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

Sign in at `http://localhost:5173` with `tenant: asato` + the email/password you just set.

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
  chat <Q…>          One-shot agent query
  api                Launch FastAPI on :8000
  ui                 Launch legacy Streamlit on :8501
  auth               User management
    create-user      …  Create an account in a tenant
    list-users       …  List users in a tenant
```

## Auth + multi-tenancy

One mongo cluster, one db per tenant:
- `tenant_id == "asato"` (default) → the existing `agent_yoku` db (legacy, no rename).
- Any other id → `agent_yoku_<tenant_id>` (auto-created on first signup).

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
8. Add narrow tools in `agent_yoku/agent/tools.py` if useful (agent already has the generic `mongo_query` escape hatch).

`agent-yoku list-connectors` will auto-discover it.

## Architecture decisions

- **Mongo is the source of truth.** No vector DB — embeddings live as a
  `embedding` field on each doc; cosine runs in-memory via numpy.
- **Two-layer agent.** Main orchestrator + `jira_researcher` + `github_researcher`
  sub-agents (isolated context).
- **Narrow tools + generic escape hatch.** 10 narrow tools for common queries;
  `mongo_query` / `mongo_count` / `describe_collection` / `list_collections`
  for analytics the narrow tools don't cover. Tool errors return
  `{"error": "..."}` so the agent can adapt rather than crash.
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
docker build -t agent_yoku .
docker run --rm -p 8000:8000 --env-file .env agent_yoku
# API on http://localhost:8000  /docs for Swagger
```

For batch ingest:
```bash
docker run --rm --env-file .env agent_yoku python -m agent_yoku.cli refresh-all
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
