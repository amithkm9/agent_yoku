# yoku Roadmap

## Vision

yoku is the **execution-integrity agent for software delivery** — a multi-tenant deep agent
that connects the tools a piece of work flows through (plan → code → ship → run → talk → document),
understands how those tools relate, and proactively catches every place the chain breaks — then
acts on it inside the tool where the work already lives.

The most valuable insights don't live inside a single tool. They live in the **edges between
tools** — the ticket marked Done with no merged PR, the PR merged but never deployed, the deploy
that caused an incident with no tracking ticket, the decision made in Slack that never made it into
the ticket. yoku sees across the delivery chain at once and closes the loop.

This is deliberately **not** a horizontal "search everything" product (that's Glean's lane, and
breadth + per-user permissions across 35+ apps is a multi-year platform we won't win). Our edge is
**depth + action in one lane**: understand how plan, code, and ship actually connect, and guarantee
they stay consistent.

```
            THE SOFTWARE DELIVERY CHAIN
   PLAN  →  CODE  →  SHIP   →  RUN     →  TALK   →  DOCUMENT
  (Jira)  (GitHub) (CI/CD)  (alerts)   (Slack)  (Confluence)
     │        │       │         │          │          │
     └────────┴───────┴────┬────┴──────────┴──────────┘
                           ▼
                ┌─────────────────────┐
                │  yoku brain   │
                │  embed + index      │
                │  cross-source links │
                │  unified identity   │
                └──────────┬──────────┘
                ┌──────────┴──────────┐
                ▼                     ▼
         REACTIVE                PROACTIVE
    answer any question      detect broken links in the
    across the chain         chain, propose + execute the
                             fix in the source tool
```

The product rule: **every connector must add a new link in the chain, not just more documents to
search.** Depth across the chain beats breadth across the company.

---

## Current State (v1)

- Three sources: JIRA + GitHub + Slack
- Source-specific collections: `jira_tickets`, `github_prs`
- Source-agnostic agent tools driven by a source registry: `filter(source, …)`, `get`, `linked`, `semantic_search`
- Linking: regex for `AS-\d+` JIRA keys in PR text only
- Identity: JIRA ↔ GitHub join on email (`unified_users`)
- Search: hybrid vector + BM25 + RRF + ZeroEntropy reranker
- Consistency checks exist (`analysis/consistency.py`): Done-without-PR, merged-without-ticket
- Multi-tenant: per-tenant MongoDB database, JWT auth, React UI
- Connectors are **read-only** (ingest only); agent mode is **reactive** only
- Scheduler (`scheduler.py`) runs background sync per tenant

---

## Phase 1 — Connect the Delivery Chain

**Goal:** the agent reasons about chain *stages*, not specific source names, and can ingest every
stage of delivery. Tenants enable the connectors they use.

Connectors are prioritized by what *link in the chain* they unlock — not by popularity.

### Tier 0 — already connected
JIRA (plan) · GitHub (code) · Slack (talk).

### Tier 1 — complete the core loop (build next)
These finish the "did it actually ship, and did it work" story — the heart of the product.

| Stage         | Sources                          | Unlocks |
|---------------|----------------------------------|---------|
| CI/CD + Deploy| GitHub Actions, **Argo CD**      | what *truly shipped* (not just merged): "Done + merged but never deployed" |
| Incidents     | PagerDuty, Opsgenie              | shipping → consequences: "this deploy caused this incident" |
| Errors        | Sentry                           | runtime failures tied to a deploy/PR: "this release spiked errors" |
| Docs / Specs  | Confluence, Notion               | the *intent* behind a ticket: "code shipped but doesn't match the spec / decision undocumented" |

Tier 0 + Tier 1 lets the agent reason across the **entire delivery chain** — the defensible core.

### Tier 2 — cover the substitutes (market reach)
Same insights, different tool. Required so a non-Jira/GitHub shop isn't a dealbreaker.

| Stage    | Substitutes                       |
|----------|-----------------------------------|
| code     | GitLab, Bitbucket                 |
| plan     | Linear, Azure DevOps Boards       |
| talk     | Microsoft Teams                   |

### Tier 3 — extend to business impact (later)
Connect engineering work to outcomes, to sell upward.

| Stage         | Sources                  | Unlocks |
|---------------|--------------------------|---------|
| Observability | Datadog, Grafana         | runtime health beyond errors |
| CRM / Support | Salesforce, Zendesk      | "this work traces to this customer ask / revenue" |

### Two non-negotiables under every connector

1. **Write, not just read.** A read-only connector is half a connector here — it can *detect* but
   not *act*. The moat is closing the loop in-tool (comment back, update, notify). Prioritize
   connectors where we can also write. Bonus: writing back *inside* the source means the source's
   own permissions apply automatically — no ACL replication needed for in-place actions.
2. **Identity + linkage keys.** Every connector emits the keys needed to resolve "same person" and
   "same work item" across systems. Without this layer, more connectors = more disconnected piles.

### Three ways data gets in

**Built-in connectors** (ship with yoku — the tiers above).

**Connector SDK** (custom/internal sources):

```python
class MyConnector:
    META = ConnectorMeta(name="argocd", domain=Domain.DEPLOY)

    def ingest(self, config: dict) -> Iterator[UnifiedDoc]:
        for record in fetch_from_argocd(config):
            yield UnifiedDoc(
                key=f"argocd/{record['app']}/{record['revision']}",
                domain="deploy",
                title=record["app"],
                body=record["summary"],
                author=record["triggered_by"],
                updated=record["finished_at"],
            )

    def write(self, action: Action) -> WriteResult:   # required for the push model
        ...
```

Install the package → connector appears in tenant settings automatically.

**REST ingest API** (existing pipelines push directly):

```
POST /api/v1/ingest
{ "key": "deploy/...", "domain": "deploy", "title": "...", "body": "...",
  "author": "alice@company.ai", "updated": "2024-03-15T10:00:00Z", "meta": {} }
```

### Canonical datasets (the primitives)

The chain ingests many *sources*, but the agent reasons over a small set of **canonical datasets**
(primitives). Because this is an LLM/semantic product — it reasons over text + relationships, not
tabular joins — a small uniform vocabulary beats many precise schemas. Everything else is a variant,
not a new dataset:

- **New vendor of an existing kind → a mapper.** GitLab → `pull_request`, Teams → `conversation`.
- **A variant of a kind → a `type` field.** email / meeting transcript / chat are all
  `conversation` (`channel_type`); ticket / incident / epic are all `work_item` (`type`).
- **A genuinely new kind → a new primitive.** Only a new *domain* earns one.

| Primitive      | Absorbs (via `type`)                          | Fed by |
|----------------|-----------------------------------------------|--------|
| `person`       | —                                             | every source's users → unified |
| `conversation` | chat, **email**, **meeting transcript**, comments | Slack, Teams, Outlook, Zoom |
| `work_item`    | ticket, issue, task, incident, epic           | Jira, Linear, PagerDuty, Planner |
| `pull_request` | *(kept separate — see decision)*              | GitHub, GitLab, Bitbucket |
| `document`     | page, wiki, spec, file, README                | Confluence, Notion, SharePoint, repos |
| `event` *(with ops)* | deploy, build, commit, alert            | Argo CD, GitHub Actions, Sentry |

`conversation` deliberately absorbs **email and meeting transcripts** — both are "people exchanging
text over time," distinguished by `channel_type`, not separate datasets. Same logic folds `incident`
into `work_item` (tracked work with a `severity`).

**Decision — `pull_request` stays its own primitive (not folded into `document`/`artifact`).**
A PR is a *process* artifact — lifecycle (open → merged), branch, CI state, review threads, and a
typed link to work items — not inert content. Code is central to this product, so keeping PRs
first-class makes "what shipped / what's unmerged / unreviewed" first-class queries. This is the
deliberate **engineering-intelligence** stance; a horizontal "any-org knowledge" product would
instead fold PR into a generic `artifact`. The door stays open: because every primitive shares one
common core (`key, title, body, author, updated, url, refs, embedding`), PR can be demoted to an
`artifact` subtype later — a cheap, reversible change.

The agent filters along three axes: **primitive** (`conversation`), **type** (`meeting`), and
**provider** (`teams`). `semantic_search` is unchanged by this — it indexes the text blob uniformly
and never cares which primitive a row is; primitives just relabel the filter axis from vendor →
concept.

The `domain` field in the unified model below carries the primitive. The granular labels used
elsewhere in this doc map onto them: `messages → conversation`, `issues`/`incidents → work_item`,
`code → pull_request`, `docs → document`, `deploy`/`errors → event`.

### Unified document model

Every source produces the same shape — the agent never needs to know which source is underneath:

```
documents {
  key         : "{source_type}/{native_key}"
  source_type : "jira" | "github" | "argocd" | "sentry" | "pagerduty" | ...
  domain      : "issues" | "code" | "deploy" | "incidents" | "errors" | "messages" | "docs"
  title       : string
  body        : string           # embed-ready full text
  author      : string           # canonical email
  url         : string
  updated     : ISO timestamp
  status      : string           # domain-normalized
  meta        : {}               # source-specific fields verbatim
  entity_refs : []               # cross-source links (populated by pipeline)
  embedding   : []
}
```

### Domain-level agent tools

Generic tools — the agent routes by what it needs, not where data lives:

```python
search(query, domains=["all"], k=50)
filter_issues(status, assignee, label, since_days, limit)
filter_code(repo, author, state, since_days, limit)
filter_deploys(app, env, status, since_days, limit)
filter_incidents(service, severity, since_days, limit)
filter_errors(project, release, since_days, limit)
filter_messages(channel, author, since_days, limit)
filter_docs(space, author, since_days, limit)
get(key)        # any document, auto-routed by source_type
linked(key)     # cross-source hops along the chain
who_owns(target)
```

---

## Phase 2 — The Chain Graph: Understanding How Work Connects

**Goal:** yoku understands how documents across stages relate. The graph is what turns
indexed data into a brain — and it's the asset Glean's per-document search doesn't build.

### entity_links collection

Every relationship between any two documents, regardless of source:

```
entity_links {
  from_key   : "github/asato-svc/pull/88"
  to_key     : "jira/AS-1234"
  link_type  : "implements" | "deploys" | "caused" | "tracks" | "discusses" | "documents" | ...
  context    : "AS-1234 in branch name"
  confidence : 1.0
  extractor  : "regex" | "metadata" | "llm"
}
```

**Extraction by stage:**

| Source             | Method                       | What gets linked |
|--------------------|------------------------------|------------------|
| JIRA, Linear       | API metadata                 | Linked issues, PRs, epics |
| GitHub, GitLab     | Regex on branch + body       | Ticket keys in PR/commit text |
| CI/CD, Argo CD     | Commit SHA / image tag       | Deploy → PR → ticket |
| Sentry             | Release / commit metadata    | Error → release → PR |
| PagerDuty          | Service + time correlation   | Incident → deploy/service |
| Slack, Teams       | Regex + `<@user>` mentions   | Ticket refs, people mentions |
| Confluence, Notion | Embedded hyperlinks          | Page → ticket/PR references |
| Unstructured prose | LLM extraction (opt-in)      | Decisions, action items, owners |

### Identity hub

Every person has one record, identities joined on email — scoped to the chain's sources:

```
unified_users {
  user_id      : "abc123"
  email        : "alice@company.ai"
  display_name : "Alice Chen"
  identities: {
    jira:   { accountId, displayName },
    github: { login, name },
    slack:  { user_id, display_name },
    ...                              # one block per connected source
  }
}
```

### who_owns across the chain

`who_owns(target)` tallies ownership signals from every stage — not just assignees and PR authors:
who commits to the service, who's paged for it, who discusses it in Slack, who wrote its docs.
Recency-weighted so current owners surface above past contributors.

---

## Phase 3 — Proactive Intelligence: Watch the Chain, Close the Loop

**Goal:** yoku stops waiting to be asked. It continuously scans the delivery chain, finds
broken links nobody is watching for, and proposes — or executes — the fix in the source tool.

This is the core differentiator. The detection half already exists (`analysis/consistency.py`);
Phase 3 adds the **act** half: detect → gate → dedup → write back → record.

### What proactive intelligence looks like

**Plan ↛ Code:**
```
FINDING: Ticket marked Done with no merged PR
JIRA:   AS-1234 "Done", assigned Alice, 3 days ago
GitHub: no merged PR references AS-1234
ACTION: comment on AS-1234 — "marked Done but no PR linked; link it or reopen?"
        [Post comment] [Dismiss]
```

**Code ↛ Ship:**
```
FINDING: PR merged but never deployed
GitHub: asato-svc#88 merged 6 days ago
ArgoCD: commit SHA not present in any environment
ACTION: comment on the PR — "merged but not deployed to any env in 6 days"
        [Post comment] [Ping author] [Dismiss]
```

**Ship → Broke:**
```
FINDING: Deploy followed by incident / error spike
ArgoCD: payment-svc v2.3 deployed 14:02
Sentry: errors +480% on release v2.3 since 14:05
PagerDuty: P2 opened 14:11, no linked ticket
ACTION: create fix ticket linked to the deploy + PR; notify on-call thread
        [Create ticket] [Dismiss]
```

**Talk ↛ Record:**
```
FINDING: Decision made in Slack with no record
Slack:  #eng "we agreed to deprecate the v1 API by June" (12 replies)
JIRA:   no deprecation ticket
Docs:   no migration guide
ACTION: draft epic "Deprecate v1 API" + sub-tasks; draft #eng notice
        [Create epic] [Draft notice] [Dismiss]
```

### How it works technically

The deep agent runs in two modes:

**Reactive** (today): user message → agent answers using all chain data.

**Proactive** (new): scheduled scanners drive the agent with analysis prompts instead of user
questions; findings surface to the tenant **Insights** panel:

```python
class DoneWithoutPRScanner:
    schedule = "every 4 hours"
    requires = ["issues", "code"]

    def prompt(self) -> str:
        return """
        Find issues in a Done status with no merged PR referencing them.
        For each, propose a comment back on the ticket.
        """
```

Each finding shows: what was found + which stages it came from, confidence, the proposed action,
and one-click approve / edit / dismiss.

### Safety controls (the difference between teammate and spam bot)

- **Shadow mode** — log every action it *would* take without doing it. Run here first.
- **Idempotency** — an `actions_taken` ledger keyed by a finding fingerprint (unique index) so it
  never comments twice on the same thing.
- **Confidence gate** — only act on unambiguous findings.
- **Escalation ladder** — comment once → ping owner → notify lead → stop. Never nag.
- **Tiers** — comment = auto; mutate (transition ticket, close PR) = human approval.
- **Kill switch** — global + per-rule.

### Scanner library (ships with yoku)

| Scanner                  | Stages required          | What it finds |
|--------------------------|--------------------------|---------------|
| DoneWithoutPRScanner     | issues + code            | Ticket Done, no merged PR |
| MergedNotDeployedScanner | code + deploy            | PR merged, never deployed |
| DeployIncidentScanner    | deploy + incidents + errors | Deploy followed by incident / error spike |
| IncidentWithoutTicket    | incidents + issues       | Incident with no tracking/fix ticket |
| StaleInProgressScanner   | issues + code            | Long "In Progress", no commits |
| UnreviewedPRScanner      | code                     | PR open too long, no review |
| BlockedTicketScanner     | issues + messages        | Blocked in Slack, no escalation |
| DecisionNotRecorded      | messages + issues + docs | Decision made, no follow-up artifact |
| OwnershipGapScanner      | code + issues + identity | Critical service with no clear owner |

Custom scanners via the SDK — same interface, drop a class in the scanners folder.

---

## Phase 4 — Scale and Depth

**Goal:** handle large tenants, improve accuracy over time, go deeper on findings.

### Atlas Vector Search (at scale)
When a tenant corpus outgrows RAM, swap the numpy in-memory matrix for MongoDB Atlas Vector Search
(HNSW on `embedding`). Search logic, BM25, RRF, adjacency, ZeroEntropy unchanged — backend only.

### LLM entity extraction (opt-in, per connector)
For sources without structured identifiers (docs, prose): lightweight extraction at ingest →
decisions, action items, owners → `entity_links` with `extractor: "llm"`, `confidence: 0.6`.
Disabled by default; enabled per connector in tenant settings.

### Feedback loop
Approve / edit / dismiss signals calibrate scanners per tenant: approved → weight up similar
patterns; dismissed → filter that pattern; edits → learn the right framing.

### Action execution
For approved fixes, the agent executes directly via the connector write path: comment, create
ticket, transition status, post Slack message, draft doc. Scope configurable per tenant —
read-only, propose-only, or execute-with-approval.

---

## Architecture (Target State)

```
DELIVERY-CHAIN SOURCE
(built-in connector / SDK connector / REST ingest API)
     │
     ▼
documents          entity_links         unified_users
(all stages,       (typed cross-stage   (N-source
 unified shape)     edges)               identity hub)
     │                   │                    │
     └───────────────────┴────────────────────┘
                         │
              Knowledge Pipeline
              embed → extract_links → unify_users
                         │
              Search Layer  (unchanged internals)
              vector + BM25 + RRF + ZeroEntropy + adjacency
                         │
                    Deep Agent
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
         REACTIVE               PROACTIVE
      User asks question      Scanners run on schedule
      Agent searches +        detect → gate → dedup →
      reasons + answers       write back → record
                              Human approves / edits / dismisses
                         │
              FastAPI + JWT + multi-tenant + SSE
              React UI: chat + insights panel + connector settings
```

---

## What Stays the Same

The core intelligence infrastructure is correct today and does not change:

- MongoDB + per-tenant database isolation
- Hybrid search: vector + BM25 + RRF
- ZeroEntropy reranker
- Adjacency scoring (migrates to read from `entity_links`)
- FastAPI + JWT auth + SSE streaming
- React UI shell

---

## Migration Path (No Big Bang)

1. Unified `documents` collection + `UnifiedDoc` contract
2. JIRA + GitHub + Slack connectors write to both existing collections AND `documents`
3. REST ingest API — external pipelines can push immediately
4. Generic domain tools alongside existing source-specific tools
5. Tier-1 connectors (CI/CD + deploy, incidents, errors, docs) — driven by first tenant needs
6. `entity_links` collection + `extract_links` pipeline
7. Connector **write path** + `actions_taken` ledger (idempotency)
8. Proactive scanner framework + first 3 chain scanners (shadow mode first)
9. Expand `unified_users` to N-source identity
10. Tier-2 substitutes, feedback loop, full action execution

---

## Open Questions

- Which Tier-1 connector do first tenants need most — CI/CD+deploy, or incidents/errors?
- Which scanner delivers value earliest — Done-without-PR, merged-not-deployed, or deploy-incident?
- Writer identity per connector: dedicated bot account, or write-as-user?
- Action execution scope: comment-only first, or propose + execute from day one?
- Cross-surface notifications (Slack ping about a private ticket): enforce source ACL at notify time?
- Feedback loop: tenant-level calibration or global model improvement?
