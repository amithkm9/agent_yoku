---
name: yoku-agent-proactive-design
description: Detailed design for yoku's proactive agent — the background loop that detects cross-source gaps, judges them, and opens a conversation with the right person. The build contract behind vision.md.
whenToUse: Read before building any part of the proactive system (event detection, detectors, memory, sub-agents, Slack bot, write-back). This is the agreed plan; vision.md is the north star, this is the how.
---

# yoku — Proactive Agent (Detailed Design)

> The detailed design `vision.md` points to. `vision.md` is the *why*; this is
> the *how* and the *in-what-order*. Read `vision.md` first.

---

## 0. What we are building (one sentence)

> **A background loop that runs after each data sync, finds gaps across
> JIRA / GitHub / Slack, decides whether a gap is real, and tells the one right
> person — eventually in Slack, eventually offering to fix it.**

That is the proactive component. Everything else is a layer we add onto that one
loop.

**What it is NOT:**

- ❌ Not the chat Q&A. That is the *reactive* side and already works.
- ❌ Not a dashboard or weekly digest. Synthesis/digests are secondary, opt-in,
  and come later — the identity is *teammate, not dashboard*.
- ❌ Not ten separate features. It is **one engine** we make smarter in steps.

The same agent, tools, and memory answer questions (reactive) and start
conversations (proactive). Proactivity is a **new entry point** into the existing
agent loop, not a parallel system.

---

## 1. The problem it solves

Work lives in four layers that drift apart:

```
INTENT        what was planned       (tickets, specs, Slack decisions)
EXECUTION     what is being built    (PRs, commits, branches)
COMPLETION    what is done           (merged, closed, deployed)
CONVERSATION  what was said          (Slack threads, PR comments)
```

A ticket closes with no PR. A PR merges but its ticket stays open. A decision
lands in Slack and never becomes a ticket. **No single tool sees all four
layers**, so nobody notices until standup. yoku already ingests all four — the
proactive job is to keep them lined up.

`yoku/pipeline/consistency.py` already computes two of these drifts
(Done-no-PR, merged-no-ticket) but the signal is invisible and nobody runs it.
A large part of "going proactive" is making that dormant signal valuable.

### Running example (used throughout)

> **AS-4396** — "Add rate-limiting to the chat API," assigned to **Priya**.
> - PR `AsatoCorp/agent-svc#212` merges (branch `priya/AS-4396-rate-limit`).
>   The ticket stays **In Progress**.
> - In `#eng`, Priya wrote *"shipped the rate limiter 🎉 will close the ticket
>   tomorrow."* She never did.
> - **AS-4410** (assigned to **Marco**) is blocked on AS-4396 being done.
> - Two weeks later AS-4396 is still open; Marco is quietly stuck.

No single tool sees the ticket + PR + Slack promise + downstream dependency.
yoku does. That is the moat.

---

## 2. The engine — one pipeline

Every proactive capability is the same machine with different detectors and
policies. Build it left to right.

```
  sync ends
     │
     ▼
[1] event delta        diff prior vs new state            → events
     │                 (foundation: sync currently overwrites,
     │                  so prior state must be captured)
     ▼
[2] detectors          cheap rules over ds-* + entity-links + events
     │  (auto-discovered)  → candidate Signal{kind, item, person, evidence}
     ▼                      surfaced in an in-app Inbox  (first visible win)
[3] judge + memory     person_agent ∧ item_agent must AGREE it is real;
     │                 memory consulted first → suppress known/dismissed
     ▼
[4] route + voice      Slack identity (unify slack users) + bot can post
     │                 ownership: assignee → PR author → who_knows (confident or skip)
     ▼
[5] converse           start_conversation: one DM, one ask, read the reply
     │                 (ships in SHADOW MODE first — see §5)
     ▼
[6] act + learn        propose/do the fix (close ticket, link PR) → re-sync →
                       update memory → audit log
```

Discipline: this routes through `tenancy.set_tenant` and the **same** agent /
tools as reactive chat. Detectors are auto-discovered exactly like
`yoku/agent/tools/_registry.py` discovers tools and `connectors.base` discovers
connectors — adding a new gap type is a new detector file, not a new subsystem.

---

## 3. Data model (new collections)

Operational collections use **plain snake_case** — matching the existing
operational collections (`chat_sessions`, `chat_messages`, `auth_users`,
`connector_configs`). They are *not* `dc-*`/`ds-*`; that prefix split is reserved
for the data pipeline (raw connector vs canonical). All are per-tenant, created
on first write like every other collection in `yoku/db/mongo.py`.

| Collection      | Phase | Holds                                                                 |
|-----------------|-------|-----------------------------------------------------------------------|
| `events`        | 1     | One row per change since last sync (`created`/`updated`/`linked`).    |
| `state_prev`    | 1     | Lightweight prior snapshot per doc key, to diff against on next sync. |
| `signals`       | 2     | Candidate gaps emitted by detectors, with evidence + status.         |
| `person_memory` | 3     | Per-person dismissals, learned patterns, last interaction.           |
| `baselines`     | 3     | Per-person rolling activity stats (p10/p50/p90) for anomaly judging.  |
| `conversations` | 5     | Open/closed Slack threads yoku started, deduped per gap.             |
| `action_log`    | 6     | Audit of every proposed / executed / rejected write-back.            |
| `commitments`   | 7+    | Promises extracted from Slack/PR comments, tracked for follow-up.     |

Indicative shapes (refined per phase):

```jsonc
// events
{ "kind": "updated", "doc_key": "github/AsatoCorp/agent-svc#212",
  "collection": "ds-pull-request", "field": "status",
  "old": "open", "new": "merged", "ts": "...", "processed": false }

// signals
{ "detector": "done_no_pr", "kind": "drift", "item_key": "jira/AS-4396",
  "person_user_id": "u_priya", "evidence": { "merged_pr": "...#212",
  "days_open_after_merge": 14 }, "confidence": 0.9,
  "status": "open",            // open | judged | dismissed | shadow | sent | resolved
  "event_ids": ["..."], "created_at": "..." }

// person_memory
{ "user_id": "u_priya",
  "dismissals": [{ "pattern": "done_no_pr", "scope": "qa_pending",
                   "note": "leaves tickets open until QA signs off",
                   "learned_at": "..." }],
  "last_interaction_at": "...", "patterns": [...] }

// conversations
{ "signal_id": "...", "person_user_id": "u_priya", "item_key": "jira/AS-4396",
  "slack_channel": "D0...", "slack_ts": "1700...",
  "state": "awaiting_reply",   // shadow | open | awaiting_reply | resolved | closed
  "opened_at": "...", "last_message_at": "..." }

// action_log
{ "action_type": "transition_ticket", "target_key": "jira/AS-4396",
  "payload": { "to": "Done" }, "status": "executed",   // proposed | executed | rejected | failed
  "proposed_by": "agent", "approved_by": "u_priya", "result": "...", "ts": "..." }
```

The agent reaches `person_memory` through a `get_memory` / `update_memory` tool,
not raw `mongo_query`. `events` / `signals` / `state_prev` stay off the agent's
`ALLOWED_COLLECTIONS` read whitelist — they are engine internals.

---

## 4. The 6-phase build plan

Each phase is shippable and testable on its own and de-risks the next. This is
a clean dependency DAG; build in order.

### Phase 1 — Event detection *(foundation; invisible)*

- **Goal:** after each sync, know exactly *what changed*.
- **Why first:** sync currently overwrites docs (idempotent upsert) and remembers
  nothing. No "what changed" → nothing to be proactive about. Everything depends
  on this.
- **Build:**
  - Capture prior state before upsert into `state_prev` (or diff against the
    canonical doc pre-overwrite).
  - `run_event_delta(tenant_id)` writes `events`, hooked into
    `yoku/pipeline/sync_service._run_post_ingest_pipeline` — the same seam where
    `unify` and `entity_links` already plug in as best-effort steps.
- **New:** `yoku/proactive/events.py`; collections `events`, `state_prev`.
- **Done when:** a sync produces correct event rows for created / updated /
  linked changes, verifiable in a test. No user-facing output yet.
- **Depends on:** nothing.

### Phase 2 — Detectors → Signals → Inbox *(first visible win)*

- **Goal:** turn events into candidate gap **signals** and show them in-app,
  unprompted. Lowest-risk proof of the whole idea — no Slack, no write-back.
- **Build:**
  - `yoku/proactive/detectors/` package, **auto-discovered** like the tools
    registry. First detectors generalize `consistency.py`: `done_no_pr`,
    `merged_no_ticket`. Each is a pure function over `events` + canonical `ds-*`
    + `ds-entity-links`, emitting `Signal{kind, item, person, evidence,
    confidence}`.
  - `signals` collection + `GET /api/inbox` + a "Proactive" tab in the React UI
    listing gaps, each citing its key.
- **New:** `yoku/proactive/detectors/*`, `yoku/proactive/signals.py`,
  `yoku/routers/inbox.py`, web Inbox tab; collection `signals`.
- **Done when:** AS-4396 (Done, no linked PR) appears in the Inbox after a sync,
  citing the key.
- **Depends on:** Phase 1.
- **Note:** detectors are a **cheap pre-filter** that finds *candidates* — they
  do **not** decide to message anyone. That is Phase 3's job. This keeps us
  compatible with "judgment before noise."

### Phase 3 — Judgment + Memory *(the noise killer)*

- **Goal:** keep only gaps genuinely worth raising; stop nagging.
- **Why here:** before yoku ever messages a human it must suppress false
  positives, or it loses trust on day one.
- **Build:**
  - Two `deepagents` sub-agents — `person_agent(person, window)` and
    `item_agent(item_key)` — that each return a **judgment**, and must **agree**
    a gap is real (AND-gate for precision). `item_agent` reads the item lifecycle
    from `ds-entity-links`; `person_agent` reads activity vs `baselines`.
  - `baselines` computed as a sync step (per-person rolling p10/p50/p90).
  - `person_memory` + `get_memory` / `update_memory` tools, consulted **before**
    every signal — dismissed or "normal for this person" patterns are skipped.
- **New:** `yoku/proactive/judge.py`, sub-agent definitions, `yoku/proactive/
  baselines.py`, `yoku/agent/tools/memory.py`; collections `person_memory`,
  `baselines`.
- **Done when:** a dismissed gap stays dismissed next sync; a "normal for this
  person" gap (e.g. Priya's QA-pending tickets) is auto-suppressed while the same
  pattern still fires for others.
- **Depends on:** Phase 2.
- **Note:** introduces the sub-agent pattern, a deliberate departure from the
  current single-agent design (`deepagents` supports it).

### Phase 4 — Slack identity + bot voice *(prerequisite for speaking)*

- **Goal:** know each person's Slack handle, and let yoku post.
- **Build:**
  - Fold `dc-slack-users` into `ds-unified-users` (extend
    `yoku/db/unified_users.py` with the same email→name-slug join already used for
    JIRA↔GitHub). This also fixes `who_knows` / `resolve_user` for free.
  - Slack app scopes (`chat:write`, `im:write`), event subscriptions for DMs and
    @mentions, `POST /api/slack/events` to route replies back into the agent.
- **New:** `unified_users.py` extension, `yoku/routers/slack_events.py`, Slack
  app config; Slack identity block on unified users.
- **Done when:** yoku posts a test DM to the correct person and their reply lands
  back in the system.
- **Depends on:** the Slack-identity-unification sub-part is **independent** of
  Phases 1–3 and may be pulled forward / done in parallel; the bot-app + events
  router gate Phase 5.

### Phase 5 — Start the conversation *(the "speaks first")*

- **Goal:** close the loop — one DM, one ask, read the reply, update memory.
- **Build:**
  - **Ownership routing:** assignee → PR author → last conversational owner →
    `who_knows` expert, with a confidence floor; below it → **skip** (never guess
    a person).
  - `start_conversation` tool + `conversations` collection, deduped so it never
    opens two threads on the same gap.
  - `run_proactive_agent(tenant_id)` orchestrator after each sync, running the
    escalating filters: active window → memory → both agents agree → one
    person / one ask / none already open → send, else **skip**.
  - **Shadow mode (default on first):** the orchestrator produces the message it
    *would* send and writes it to the Phase-2 Inbox (`signal.status = "shadow"`)
    instead of Slack. Flip a per-tenant flag to go live only once shadow output
    looks right.
- **New:** `yoku/proactive/orchestrator.py`, `yoku/proactive/routing.py`,
  `yoku/agent/tools/start_conversation.py`; collection `conversations`;
  scheduler wiring (mirrors auto-sync; off under `ENV=test`/`ci`).
- **Done when:** AS-4396 produces **exactly one** intended message to Priya
  (shadow first, then a real DM), and her reply is captured.
- **Depends on:** Phases 3 and 4.

### Phase 6 — Closed-loop write-back *(yoku acts)*

- **Goal:** on a "yes," yoku fixes it — transition the ticket, link the PR, post
  the summary.
- **Build:**
  - `yoku/agent/tools/actions/` (auto-discovered). **Proposal-gated first:** the
    tool returns `{proposed_action}`; execution only after explicit approval (in
    the DM reply or Inbox). Later, a small allowlist of low-risk *reversible*
    actions (linking, labeling) may go autonomous.
  - Connector write methods (`jira/client.py` transition, `github` comment,
    `slack` postMessage). Every action → `action_log` audit. After execution →
    `sync_one(key)` so the next answer reflects the change.
- **New:** `yoku/agent/tools/actions/*`, `yoku/routers/actions.py`, connector
  write methods, `sync_one`; collection `action_log`.
- **Done when:** Priya replies "close it" → AS-4396 transitions to Done + links
  #212, logged in `action_log`, reflected after a targeted re-sync.
- **Depends on:** Phase 5.
- **Note:** this changes yoku's contract from read-only to side-effecting — the
  riskiest phase, deliberately last.

### After Phase 6 — aggressive breadth *(pure additions)*

Once the engine exists, advanced capabilities are **just new detectors** on the
same loop — no new plumbing:

- Behavioral baselines / "went quiet" anomalies (extends Phase 3 baselines).
- Commitment tracking — Slack/PR promises → follow-up (`commitments`).
- Decision capture — Slack decisions implying untracked work → propose a ticket.
- Predictive risk — deadline/dependency slippage via the `ds-entity-links` graph.
- Bus-factor / knowledge-silo alerts via ownership concentration over time.

Breadth, not architecture. That is the payoff of building the spine right.

---

## 5. Cross-cutting concerns

- **Shadow mode** — Phase 5 ships not-sending first; intended messages land in the
  Inbox for human review. The single most important de-risk for the one
  irreversible action (DMing humans).
- **Guardrails** (over Phases 5–6) — per-tenant kill switch, quiet hours,
  max messages per person per day, and a full `action_log`. Tunable per tenant.
- **Tenancy** — every stage runs under `tenancy.set_tenant`; collections are
  per-tenant by construction. No cross-tenant reasoning, ever.
- **Scheduling** — `run_proactive_agent` fires after each sync via the existing
  APScheduler (`yoku/pipeline/scheduler.py`), non-overlapping, off under
  `ENV=test`/`ci` — the same pattern as auto-sync.
- **Cost** — cheap deterministic detectors (Phase 2) gate the expensive LLM
  sub-agents (Phase 3), so we don't run judgment on every event.

---

## 6. Principles (from `vision.md`)

- **One agent, both directions.** No separate monitoring system; the same
  reasoning loop answers questions and starts conversations.
- **Judgment before noise.** Two independent dimensions must agree, memory is
  checked first, every message is one clear ask to one person.
- **A teammate, not a dashboard.** Direct, specific, short, cite the key.
  *"AS-4396 is Done but has no linked PR — did it merge somewhere I'm not
  seeing?"* — never a report.
- **Schema-driven.** Detectors and sources are registry entries, not code paths.
- **When in doubt, skip.** Noise destroys trust. Every stage is a chance to stop.

---

## 7. Open decisions (resolve as we build)

1. **Autonomous send vs. approval gate.** `vision.md` sends without a human
   confirming, trusting extreme precision. Recommendation: ship **proposal-gated**
   (Phases 5–6), earn trust, then graduate only the safest reversible actions to
   autonomous.
2. **Prior-state capture mechanism (Phase 1).** Snapshot collection
   (`state_prev`) vs. diff-on-upsert vs. Mongo change streams. Pick before
   building Phase 1 — it shapes everything above it.
3. **Precision vs. recall.** The two-agent AND-gate maximizes precision but will
   silently swallow real gaps. How do we measure what it missed? (An eval harness
   over a labeled gap set, mirroring `eval/retrieval.py`.)

---

## 8. We are here

```
[Phase 1] ← START   [2] Inbox   [3] Judge+Memory   [4] Slack   [5] Speak   [6] Act
   ▲ build this first (events stream); nothing works without it
```

Start with Phase 1 — small, self-contained, and the foundation everything else
consumes.
