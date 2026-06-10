---
name: build-plan
description: The single ordered build plan for yoku — merges the 8 candidate features (feature-roadmap.md) and the 6 proactive phases (yoku_agent.md) into one milestone sequence with acceptance criteria.
whenToUse: Read when deciding what to build next. feature-roadmap.md explores the candidates in depth; yoku_agent.md is the proactive design contract; this doc is the agreed ORDER. Build top to bottom; parallel tracks are marked.
---

# yoku — Unified Build Plan

> `feature-roadmap.md` explores **eight candidate features**. `yoku_agent.md`
> designs **six proactive phases**. They overlap (write-back appears in both;
> Slack unification appears in both; trends and proactive events share a
> prev-state mechanism). This doc merges them into **one sequence** — each
> milestone is shippable, testable, and de-risks the next.

## How the two docs map onto one spine

```
feature-roadmap.md                    yoku_agent.md            this plan
─────────────────────                 ─────────────            ─────────
#4  Slack unify + threads      ═══    Phase 4a (pulled fwd) →  M0
#8a citation URL bug                                        →  M0
#7  answer-quality evals                                    →  M1  (parallel)
                                      Phase 1 events        →  M2
#3  trends (prev-state part)   ═══    Phase 1 events        →  M2 + M4
                                      Phase 2 inbox         →  M3
                                      Phase 3 judge+memory  →  M5
                                      Phase 4b Slack bot    →  M6
                                      Phase 5 converse      →  M7
#2  write-back actions         ═══    Phase 6 act           →  M8
#5  webhooks                                                →  accelerant track
#6  new connectors                                          →  parallel track
#8b–d UX polish                                             →  parallel track
```

Two former duplications are now **single builds**:

- **One delta mechanism.** Trend analytics (#3) and proactive events (Phase 1)
  both need "what changed since last sync." Build the `events` stream once
  (M2); trends (M4) aggregate it. No `dc-state-prev`.
- **One action layer.** Chat write-back (#2) and proactive write-back (Phase 6)
  share `ActionExecutor` + `action_log` + the proposal gate (M8). Two entry
  surfaces, one engine.

---

## M0 — Correctness first *(small; do immediately)*

Fixes answers that are **wrong today**. Pure data/frontend layer, near-zero risk.

- Fold `dc-slack-users` into `ds-unified-users` (extend `yoku/db/unified_users.py`
  with the existing email→name-slug join). `who_knows` / `resolve_user` improve
  with zero tool changes. *(Also a hard prerequisite for M6/M7 — yoku can't DM a
  person it can't identify.)*
- Thread-aware Slack conversations: group by `thread_ts` into thread-level
  `ds-conversation` docs; embed whole discussions.
- Fix the hardcoded JIRA base URL in `web/src/pages/Chat.tsx` citations — plumb
  the tenant's connector `base_url` through the API.

**Done when:** `resolve_user("priya")` returns her Slack identity; a threaded
discussion comes back as one conversation; a non-Asato tenant's citations link
to *their* JIRA.

## M1 — Answer-quality eval harness *(parallel track; before behavior changes)*

Feature #7. ~30-case golden set (query → expected citations / must-mention),
LLM-judge for faithfulness + citation recall, `make eval`, CI threshold gate.

**Why this early:** M5's judge sub-agents, M8's action tools, and any
model/prompt change all need a regression net. Dataset curation is the real
work — start it while M2/M3 are being built.

**Done when:** `make eval` produces a scored report and CI fails on regression.

## M2 — Event delta *(proactive Phase 1; invisible foundation)*

Diff-on-upsert at the existing ingest `find_one` seam → `events` collection
(see yoku_agent.md §4 Phase 1 for the mechanism decision). Hooked into
`_run_post_ingest_pipeline`.

**Done when:** a sync produces correct `created`/`updated`/`linked` event rows,
verified in tests. No UI yet.

## M3 — Detectors → Signals → Inbox *(proactive Phase 2; first visible win)*

Auto-discovered detectors (`done_no_pr`, `merged_no_ticket` — generalizing
`pipeline/consistency.py`) → `signals` with **maturation windows** and
**auto-resolution** (yoku_agent.md §4 Phase 2) → `GET /api/inbox` → React
"Proactive" tab with confirm/dismiss (every click is an eval label).

**Done when:** a Done-no-PR ticket appears in the Inbox after its maturation
window, citing the key; dismissing it sticks; a self-healing gap records
`resolution: "self_healed"`.

## M4 — Trends & dashboards *(parallel after M2)*

Feature #3, now riding the events stream: aggregate `events` into `dc-metrics`
(Mongo time-series) — cycle times, transitions, throughput. `/api/stats/trends`
+ React dashboard page + whitelist `dc-metrics` so chat can answer
"how has velocity changed?".

**Done when:** the dashboard renders real trends and the agent answers a trend
question from `dc-metrics`.

## M5 — Judgment + memory *(proactive Phase 3; the noise killer)*

`person_agent` ∧ `item_agent` AND-gate, `baselines` sync step, `person_memory`
+ `get_memory`/`update_memory` tools, tiered cost gates (detector → maturation
→ memory → LLM). Judge verdicts logged even when rejected (recall measurement,
yoku_agent.md §7.3).

**Done when:** a dismissed gap stays dismissed next sync; a "normal for this
person" pattern is suppressed for them while still firing for others.

## M6 — Slack bot voice *(proactive Phase 4b)*

Slack app (`chat:write`, `im:write`), event subscriptions, `POST
/api/slack/events` routing replies into the agent. (Identity half already done
in M0.)

**Done when:** yoku DMs a test message to the right person and their reply
lands back in the system.

## M7 — Speak first *(proactive Phase 5; shadow → live)*

Ownership routing with confidence floor, `start_conversation` tool,
`conversations` dedupe, `run_proactive_agent` orchestrator after each sync.
**Shadow mode default**: intended DMs land in the Inbox; flip per-tenant flag
only when the per-detector confirm rate clears its threshold (yoku_agent.md
§7.3). Guardrails: kill switch, quiet hours, per-person daily cap.

**Done when:** the canonical AS-4396 case produces exactly one intended message
to the right person — shadow first, then live — and the reply is captured.

## M8 — Write-back actions *(feature #2 + proactive Phase 6; one engine)*

Proposal-gated action tools (`create_jira_ticket`, `transition_ticket`,
`link_pr_to_ticket`, `comment_on_pr`, `post_slack_message`), `ActionExecutor`,
`action_log` audit, connector write methods, `sync_one(key)` targeted re-sync
after execution. Both surfaces: chat SSE `proposed_action` confirm card **and**
DM-reply approval. Ship **one** action end-to-end first (`create_jira_ticket`
from merged-no-ticket).

**Done when:** "close it" in a DM (or a chat confirm) transitions the ticket,
links the PR, writes the audit row, and the next answer reflects it.

---

## Ongoing parallel tracks (slot in anywhere)

- **Webhooks (#5)** — accelerant, best after M3 so fresh events feed visible
  signals. `sync_one` lands in M8 regardless; webhooks reuse it.
- **New connectors (#6)** — Notion/Confluence first (the "why" layer). Pure
  registry work via `docs/adding-a-connector.md`; each new source automatically
  becomes detector-visible once M2/M3 exist.
- **UX polish (#8b–d)** — saved queries, shareable answers, token streaming.
  Filler work between milestones.
- **Breadth detectors (post-M8)** — commitments, decision capture, went-quiet
  baselines, predictive risk (yoku_agent.md §4 "after Phase 6"). New detector
  files, no new plumbing.

## Dependency graph

```
M0 ──────────────┬──────────────────────────► M6 ─► M7 ─► M8
                 │                            ▲     ▲
M1 (parallel) ───┼─── safety net for M5/M8    │     │
                 │                            │     │
M2 ─► M3 ────────┴─► M5 ─────────────────────-┘─────┘
  └─► M4 (parallel)
        webhooks / connectors / UX = anytime
```
