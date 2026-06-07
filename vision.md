# yoku — Vision

One agent that watches the team's work across JIRA, GitHub, and Slack — answers
when asked, and speaks first when something is off.

---

## The Problem

Work lives in layers, and the layers drift apart:

```
INTENT        what was planned       (tickets, specs, Slack decisions)
EXECUTION     what is being built    (PRs, commits, branches)
COMPLETION    what is done           (merged, closed, deployed)
CONVERSATION  what was said          (Slack threads, PR comments)
```

A ticket closes with no PR behind it. A PR merges but its ticket stays open.
A decision lands in Slack and never becomes a ticket. No single tool sees all
four layers, so nobody notices until standup — or until the sprint review.

yoku's job is to see all four layers at once and keep them lined up.

---

## What yoku Is

A multi-tenant deepagent over a unified picture of the team's work:

- **Connectors** ingest JIRA tickets, GitHub PRs, and Slack messages into
  MongoDB (`dc-*` raw collections), on a schedule and on demand.
- **A canonical layer** (`ds-*`) projects every source doc onto one of a few
  typed primitives — `work_item`, `pull_request`, `conversation` — plus
  derived `entity-links` edges and `unified-users` identities. Cross-source
  reasoning happens here, not against source quirks.
- **A schema-driven agent** (LangChain `deepagents`) with thin, composable
  tools: hybrid semantic search, schema-aware mongo queries, user resolution,
  topic expertise. Tools read everything from registries — adding a source
  never means editing a tool or a prompt.
- **Surfaces**: React chat UI, FastAPI REST/SSE API, Click CLI.

---

## Reactive and Proactive — Same Agent, Both Ways

The sync pipeline is yoku's eyes and ears. Today it only feeds answers to
questions users ask. The vision is one loop that runs in both directions:

```
REACTIVE    user asks → agent searches, reasons, answers        (works today)
PROACTIVE   sync detects a gap → agent verifies, judges,
            opens a Slack conversation with the one person
            who can fix it                                      (the plan)
```

Same agent, same tools, same memory. A conversation yoku starts and a
conversation a user starts converge on the same runtime.

---

## Where We Are (Reactive)

- ✓ JIRA / GitHub / Slack connectors, auto-discovered, per-tenant credentials
- ✓ Canonical layer: mappers → `ds-work-item`, `ds-pull-request`,
  `ds-conversation`, `ds-entity-links`, `ds-unified-users`
- ✓ Hybrid retrieval: vector + BM25 + RRF, optional reranker
- ✓ Scheduled sync (APScheduler) + on-demand sync per connector
- ✓ Chat over SSE with session history, JWT auth, db-per-tenant isolation

What it cannot do yet:

```
× speak first
× reason over what changed since the last sync
× learn from conversation outcomes
× route a finding to the right person
× write back to JIRA, GitHub, or Slack
```

---

## The Plan (Proactive)

Detailed design: `docs/yoku_agent.md`. The build order:

### 1. Event detection
After every sync, diff old vs new state and write an `events` collection
(`created | updated | linked`, doc key, field change, processed flag).
Hook: `run_event_delta(tenant_id)` at the end of the sync pipeline.
Events are the raw material — nothing proactive happens without them.

### 2. Memory
A `memory` collection per person: dismissals, learned patterns, last
interaction, team baselines. Read via a `get_memory` tool — always consulted
before acting. If a pattern was dismissed before or is normal for this
person, skip. Memory is what keeps yoku from becoming a nag.

### 3. Sub-agents for the two dimensions
Every event has a person and an item. Two sub-agents reason over them:

- `person_agent(person, window)` — week-by-week activity across all sources;
  surfaces weeks where the layers broke or the person went quiet.
- `item_agent(item_key)` — full lifecycle of one work item; surfaces stages
  claimed without evidence (Done with no PR, PR with no ticket).

Both return judgments, not data. Both must agree there is a gap.

### 4. Slack identity
yoku joins the workspace as a bot: `chat:write`, `im:write`, event
subscriptions for DMs and @mentions, an App Home tab of open items.
A `start_conversation` tool opens a thread and indexes it in a
`conversations` collection — never duplicating an open one.
`/api/slack/events` routes replies back into the same agent.

### 5. The proactive loop
`run_proactive_agent(tenant_id)` after each sync, for each unprocessed event:

```
active window? → memory check → person + item judgments agree?
→ worth a conversation? (one person, one clear ask, none already open)
→ one Slack message → read reply → update memory → close or continue
```

When in doubt — skip. Noise destroys trust.

---

## Principles

- **One agent, both directions.** No separate "monitoring system" — the same
  reasoning loop answers questions and starts conversations.
- **Schema-driven.** Sources are registry entries, not code paths. The
  canonical layer is the contract; raw collections are an implementation detail.
- **Judgment before noise.** Two independent dimensions must agree, memory is
  checked first, and every message is one clear ask to one person.
- **A teammate, not a dashboard.** Direct, specific, short. Cite the key.
  "AS-4396 is Done but has no linked PR — did it merge somewhere I'm not
  seeing?" — never a report.
