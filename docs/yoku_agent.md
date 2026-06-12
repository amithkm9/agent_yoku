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

// signals — unique upsert key: (detector, item_key). Re-detection on a later
// sync refreshes evidence on the SAME row; it never duplicates a signal.
{ "detector": "done_no_pr", "kind": "drift", "item_key": "jira/AS-4396",
  "person_user_id": "u_priya", "evidence": { "merged_pr": "...#212",
  "days_open_after_merge": 14 }, "confidence": 0.9,
  "status": "open",            // open | judged | dismissed | shadow | sent | resolved
  "first_seen_at": "...",      // when the detector first fired
  "matured_at": null,          // set once the gap has persisted past the detector's
                               // maturation window — only matured signals are judged
  "resolution": null,          // self_healed | acted | dismissed | expired
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
  - **Recommended mechanism: diff-on-upsert, not a snapshot collection.** Every
    ingest already does a `find_one` on the existing doc before upserting (e.g.
    `connectors/jira/ingest.py` fetches `{text, embedding}` to decide whether to
    clear the embedding). Widen that projection to the tracked fields (`status`,
    `assignee`, `linked_prs`, …) and emit an event row right there when a value
    changes — the prior state is already in hand, for free. `state_prev` then
    shrinks to a thin fallback for docs created before this shipped (first sync
    after deploy emits no spurious `updated` events). Mongo change streams are
    rejected: they require a replica set, which local dev doesn't guarantee.
  - `run_event_delta(tenant_id)` finalizes/flushes `events`, hooked into
    `yoku/pipeline/sync_service._run_post_ingest_pipeline` — the same seam where
    `unify` and `entity_links` already plug in as best-effort steps.
  - **Events are dual-use.** The same stream is the prev-state source for
    trend analytics (feature-roadmap.md #3): status-transition metrics, cycle
    times, and throughput are aggregations over `events`. Do **not** build that
    feature's separate `dc-state-prev` — one delta mechanism serves both.
- **New:** `yoku/proactive/events.py`; collections `events`, `state_prev`
  (thin bootstrap fallback only).
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
  - **Signal maturation (hysteresis).** Each detector declares a maturation
    window (e.g. `done_no_pr: 3 days`). A signal is upserted the moment the
    detector fires but is only *judged/surfaced* once the gap has persisted past
    its window across syncs. This kills the worst false-positive class — flagging
    a transient state (PR merged ten minutes ago; the person simply hasn't closed
    the ticket *yet*) — with zero LLM cost.
  - **Signal lifecycle + auto-resolution.** Signals upsert on
    `(detector, item_key)`. When a later sync shows the underlying gap closed
    *without* yoku having said anything, mark `resolution: "self_healed"`; after
    a conversation, `"acted"`; on user dismissal, `"dismissed"`. These outcomes
    are the ground truth that Phase 3's memory learns from and §7.3's
    precision/recall eval measures against — capture them from day one.
  - `signals` collection + `GET /api/inbox` (with confirm/dismiss actions) + a
    "Proactive" tab in the React UI listing matured gaps, each citing its key.
    Every confirm/dismiss is a **label** — the Inbox doubles as the labeling
    tool for the eval set (§7.3).
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
- **Cost** — strictly tiered, cheapest gate first:
  `detector (pure function) → maturation window (clock) → memory lookup (one
  mongo read) → LLM judges (two sub-agents)`. Judgment runs only on matured,
  not-previously-dismissed signals — typically a handful per sync, not one per
  event. Batch the `person_agent` call when several signals share a person
  (one activity read serves all of them).
- **Latency** — the loop's reaction time is bounded by the 60-min sync poll.
  Acceptable for v1 (these gaps live for days, not minutes). When it matters,
  webhooks (feature-roadmap.md #5) slot in *above* Phase 1: a webhook-triggered
  `sync_one(key)` runs the same diff-on-upsert path and emits the same events —
  an accelerant, not a second pipeline.
- **Digest fallback (post-Phase 6, opt-in)** — signals that mature but stay
  below the conversation bar don't have to die silently: an opt-in per-person
  weekly digest DM can batch them. Strictly secondary — the identity stays
  *teammate, not dashboard* — but it keeps borderline signal from being pure
  waste.

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

## 7. Open decisions — resolved recommendations

1. **Autonomous send vs. approval gate → proposal-gated first.** `vision.md`
   sends without a human confirming, trusting extreme precision. Ship
   **proposal-gated** (Phases 5–6), earn trust, then graduate only the safest
   reversible actions (linking, labeling) to autonomous. The action layer is
   built **once** and shared by both surfaces: the same
   `ActionExecutor` + `action_log` serve a proposal confirmed in a chat turn
   (feature-roadmap.md #2) and one confirmed in a proactive DM (Phase 6).
2. **Prior-state capture (Phase 1) → diff-on-upsert.** The ingest upsert
   already reads the existing doc before writing; widen the projection and emit
   the event at that seam (see Phase 1 build notes). `state_prev` survives only
   as a bootstrap fallback; change streams are out (replica-set requirement).
3. **Precision vs. recall → shadow mode *is* the eval.** The two-agent AND-gate
   maximizes precision but will silently swallow real gaps. Measure both sides
   with data the loop already produces:
   - **Precision:** every shadow/Inbox item carries confirm/dismiss buttons;
     the confirm rate per detector is the precision metric. Go live on a
     detector only above a threshold (e.g. >70% confirmed over 2 weeks).
   - **Recall:** log judge-rejected signals too (`status: "judged"`,
     verdict attached). Periodically sample them into the Inbox as
     "low-confidence" items; confirmed ones are AND-gate misses. Together with
     `resolution: "self_healed"` rates, this yields a labeled gap set over time
     — the harness then mirrors `eval/retrieval.py` over it, with no separate
     labeling project.

---

## 8. We are here

```
[Phase 1 ✓]   [2 Inbox ✓]   [3 Judge+Memory ✓]   [4a+4b Slack ✓]   [5 Speak ✓]   [6 Act ✓]
        THE ENGINE IS COMPLETE — what remains is breadth (§4 "after Phase 6") and go-live.
```

Phase 1 shipped (build-plan M2): diff-on-upsert events in all four ingest
paths + `linked` events from `pr_to_jira`, into a per-tenant `events`
collection (`yoku/proactive/events.py`). Phase 2 shipped (build-plan M3):
auto-discovered detectors (`yoku/proactive/detectors/`) with recency windows
+ maturation, the `signals` lifecycle (sticky dismissal, self-heal
auto-resolution, confirm/dismiss labels) in `yoku/proactive/signals.py`,
`GET /api/inbox`, and the React Proactive tab. Phase 3 shipped (build-plan
M5): tiered judgment in `yoku/proactive/judge.py` — human-confirmed labels
override everything, then memory (`yoku/proactive/memory.py`, fed by Inbox
dismissals), then deterministic per-person baselines
(`yoku/proactive/baselines.py`), then the budgeted LLM item∧person AND-gate;
rejected verdicts are retained (status `judged`) as the §7.3 recall stream.
One deliberate deviation: the two judgment dimensions are single-shot
structured LLM calls over pre-gathered evidence, not tool-using sub-agents —
same epistemics, ~10× cheaper; full sub-agents arrive with Phase 5
conversations. `get_memory` / `update_memory` are live agent tools. Slack
identity (Phase 4a) shipped as build-plan M0a. Phase 4b shipped (build-plan
M6): outbound voice (`yoku/proactive/messenger.py` — unified-identity
resolution, bot refusal, dry-run default; `yoku slack-test-dm`) and inbound
ears (`yoku/routers/slack_events.py` — per-tenant signature verification,
team_id→tenant routing, event dedupe, bot-echo filtering, into
`slack_inbound`). Per-tenant app setup: `docs/slack-app-setup.md`; the live
DM round-trip awaits a workspace app install. Phase 5 shipped (build-plan
M7): `yoku/proactive/orchestrator.py` + `routing.py` — judged-real signals
walk dedupe → never-guess routing → quiet hours / per-person daily cap →
deterministic one-ask message templates → SHADOW by default (drafts on the
signal + `conversations`, reviewable in the Inbox). Real sends need three
keys: global `proactive_speak_enabled` ∧ tenant `proactive_send_enabled`
(Slack settings checkbox, default off) ∧ a stored bot token. Replies from
`slack_inbound` thread onto awaiting conversations. `who_knows` ownership
fallback stays unimplemented by design (below the confidence floor → skip).
Phase 6 shipped (build-plan M8): the two-phase action layer
(`yoku/actions/executor.py` — propose → approve/reject → execute under
tenant credentials → `action_log` audit → `sync_one` targeted refresh),
connector write methods (JIRA transition/comment/create, GitHub PR comment),
the `propose_action` agent tool (proposes ONLY — execution requires an
admin in the Proactive tab or `POST /api/actions/{id}/approve`), and
flag-gated DM approval (`dm_approval_enabled`, default off: an exact
affirmative reply from the person yoku asked approves that gap's single
pending action). Action types: transition_ticket, comment_on_jira,
create_jira_ticket, link_pr_to_ticket, comment_on_pr.

Go-live checklist: install the Slack app (docs/slack-app-setup.md), let
shadow mode run for a week, then flip the tenant's "Proactive DMs" toggle.
Everything beyond is breadth — new detectors on the same loop (§4
"after Phase 6").
