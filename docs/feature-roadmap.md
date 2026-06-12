# yoku — Feature Roadmap (Deep Dive)

> A deep exploration of 8 candidate features to extend yoku into a more complete
> product. For each feature: **what it is**, **how to build it**, **why it fits**,
> and an **architecture diagram + clear build points**.
>
> **Build order lives in [`build-plan.md`](build-plan.md)**, which merges these
> 8 features with the 6 proactive phases of [`yoku_agent.md`](yoku_agent.md)
> into one milestone sequence. Two designs below have since been unified there:
> Feature 3's prev-state diffing now rides the proactive `events` stream
> (no separate `dc-state-prev`), and Feature 2's action layer is built once,
> shared with proactive Phase 6.

## Context: what yoku is today

A **read-only, reactive Q&A agent** over three sources (JIRA `dc-jira`, GitHub
`dc-github`, Slack `dc-slack`), projected into canonical primitives
(`ds-work-item`, `ds-pull-request`, `ds-conversation`), linked via
`ds-entity-links`, and answered by a `deepagents` planner (`gpt-5.4-mini`) over 8
schema-driven tools (hybrid semantic search + safe mongo pipelines + user
resolution + `who_knows`). Multi-tenant by db-per-tenant, synced on a 60-min
APScheduler poll.

The architecture's superpower — and the lens used to pick these features — is that
it is **schema-driven**: the registries (Pydantic models, `sources.py`,
`relationships.yaml`) are the single source of truth, so a new source or
capability shouldn't touch the tool/prompt layer. The highest-leverage features
either (a) ride the registries for free, or (b) add a *new dimension* (time,
write-back, proactivity) the current design lacks entirely.

### Gaps that motivated the list

- **Reactive only** — you must ask. No proactive digests, alerts, or anomaly detection.
- **Read-only** — can't act (create a ticket, post to Slack, comment on a PR).
- **Point-in-time** — no history/trend analytics; sync overwrites, nothing is time-series.
- **Slack users aren't unified** (`dc-slack-users` never joins `ds-unified-users`),
  so `who_knows`/`resolve_user` silently miss Slack identity.
- **Polling-only sync** (60 min) — no webhooks, so answers can be an hour stale.
- **Conversations are per-message**, never threaded — Slack context is fragmented.

---

# Feature 1 — Proactive intelligence (scheduled digests & alerts)

### 1. What it is (deeply)
Today yoku only answers when asked. This feature inverts that: users save
**"watches"** — natural-language standing questions like *"merged PRs this week
with no linked ticket"*, *"tickets stuck in review > 5 days"*, *"what shipped"* —
and yoku runs them on a schedule, then **pushes** the answer to Slack DM, email,
or an in-app inbox.

Three conceptual pieces:
- **Watch** = a saved query + schedule (cron-ish) + delivery target + a "only
  notify if changed/non-empty" rule.
- **Runner** = a scheduler job that executes each watch through the *existing*
  agent and renders the result.
- **Delivery** = adapters that send the rendered digest somewhere.

Key insight: the `consistency.py` checks (done-without-PR, merged-without-ticket)
already compute real signal but are invisible. Proactive mode is the surface that
makes that signal valuable.

### 2. How to build it (method/strategy)
- New collection `dc-watches` (tenant-scoped, like `connector_configs`):
  `{name, query, schedule, delivery: {type, target}, last_run_at, last_result_hash, enabled, created_by}`.
- New module `yoku/proactive/runner.py`:
  - For each enabled watch in each tenant, set tenant context (reuse
    `tenancy.set_tenant`), call `chat.ask(watch.query)`, extract `final_answer`.
  - Hash the structured result; compare to `last_result_hash` to implement "only
    notify on change" (suppresses noisy repeats).
  - Render via a small `digest.py` (markdown → Slack blocks / HTML email).
- Delivery adapters in `yoku/proactive/delivery/`: `slack.py` (reuse
  `slack/client.py` `chat.postMessage`), `email.py` (SMTP/SES), `inapp.py` (write
  to a `dc-notifications` collection the UI polls).
- Wire into the existing **APScheduler** (`scheduler.py`) as a second job, or give
  watches their own cadence with per-watch `CronTrigger`.
- API: `yoku/routers/watches.py` — CRUD + "run now". UI: a "Watches" tab + an
  in-app notification bell.

**Strategy:** start with **in-app inbox only** (zero external dependency, easy to
test), then add Slack, then email. Reuse the agent rather than building a separate
query engine — keeps it schema-driven, so new connectors are watchable for free.

### 3. Why it fits
- Reuses the two assets yoku already has — the agent (`chat.ask`) and the
  scheduler — so the marginal cost is low.
- Changes yoku from "a tool you remember to open" into "a system that earns daily
  attention." That's the difference between a demo and a product people keep.
- Monetizes the dormant `consistency.py` logic.

### 4. Architecture + build points
```
┌─────────────┐   CRUD    ┌──────────────────┐
│  React UI   │ ────────▶ │ routers/watches  │ ──▶ dc-watches (per tenant)
│ Watches tab │           └──────────────────┘
│  + bell 🔔  │ ◀── poll ── dc-notifications
└─────────────┘
                          ┌────────────────────────────────┐
   APScheduler ──fires──▶ │ proactive/runner.py            │
   (scheduler.py)         │  for tenant in tenants:        │
                          │    for watch in watches:       │
                          │      chat.ask(watch.query) ────┼──▶ existing agent (8 tools)
                          │      hash & diff vs last_run   │
                          │      render digest             │
                          │      deliver ──────────────────┼──▶ Slack / Email / in-app
                          └────────────────────────────────┘
```
- **Touches:** new `yoku/proactive/`, new `dc-watches` + `dc-notifications`
  collections, new router, scheduler addition, new UI tab.
- **Does NOT touch:** tools, prompts, schema registries.
- **Risk:** low. **Effort:** medium. **Gotcha:** dedupe / "notify on change" is
  what separates useful from spammy — design `last_result_hash` early.

---

# Feature 2 — Write-back actions (agent that *does*, not just reads)

### 1. What it is (deeply)
Today every tool is read-only and returns `{"error": ...}` instead of mutating
anything. This feature adds a **small, gated set of side-effecting tools**:
`create_jira_ticket`, `transition_ticket`, `comment_on_pr`, `post_slack_message`,
`link_pr_to_ticket`. The agent **proposes** an action ("Merged PR #173 has no
ticket — file one titled X?"), the user **approves in the UI**, and yoku executes
it through the existing authenticated connector clients.

The architecturally important part is the **human-in-the-loop gate**: side effects
must never fire from the agent loop directly. The agent emits a *proposed action*;
execution happens only after explicit user confirmation.

### 2. How to build it (method/strategy)
- New `yoku/agent/tools/actions/` package — auto-discovered by
  `_registry.discover_tools()`. But these tools **don't execute**; they return a
  structured *proposal* (`{"proposed_action": {type, target, payload, preview}}`).
- The **chat SSE flow** (`routers/chat.py::_stream_agent_turn`) detects a proposal
  and emits a `proposed_action` event instead of finishing. UI renders a
  confirm/edit/cancel card.
- On confirm, UI calls a new `POST /api/actions/execute` endpoint → an
  `ActionExecutor` that maps action type → connector client call (`jira/client.py`
  create issue, `github/client.py` PR comment, `slack/client.py` postMessage).
- After execution, do a **targeted re-sync** of the affected doc (ties into Feature
  5's `sync_one`) so the agent's next answer reflects the change.
- Write an **audit log** (`dc-action-log`) for every proposed/executed/rejected
  action — non-negotiable for a side-effecting system.
- Permissions: gate behind a role (reuse the `is_admin` flag initially; later a
  proper RBAC).

**Strategy:** ship **one** action end-to-end first (`create_jira_ticket` from a
merged-no-ticket PR — pairs perfectly with Feature 1's signal), prove the gate,
then add the rest.

### 3. Why it fits
- The connectors already hold authenticated, retry-wrapped REST clients — the hard
  plumbing exists.
- "Find the gap → fix the gap" in one breath is a 10× value jump over "find the
  gap, now go do it manually."
- Natural completion of the cross-source story: yoku already *knows* a PR has no
  ticket; acting is the obvious next step.

### 4. Architecture + build points
```
User: "file tickets for merged PRs with no ticket"
        │
        ▼
   agent loop ──▶ action tool returns PROPOSAL (no side effect)
        │
   chat SSE emits  event: proposed_action ───────▶ UI confirm card
                                                         │ approve
                                                         ▼
                                          POST /api/actions/execute
                                                         │
                                                ┌────────▼─────────┐
                                                │  ActionExecutor  │
                                                │  + audit log     │──▶ dc-action-log
                                                └────────┬─────────┘
                                                         ▼
                                   jira/github/slack client.py  (real write)
                                                         │
                                                         ▼
                                          sync_one(key)  → re-embed/re-link/index
```
- **Touches:** new action tools, new `/api/actions` router + executor, SSE
  confirmation event, UI confirm card, audit collection, connector clients (add
  write methods).
- **This one changes yoku's fundamental contract** (read-only → side-effecting).
  Write a short design doc for the gate + audit before coding.
- **Risk:** high (irreversible external effects). **Effort:** high.
  **Mitigation:** confirmation gate, audit log, dry-run preview, role restriction.

---

# Feature 3 — Time-travel analytics & trend dashboards

### 1. What it is (deeply)
Sync currently **overwrites** docs — yoku is point-in-time and has no memory of
*change*. `/api/stats/counts` is a single snapshot. This feature captures **deltas
over time** into a time-series collection so you can answer trend questions: PR
cycle time, ticket age distribution, throughput per week, where work stalls,
contributor activity — and render them as charts.

It opens a new persona: **engineering managers/leads**, who care about trends, not
individual items.

### 2. How to build it (method/strategy)
- Add a sync step in `sync_service.run_full_pipeline_for_tenant`: after unify,
  compute per-run metrics (open/closed counts by status, PR merge times, status
  transitions detected by diffing against last snapshot) and append to a Mongo
  **time-series collection** `dc-metrics` (`{ts, metric, dimensions, value}`).
- Detect transitions by comparing current `status` vs the previously stored value
  (store a lightweight `dc-state-prev` keyed by doc key, or diff against the latest
  `dc-metrics` point).
- New endpoints `/api/stats/trends?metric=cycle_time&window=90d` aggregating the
  time-series.
- New React **Dashboard page**: burndown, velocity, stale-work-over-time,
  cycle-time histogram (a charting lib like Recharts).
- Whitelist `dc-metrics` in `ALLOWED_COLLECTIONS` so the *agent* can also answer
  "how has our merge velocity changed?" via the existing `mongo_query` tool —
  analytics for free in chat.

### 3. Why it fits
- Pure additive: a new sync step + new collection + new page. No tool/prompt
  changes for the core.
- Unlocks a second buyer (managers) on top of the same data already being ingested.
- Mongo time-series collections are built for exactly this.

### 4. Architecture + build points
```
sync_service.run_full_pipeline_for_tenant
   ingest → embed → unify → link
                         │
                         ▼
               metrics step:  diff current vs prev state
                         │     → transitions, cycle times, counts
                         ▼
                  dc-metrics (time-series)
                   │                    │
        ┌──────────▼─────────┐   ┌──────▼─────────────┐
        │ /api/stats/trends  │   │ mongo_query tool   │
        └──────────┬─────────┘   │ (whitelisted)      │
                   ▼             └──────┬─────────────┘
        React Dashboard (charts)        ▼
                                "how has velocity changed?" in chat
```
- **Touches:** new sync step, `dc-metrics` collection (+ whitelist),
  `/api/stats/trends`, new React page.
- **Risk:** low. **Effort:** medium. **Gotcha:** transition detection needs
  *previous* state — design the diff source (prev-state collection vs time-series
  tail) up front.

---

# Feature 4 — Unify Slack identities + thread-aware conversations

### 1. What it is (deeply)
Two correctness fixes bundled:
- **(a) Slack identity unification.** `dc-slack-users` is ingested but **never
  joined into `ds-unified-users`** — only JIRA↔GitHub are merged. So `who_knows`
  and `resolve_user` can't tell that a Slack participant is the same person as a
  JIRA assignee. Cross-source people questions are silently *wrong* today.
- **(b) Thread-aware conversations.** Slack is ingested and embedded **per
  message**, so a threaded discussion is fragmented into disconnected vectors.
  Grouping by `thread_ts` into thread-level conversation docs gives the agent whole
  discussions.

Frame this as a **bug fix disguised as a feature** — it makes existing answers
correct.

### 2. How to build it (method/strategy)
- **(a)** Extend `unified_users.main()` (in `storage/unified_users.py`): after the
  JIRA↔GitHub merge, fold in `dc-slack-users` using the same email-exact →
  name-slug fallback already implemented for the other two. Add a
  `slack: {user_id, display_name, email}` block to the unified doc and extend the
  alias-override map. `who_knows`/`resolve_user` then improve with **zero tool
  changes**.
- **(b)** Add a threading pass: in `slack/ingest.py` or the unify mapper, group
  messages sharing a `thread_ts` (already captured) into a parent conversation with
  concatenated body for embedding, while keeping individual messages addressable.
  Re-embed thread-level text.

### 3. Why it fits
- Corrects answers yoku already gives — the highest-trust kind of fix.
- **Pure data layer**: extends `unified_users.py` and the Slack ingest/mapper. No
  tools, no prompts, no UI. `who_knows` and `resolve_user` read from
  `ds-unified-users`, so they get better automatically.
- Lowest risk of all 8 → ideal first thing to ship.

### 4. Architecture + build points
```
BEFORE:
  dc-jira-users ─┐
  dc-github-users┴─▶ unified_users.main() ─▶ ds-unified-users  (Slack MISSING)
  dc-slack-users  ····· (orphaned)

AFTER:
  dc-jira-users ─┐
  dc-github-users┤
  dc-slack-users ┴─▶ unified_users.main() ─▶ ds-unified-users {jira, github, slack}
                       (email → name-slug fallback, same as today)
                                │
                  who_knows / resolve_user read this → now Slack-aware

  Threading:
  dc-slack (per message, thread_ts) ──group by thread_ts──▶ thread-level
       conversation doc → embed whole discussion (ds-conversation)
```
- **Touches:** `storage/unified_users.py`, `slack/ingest.py` (or slack mapper),
  re-embed.
- **Risk:** very low. **Effort:** low-medium. **Do this first.**

---

# Feature 5 — Real-time sync via webhooks

### 1. What it is (deeply)
Sync is a **60-minute APScheduler poll** — answers can be an hour stale. This adds
a webhook endpoint `/api/webhooks/{source}` that accepts JIRA/GitHub/Slack event
callbacks and does a **targeted single-doc upsert → embed → re-link →
index-invalidate**, so a merged PR or new comment shows up in seconds. The poll
stays as the backfill/safety net.

Freshness is a **credibility** issue: people stop trusting a "status" agent that's
stale.

### 2. How to build it (method/strategy)
- New `yoku/routers/webhooks.py` with per-source signature verification (GitHub
  HMAC, Slack signing secret, JIRA shared secret).
- A `sync_one(source, key)` function that reuses the existing connector ingest path
  for a single entity, then runs the same embed/link/unify steps scoped to that one
  doc, then calls `invalidate_index(tenant_id)`.
- Tenant routing: map the incoming workspace/org/project → tenant (lookup in
  `connector_configs`).
- Queue heavy work (re-embed) so the webhook returns 200 fast (a lightweight
  in-process task or a small job table).
- Register webhook URLs during connector setup (or document manual setup in the
  Settings guide).

### 3. Why it fits
- Reuses ingest/embed/link/index functions — `sync_one` is mostly orchestration of
  existing pieces.
- Directly addresses the freshness gap that undermines trust.
- Composes with Feature 2 (after a write-back action, call `sync_one` to reflect it
  immediately).

### 4. Architecture + build points
```
GitHub/JIRA/Slack ──webhook POST──▶ /api/webhooks/{source}
                                          │ verify signature
                                          │ resolve tenant (connector_configs)
                                          ▼
                                    sync_one(source, key)
                                          │ upsert single doc
                                          │ embed (1 doc) · re-link · unify
                                          ▼
                                    invalidate_index(tenant_id)

   APScheduler poll (60 min)  ── remains as backfill / catch-missed-events
```
- **Touches:** new webhooks router, new `sync_one` path, connector-setup URL
  registration.
- **Risk:** medium (signature verification, tenant mapping, replay/dedupe).
  **Effort:** medium. **Gotcha:** make the endpoint return fast; defer embedding.

---

# Feature 6 — New connectors (Linear, Notion/Confluence, Google Calendar)

### 1. What it is (deeply)
Add 1–2 high-value sources via the existing recipe
(`docs/adding-a-connector.md`). Top candidates: **Notion/Confluence** (design docs,
specs, RFCs — the "why" behind tickets), **Linear** (alt issue tracker), **Google
Calendar** ("who's in which meeting / availability / when did this get decided").
Every source added multiplies cross-source value — that's literally yoku's pitch.

### 2. How to build it (method/strategy)
Follow the 9-step recipe exactly:
1. Copy `connectors/jira/` → `connectors/<source>/`, swap `client.py` (auth + REST
   + retry via `@make_retry`).
2. Adapt `ingest.py` to upsert into a new `dc-<source>` collection; extract JIRA
   keys for cross-linking.
3. Declare `META: ConnectorMeta` in `__init__.py` (auto-discovered).
4. Add a CLI subcommand + register collection in `ALLOWED_COLLECTIONS` (raw stays
   internal; canonical `ds-*` is agent-facing).
5. Add a Pydantic model in `models/` (docstring = collection description; fields
   carry `description`/`display`/`filterable`), map in
   `schema_registry.COLLECTION_MODELS`.
6. Add a `SourceSpec` to `sources.py` (key pattern + example) + a mapper to a
   canonical primitive + entries in `relationships.yaml`.
7. Add a connector-config editor in the Settings UI.

After that, `semantic_search` / `list_collections` / `describe_collection` /
`mongo_query` / `who_knows` cover the new source **automatically — no tool or
prompt edits.**

### 3. Why it fits
- This is the architecture's *designed-for* path — it validates the entire
  schema-driven thesis.
- Highest ROI per unit novelty: each connector compounds with all existing ones (a
  Notion RFC links to the JIRA epic links to the PR links to the Slack thread).
- Lower technical risk than the "new dimension" features.

### 4. Architecture + build points
```
new source API ──client.py(@make_retry)──▶ ingest.py ──▶ dc-<source>
                                                              │ extract refs
   models/<source>.py (Pydantic)  ──registered in──▶ schema_registry.COLLECTION_MODELS
   sources.py SourceSpec (key pattern) ──┐
   mappers/<source>.py ───────────────────┤──▶ ds-<primitive>  (canonical)
   relationships.yaml (joins) ────────────┘
                                                              │
   ALL existing tools cover it for free ◀──── semantic_search / mongo_query / who_knows
```
- **Touches:** new connector folder, model, mapper, SourceSpec, relationships, CLI,
  ALLOWED_COLLECTIONS, Settings UI editor.
- **Does NOT touch:** tools, prompts. **Risk:** low-medium (mostly API-auth quirks
  per source). **Effort:** medium per connector.
- **Note:** Calendar is the most novel (time/availability is a different shape than
  issue/PR/message) and may need a small mapper rethink; Notion/Linear slot in
  cleanly.

---

# Feature 7 — End-to-end answer-quality evaluation harness

### 1. What it is (deeply)
`eval/retrieval.py` scores **retrieval** (hit-rate, MRR, recall) — but nothing
scores the **final answer**. `agent_smoke.py` is 5 queries with no scoring. As you
add write-back, more tools, and swap models, you have no regression net. This
feature adds a **golden Q→expected-citations dataset** + an **LLM-judge** for
faithfulness/grounding/completeness + a `make eval` CI gate.

### 2. How to build it (method/strategy)
- Curate a golden set: `{query, expected_citations, must_mention, source}` (start
  ~30 cases across point/broad/structural/analytical/cross-source — mirror the
  prompt's question taxonomy).
- New `yoku/eval/answer_quality.py`: run `chat.ask`, then an **LLM-judge** scores
  faithfulness (every claim cited?), citation recall (did it cite the expected
  keys?), and groundedness (no invented facts). Reuse the existing `UsageCallback`
  to track eval token cost.
- Aggregate into a report (extend the `EvalReport` dataclass pattern already in
  `eval/retrieval.py`).
- `make eval` target + a CI job that fails on regression below a threshold. Run it
  on prompt/tool/model changes.

### 3. Why it fits
- Builds directly on existing scaffolding (`eval/`, `scripts/agent_smoke.py`,
  `UsageCallback`).
- It's the **safety net that makes features 2 and 6 safe to ship** — every new
  tool/source/model can regress answers silently otherwise.
- "You can't improve what you don't measure" — turns prompt/model tuning from vibes
  into data.

### 4. Architecture + build points
```
golden_set.yaml (Q → expected citations, must_mention)
        │
        ▼
eval/answer_quality.py
   for case: chat.ask(case.query) ─▶ agent ─▶ answer + citations
        │
        ├─ retrieval metrics (reuse eval/retrieval.py: MRR, recall)
        └─ LLM-judge: faithfulness · citation recall · groundedness
        │
        ▼
   EvalReport ──▶ make eval ──▶ CI gate (fail < threshold)
```
- **Touches:** new `eval/answer_quality.py`, golden dataset, `make eval`, CI.
- **Risk:** low. **Effort:** medium (dataset curation is the real work).
  **Gotcha:** LLM-judge needs a stable rubric or it's noisy — pin the judge model +
  prompt.

---

# Feature 8 — UX polish: real citations, saved views, shareable answers, token streaming

### 1. What it is (deeply)
A bundle of cheap, high-frequency wins:
- **(a) Fix citation base URL** — `Chat.tsx:118` hardcodes
  one specific Atlassian site for JIRA links, ignoring the tenant's configured
  `base_url`. **This is an outright bug for every other tenant.**
- **(b) Saved/pinned queries** surfaced as one-click chips (beyond the 5 hardcoded
  suggestions).
- **(c) Shareable answers** — permalink to a turn so people can paste it in Slack.
- **(d) Token-level answer streaming** — today only *tool* events stream over SSE;
  the final answer arrives all at once. Stream the answer tokens for a live-typing
  feel.

### 2. How to build it (method/strategy)
- **(a)** Plumb the tenant's connector `base_url` to the frontend (via
  `/api/connectors` or `/me`) and use it in the citation linkifier instead of the
  constant.
- **(b)** `dc-saved-queries` collection + chips in the Chat hero; "save this
  question" affordance.
- **(c)** A `GET /api/sessions/{id}/turns/{turn_id}` permalink + a share button
  (respecting tenant auth).
- **(d)** In `_stream_agent_turn`, switch the answer emission to
  `stream_mode="messages"` (or token callback) and emit incremental `answer_delta`
  SSE events; UI appends.

### 3. Why it fits
- Daily-touch surfaces — they make yoku feel *finished*.
- (a) is a real correctness bug for multi-tenancy (the whole product is
  multi-tenant; a hardcoded URL breaks every other tenant's citations).
- Great "filler" track to run in parallel with the big bets.

### 4. Architecture + build points
```
(a) /api/connectors or /me ──base_url──▶ Chat.tsx linkifier (replace hardcoded const)
(b) dc-saved-queries ──▶ /api/saved-queries ──▶ chips in hero
(c) /api/sessions/{id}/turns/{turn_id} ──▶ share permalink
(d) chat SSE: stream_mode="messages" ──answer_delta events──▶ UI appends tokens
```
- **Touches:** mostly frontend + small API additions; (d) touches
  `routers/chat.py` SSE flow.
- **Risk:** low. **Effort:** low (each item is small). **Do (a) immediately — it's
  a bug.**

---

## How they relate (sequencing)

```
SAFE FIRST (fix wrong answers):     #4 Slack unify   ·   #8a citation URL bug
BIGGEST UNLOCK (reuse agent):       #1 Proactive digests
CREDIBILITY (freshness):            #5 Webhooks  ──┐ composes with
HIGHEST CEILING (new contract):     #2 Write-back ─┘  (re-sync after action)
PARALLEL TRACKS:                    #3 Trends · #6 Connectors · #7 Evals · #8 UX
SAFETY NET enabling #2/#6:          #7 Evals
```

| Order | Feature | Why this slot |
|---|---|---|
| **First** | #4 Slack unify + threading | Fixes *wrong* answers today; pure data layer; low risk |
| **First** | #8a citation URL bug | Outright multi-tenant bug; trivial fix |
| **Then** | #1 Proactive digests/alerts | Biggest product unlock, reuses existing agent + scheduler |
| **Then** | #5 Webhooks | Credibility (freshness); modest effort |
| **Big bet** | #2 Write-back actions | Highest ceiling, but needs careful human-in-the-loop design |
| **Parallel** | #3 Trends · #6 connectors · #7 evals · #8 UX | Independent tracks |

**Two design-doc-before-code items:** #2 (introduces side effects into a read-only
system) and #3's transition-detection (needs a prev-state strategy). Everything
else is build-directly.
