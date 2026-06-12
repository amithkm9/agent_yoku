# Agent Yoku

**One agent that watches your team's work across JIRA, GitHub, and Slack —
answers when asked, and speaks first when something is off.**

---

## The problem

Engineering work lives in layers, and the layers drift apart:

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
- "Who actually knows this system?" takes three Slack messages to answer.
- Status questions ("what shipped last week?", "is anyone blocked on X?")
  mean opening three tools and stitching the answer together by hand.

No single tool sees all four layers, so nobody notices the drift until
standup — or the sprint review. Yoku's job is to see all four layers at once
and keep them lined up.

## What yoku does

**Reactive — ask anything across your sources.** A planning agent answers
questions over the unified picture: point lookups ("tell me about AS-1234"),
cross-source joins ("which PRs implement this ticket?"), people questions
("who knows the auth code?"), and trends ("how has merge velocity changed?")
— every claim cited with its key, one click from the source.

**Proactive — yoku notices gaps before you do.** After every sync, a
background engine:

1. **Detects** cross-source drift (done-without-PR, merged-without-ticket) —
   with maturation windows so transient states never page anyone.
2. **Judges** each gap through cost-tiered filters: human verdicts override
   everything, per-person memory suppresses patterns people have dismissed,
   deterministic baselines suppress "that's just how they work" (a PM whose
   tickets never have PRs isn't slipping), and only the ambiguous remainder
   reaches a two-dimension LLM judgment that must *agree* the gap is real.
3. **Speaks** — drafts one short, specific Slack DM to the one person who can
   fix it ("AS-4396 is marked Done, but I can't find a PR linked to it — did
   the code ship somewhere I'm not seeing?"). Ships in **shadow mode** by
   default: drafts land in a review Inbox, and nothing is sent until a
   per-tenant flag is flipped.
4. **Acts** — on approval, yoku closes the loop: transition the ticket, link
   the PR, comment, create a ticket. Every action is **proposal-gated**
   (the agent can only propose; a human approves) and fully audit-logged.

**Trends** — weekly engineering metrics (merge velocity, PR cycle time
median + p90, ticket throughput, open-gap count) computed from the same data,
rendered as a dashboard and queryable in chat.

The principles behind the proactive side: *judgment before noise* (every
stage is a chance to stay quiet), *a teammate, not a dashboard* (one clear
ask, citing the key — never a report), and *when in doubt, skip*.

## How it works

```
 ┌──────────────┐    JWT      ┌─────────────────────┐   invoke   ┌──────────────────┐
 │  React UI    │ ──────────▶ │  FastAPI            │ ─────────▶ │ deepagent        │
 │  chat·inbox  │             │  (uvicorn :8000)    │            │ + 11 tools       │
 │  trends      │             │  + tenant ContextVar │            │ + registries     │
 └──────────────┘             └──────┬──────────────┘            └────────┬─────────┘
                                     │ db per tenant                      │
                                     ▼                                    ▼
 ┌──────────────────────────────────────────────────────────────────────────────────┐
 │ MongoDB — yoku_<tenant>                                                          │
 │  dc-*  raw connector docs (jira, github, slack, users)                           │
 │  ds-*  canonical layer (work-item, pull-request, conversation, entity-links,     │
 │        unified-users, metrics)                                                   │
 │  engine: events · signals · baselines · person_memory · conversations ·          │
 │          action_log · slack_inbound                                              │
 └──────────────────────────────────────────────────────────────────────────────────┘
        ▲                                                   │
        │ ingest → embed → unify → link → detect →          ▼
        │ judge → speak → metrics      (hourly APScheduler + on-demand + sync_one)
 ┌──────┴───────┐
 │ JIRA · GitHub │  ◀── write-back: transition / comment / create / link
 │ · Slack       │  ◀── bot voice: DMs out, replies + @mentions in (signed events)
 └───────────────┘
```

**Schema-driven by design.** Three registries are the single source of truth —
the agent tools read everything from them, nothing about a collection is
hardcoded in the tool layer:

- **Pydantic models** (`yoku/schemas/`) — each collection's description and
  fields (with `display`/`filterable` metadata).
- **Source registry** (`yoku/agent/sources.py`) — key shapes and routing.
- **Relationship registry** (`yoku/schemas/relationships.yaml`) — cross-source joins.

Connectors, agent tools, and gap detectors are all **auto-discovered** —
onboarding a new source or gap type is a new file plus registry entries,
never an edit to tools or prompts. Recipe: `docs/adding-a-connector.md`.

## Project layout

```
yoku/
├── config.py · constants.py · auth.py · main.py · cli.py
├── connectors/            # one folder per source (auto-discovered)
│   ├── jira/  github/  slack/        # client + ingest (+ users, write methods)
│   └── base.py · _runtime.py         # contract, per-tenant config binding
├── schemas/                # Pydantic models = collection contracts (+ relationships.yaml)
├── mappers/                # dc-* → canonical ds-* projection
├── pipeline/               # embed · unify · entity_links · pr_to_jira · metrics
│   ├── slack_threads.py    #   thread rollup (whole discussions, one embedding)
│   ├── sync_service.py     #   full per-tenant refresh orchestration
│   ├── sync_one.py         #   targeted single-doc refresh (post-action)
│   └── scheduler.py        #   hourly auto-sync loop
├── proactive/              # the gap engine
│   ├── events.py           #   diff-on-upsert change stream
│   ├── detectors/          #   auto-discovered gap detectors (one file each)
│   ├── signals.py          #   signal lifecycle: maturation, dismissal, healing
│   ├── baselines.py        #   per-person "normal workflow" stats
│   ├── memory.py           #   dismissals + learned patterns per person
│   ├── judge.py            #   tiered judgment (human > memory > baseline > LLM)
│   ├── routing.py          #   gap → the one right person (never guess)
│   ├── orchestrator.py     #   speak-first loop, shadow mode, reply threading
│   └── messenger.py        #   Slack DM primitive (dry-run default)
├── actions/                # write-back: propose → approve → execute → audit
├── agent/                  # deepagent + auto-discovered tools + registries
├── routers/                # FastAPI: auth · chat · sessions · inbox · actions ·
│                           #   stats/trends · connectors · slack events
├── eval/                   # retrieval + answer-quality harnesses
├── db/                     # tenant-aware mongo accessors · tenancy · unified users
└── utils/                  # retry, dates, keys, jira-key extraction
web/                        # React (Vite + TS): chat · proactive inbox · trends · settings
docs/                       # vision, proactive design, build plan, connector recipe
tests/                      # 480+ unit + integration tests
```

## Quickstart

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

Sign in with the exact tenant name you created. Connect JIRA / GitHub / Slack
per tenant in **Settings**; enable the Slack bot voice (DMs + replies) with
`docs/slack-app-setup.md`.

### Auto-sync and the proactive loop

A background scheduler re-runs the full pipeline for every configured tenant:
ingest → thread rollup → embed → unify users → link → backfill → canonical
projection → entity links → **baselines → detectors → judge → speak →
metrics**. Controlled by `AUTO_SYNC_ENABLED` (default true) and
`SYNC_INTERVAL_MINUTES` (default 60); always off under `ENV=test|ci`.

Safety rails for the speaking/acting side (all default-safe):

| Control | Default | Meaning |
|---|---|---|
| shadow mode | on | drafts go to the Inbox, not Slack |
| `proactive_send_enabled` (per tenant) | off | the go-live flag, in Slack settings |
| `PROACTIVE_SPEAK_ENABLED` | on | global kill switch for real DMs |
| `PROACTIVE_DAILY_DM_CAP` | 1 | max yoku DMs per person per day |
| `PROACTIVE_QUIET_UTC` | unset | e.g. `20-06`: no DMs in the window |
| `JUDGE_BUDGET_PER_RUN` | 20 | LLM-judged signals per sync (cost cap) |
| `DM_APPROVAL_ENABLED` | off | affirmative DM replies may approve that gap's action |
| write-back | proposal-gated | agent proposes; an admin approves; everything audit-logged |

## CLI

```
yoku --tenant <id> <command>

  ingest jira|jira-users|github|github-users|slack-export …
  refresh-all          full pipeline (ingest → … → judge → speak → metrics)
  embed · link · unify-users · unify · backfill-pr-emails
  chat "<question>"    one-shot agent query
  slack-test-dm <person> [--send]   verify the bot voice (dry-run default)
  status · consistency · list-connectors
  auth create-user|list-users
  api                  launch FastAPI on :8000
```

## Auth + multi-tenancy

One mongo cluster, one database per tenant (`yoku_<tenant>`), auto-created on
first signup. The JWT carries the tenant id; every authenticated request binds
a tenant `ContextVar` that all storage accessors read — cross-tenant access is
impossible by construction. Connector tokens and the Slack signing secret are
encrypted at rest. Passwords are bcrypt-hashed.

## Quality gates

```bash
make fmt            # autoflake + ruff --fix + black
make lint           # ruff + black --check
make test           # pytest (480+ tests; integration needs local mongo)
make web-lint       # TypeScript checks
make web-build      # production build
make schemas        # regenerate JSON Schema docs
make agent-smoke    # 5-query agent regression suite   (hits OpenAI)
make eval           # answer-quality eval on the golden set (hits OpenAI)
make retrieval-eval # retrieval hit-rate / MRR on live data
```

CI runs on every push and PR. Run `make agent-smoke` after touching agent
behavior; `make eval` after prompt/tool/model changes.

## Docker

```bash
docker build -t yoku .
docker run --rm -p 8000:8000 --env-file .env yoku
# batch ingest:
docker run --rm --env-file .env yoku python -m yoku.cli refresh-all
```

## Docs

| Doc | What it is |
|---|---|
| `vision.md` | why yoku exists — the north star |
| `docs/yoku_agent.md` | the proactive engine's design contract (all 6 phases shipped) |
| `docs/build-plan.md` | the milestone sequence the build followed |
| `docs/adding-a-connector.md` | onboard a new source end to end |
| `docs/slack-app-setup.md` | enable the Slack bot voice for a tenant |
| `docs/feature-roadmap.md` | the original deep-dive on candidate features |
