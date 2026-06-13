<div align="center">

# ✳︎ Agent Yoku

### One AI teammate that watches your team's work across JIRA, GitHub & Slack — answers when asked, speaks up when something's off, and remembers what people tell it.

<br/>

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React_18-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-47A248?logo=mongodb&logoColor=white)
![Tests](https://img.shields.io/badge/tests-525%20passing-2ea44f)

[What it is](#-what-it-is) · [The four layers](#the-four-layers-that-drift-apart) · [Features](#-features) · [Memory engine](#-the-memory-engine--a-teammate-that-remembers) · [How it works](#-how-it-works) · [Quickstart](#-quickstart)

</div>

---

## ✶ What it is

Engineering work is scattered across tools that don't talk to each other. The **plan** lives in JIRA, the **code** lives in GitHub, the **conversation** lives in Slack — and no single tool sees all three. So work quietly drifts out of sync until standup, or the sprint review, or an incident.

**yoku is the agent that sees all of it at once.** It ingests your team's tickets, pull requests, and Slack messages into one unified picture, then does two things:

- **Reactive** — answer any question across your sources. *"Which PRs implement AS‑1234?"* · *"Who actually knows the auth code?"* · *"What shipped last week?"* — every claim cited, one click from the source.
- **Proactive** — notice gaps before you do. A ticket marked **Done with no PR**. A PR **merged with no ticket**. yoku detects the drift, decides if it's worth raising, and drafts one short, specific nudge to the *one* person who can fix it.

What makes it different from a dashboard or a chatbot: **yoku has a memory.** When someone replies *"that's a spike, we don't open PRs for those,"* yoku understands it, stops nagging, and remembers it — so it judges the *next* gap smarter. It's less a tool, more a teammate.

> **Built to be safe by default.** yoku ships in *shadow mode*: it drafts messages into a review Inbox and sends nothing until you flip a per‑team switch. Every write‑back (closing a ticket, linking a PR) is proposal‑gated — the agent proposes, a human approves, everything is audit‑logged.

---

## The four layers that drift apart

```
INTENT        what was planned       (tickets, specs, Slack decisions)
EXECUTION     what is being built    (PRs, commits, branches)
COMPLETION    what is done           (merged, closed, deployed)
CONVERSATION  what was said          (Slack threads, PR comments)
```

Every team knows the symptoms:

- A ticket is marked **Done with no PR behind it** — claimed finished, no code.
- A **PR merges but nothing tracks it** — code shipped, invisible to planning.
- A decision lands in Slack and **never becomes a ticket**.
- *"Who actually knows this system?"* takes three Slack messages to answer.

No single tool sees all four layers, so nobody notices the drift until it's expensive. **yoku's whole job is to see all four at once and keep them lined up.**

---

## ✨ Features

| | Feature | What it does |
|---|---|---|
| 💬 | **Cross‑source chat** | A `deepagents` planning agent answers point lookups, cross‑source joins, people questions, and trends — every claim cited with its key. |
| 🛰️ | **Proactive gap engine** | Detects cross‑source drift after every sync, with maturation windows so transient states never page anyone. |
| 🧠 | **Tiered judgment** | Human verdicts → per‑person memory → deterministic baselines → a two‑dimension LLM check that must *agree* a gap is real. *Judgment before noise.* |
| 🤝 | **Memory engine** | Learns from Slack replies, forms durable beliefs, tracks commitments, and follows up on promises. ([deep dive](docs/memory-engine.md)) |
| 👥 | **People view** | "What yoku knows" — beliefs, commitments, and the interaction timeline for every person. |
| 📊 | **Trends dashboard** | Weekly engineering metrics, recomputed each sync and queryable in chat. |
| 💬 | **Slack bot voice** | DMs out, replies & @mentions in (signed events). Shadow‑mode by default. |
| ✅ | **Proposal‑gated write‑back** | Transition a ticket, link a PR, comment, create a ticket — agent proposes, human approves, fully audit‑logged. |
| 🌗 | **Polished UI** | Streaming answers, dark mode, full keyboard a11y, responsive — React + Vite + TS. |

---

## 🧠 The memory engine — a teammate that remembers

Most ops agents are stateless: every run, they re‑reason from scratch and re‑nag about the same things. yoku closes the loop in four phases — it *learns from what people tell it*:

```
yoku nudges Priya about a Done ticket with no PR
   └─ Priya replies on Slack: "that's a spike, we don't open PRs for those"
        └─ yoku UNDERSTANDS the reply  → settles this gap, records the lesson
             └─ CONSOLIDATES it into a belief (confidence 0.7, decays over time)
                  └─ next time a spike of Priya's looks like a gap → yoku stays quiet
   └─ or Priya replies "good catch, I'll link it Friday"
        └─ yoku REMEMBERS the promise → chases it once if Friday passes
```

| Phase | What it adds |
|---|---|
| **A — Episodic spine** | Every interaction (nudge, reply, confirm, dismiss, follow‑up) becomes a timestamped episode. |
| **B — Reply understanding** | An LLM reads each Slack reply → suppresses the gap, captures a commitment, or learns a workflow rule. |
| **C — Belief consolidation** | Episodes distil into durable per‑person beliefs with recency‑weighted confidence, fed into the judge. |
| **D — Follow‑ups + recall** | Lapsed promises get one gentle chase; the chat agent can recall a person's full history. |

A natural‑language explanation in Slack now measurably shapes how yoku judges the *next* gap — and it works from a single conversation. → **[Read the full design: `docs/memory-engine.md`](docs/memory-engine.md)**

---

## 🏗️ How it works

```
 ┌──────────────┐    JWT      ┌─────────────────────┐   invoke   ┌──────────────────┐
 │  React UI    │ ──────────▶ │  FastAPI            │ ─────────▶ │ deepagent        │
 │  chat·inbox  │             │  (uvicorn :8000)    │            │ + 12 tools       │
 │  people·trends│            │  + tenant ContextVar │            │ + registries     │
 └──────────────┘             └──────┬──────────────┘            └────────┬─────────┘
                                     │ db per tenant                      │
                                     ▼                                    ▼
 ┌──────────────────────────────────────────────────────────────────────────────────┐
 │ MongoDB — yoku_<tenant>                                                          │
 │  dc-*  raw connector docs (jira, github, slack, users)                           │
 │  ds-*  canonical layer (work-item, pull-request, conversation, entity-links,     │
 │        unified-users, metrics)                                                   │
 │  engine: events · signals · baselines · person_memory · beliefs · episodes ·     │
 │          conversations · action_log · slack_inbound                              │
 └──────────────────────────────────────────────────────────────────────────────────┘
        ▲                                                   │
        │ ingest → embed → unify → link → detect → judge →  ▼
        │ speak → understand → consolidate → metrics  (hourly + on-demand)
 ┌──────┴───────┐
 │ JIRA · GitHub │  ◀── write-back: transition / comment / create / link
 │ · Slack       │  ◀── bot voice: DMs out, replies + @mentions in (signed events)
 └───────────────┘
```

**Schema‑driven by design.** Three registries are the single source of truth — the agent's tools read everything from them, nothing about a collection is hardcoded:

- **Pydantic models** (`yoku/schemas/`) — each collection's description + fields (with `display`/`filterable` metadata).
- **Source registry** (`yoku/agent/sources.py`) — key shapes and routing per source.
- **Relationship registry** (`yoku/schemas/relationships.yaml`) — cross‑source joins.

Connectors, agent tools, and gap detectors are all **auto‑discovered** — onboarding a new source or gap type is a new file plus registry entries, never an edit to tools or prompts. Recipe: [`docs/adding-a-connector.md`](docs/adding-a-connector.md).

### Project layout

```
yoku/
├── connectors/   # one folder per source (auto-discovered): jira · github · slack
├── schemas/      # Pydantic models = collection contracts (+ relationships.yaml)
├── mappers/      # raw dc-* → canonical ds-* projection
├── pipeline/     # embed · unify · entity_links · metrics · sync orchestration · scheduler
├── proactive/    # the engine: events · detectors · signals · baselines · judge · routing
│   ├── episodes.py · reply_understanding.py · beliefs.py   # the memory engine (A–D)
│   └── orchestrator.py                                     # speak-first loop + follow-ups
├── actions/      # write-back: propose → approve → execute → audit
├── agent/        # deepagent + auto-discovered tools + registries
├── routers/      # FastAPI: auth · chat · sessions · inbox · people · actions · stats · …
├── eval/  db/  utils/  middleware/
web/              # React (Vite + TS): chat · proactive inbox · people · trends · settings
docs/             # vision, memory engine, connector recipe
tests/            # 525 unit + integration tests
```

---

## 🚀 Quickstart

```bash
# 1. Mongo running locally
brew services start mongodb-community

# 2. Install
poetry install
poetry run pre-commit install        # one-time

# 3. Configure
cp .env.example .env                 # JIRA_TOKEN, GITHUB_TOKEN, OPENAI_API_KEY,
                                     # JWT_SECRET (rotate in prod!)

# 4. Pull data + index + link (idempotent)
make refresh-all

# 5. Bootstrap an admin user
poetry run yoku auth create-user --email you@example.com --tenant acme --admin

# 6. Run
make api          # http://localhost:8000   (FastAPI, Swagger at /docs)
make web-install  # one-time npm install
make web          # http://localhost:5173   (React UI)
```

Sign in with the workspace name you created. Connect JIRA / GitHub / Slack per workspace in **Settings**; enable the Slack bot voice with [`docs/slack-app-setup.md`](docs/slack-app-setup.md).

### Auto‑sync & safety rails

A background scheduler re‑runs the full pipeline for every configured tenant: ingest → embed → unify → link → **detect → judge → speak → understand → consolidate → metrics**. Everything that can talk to a human is default‑safe:

| Control | Default | Meaning |
|---|---|---|
| shadow mode | **on** | drafts go to the Inbox, not Slack |
| `proactive_send_enabled` (per tenant) | off | the go‑live flag, in Slack settings |
| `PROACTIVE_DAILY_DM_CAP` | 1 | max yoku DMs per person per day |
| `JUDGE_BUDGET_PER_RUN` | 20 | LLM‑judged signals per sync (cost cap) |
| write‑back | proposal‑gated | agent proposes; an admin approves; everything audit‑logged |

---

## 🖥️ CLI

```
yoku --tenant <id> <command>

  ingest jira|github|slack-export …     refresh-all
  embed · link · unify · backfill        chat "<question>"   (one-shot agent query)
  slack-test-dm <person> [--send]        status · consistency · doctor
  auth create-user|list-users            api    (launch FastAPI on :8000)
```

## 🔐 Auth & multi‑tenancy

One MongoDB cluster, **one database per tenant** (`yoku_<tenant>`), auto‑created on first signup. The JWT carries the tenant id; every request binds a tenant `ContextVar` that all storage reads — **cross‑tenant access is impossible by construction**. Connector tokens and the Slack signing secret are encrypted at rest; passwords are bcrypt‑hashed.

## ✅ Quality gates

```bash
make fmt            # autoflake + ruff --fix + black
make lint           # ruff + black --check
make test           # pytest (525 tests; integration needs local mongo)
make web-lint       # TypeScript checks      make web-build   # production build
make agent-smoke    # 5-query agent regression suite   (hits OpenAI)
make eval           # answer-quality eval on the golden set
```

CI runs on every push and PR.

## 🐳 Docker

```bash
docker build -t yoku .
docker run --rm -p 8000:8000 --env-file .env yoku
docker run --rm --env-file .env yoku python -m yoku.cli refresh-all   # batch ingest
```

## 📚 Docs

| Doc | What it is |
|---|---|
| [`vision.md`](vision.md) | why yoku exists — the north star |
| [`docs/memory-engine.md`](docs/memory-engine.md) | how yoku learns from replies and follows through |
| [`docs/yoku_agent.md`](docs/yoku_agent.md) | the proactive engine's design contract |
| [`docs/adding-a-connector.md`](docs/adding-a-connector.md) | onboard a new source end to end |
| [`docs/slack-app-setup.md`](docs/slack-app-setup.md) | enable the Slack bot voice |
| [`docs/build-plan.md`](docs/build-plan.md) · [`docs/feature-roadmap.md`](docs/feature-roadmap.md) | the milestone sequence & candidate features |

<div align="center">
<br/>

**yoku** — *judgment before noise · a teammate, not a dashboard · when in doubt, skip.*

</div>
