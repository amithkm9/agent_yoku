# agent-yoku Roadmap

## Vision

agent-yoku is the **full knowledge brain of a company** — a multi-tenant deep agent that connects
to every internal data source a company has, understands the relationships across all of it, and
proactively surfaces conflicts, gaps, optimizations, and resolutions without being asked.

The most valuable insights a company needs don't exist inside a single tool. They exist in the
space between tools — the conflict between what Slack says is blocked and what JIRA says is
in progress, the overlap between two teams solving the same problem, the $47k in unused licenses
that nobody noticed. agent-yoku sees across all sources simultaneously and acts on what it finds.

```
                        ANY COMPANY DATA SOURCE
                  (issues, code, messages, meetings, docs,
                   identity, spend, hardware, software, security)
                               │
                               ▼
                    ┌─────────────────────┐
                    │  agent-yoku brain   │
                    │                     │
                    │  embed + index      │
                    │  knowledge graph    │
                    │  unified identity   │
                    └──────────┬──────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
                    ▼                     ▼
             REACTIVE                PROACTIVE
        answer any question      surface conflicts,
        across all sources       gaps, optimizations,
                                 and propose resolutions
```

---

## Current State (v1)

- Two sources: JIRA + GitHub only
- Source-specific collections: `jira_tickets`, `github_prs`
- Source-specific agent tools: `filter_jira`, `filter_prs`, `semantic_search`
- Linking: regex for `AS-\d+` JIRA keys in PR text only
- Identity: JIRA ↔ GitHub join on email
- Search: hybrid vector + BM25 + RRF + ZeroEntropy reranker
- Multi-tenant: per-tenant MongoDB database, JWT auth, React UI
- Agent mode: reactive only — answers questions when asked

---

## Phase 1 — Foundation: Connect Any Source

**Goal:** Any data source can feed agent-yoku. The agent reasons about domains, not specific
source names. Tenants configure which connectors to enable.

### Three ways data gets in

**Tier 1 — Built-in connectors** (common enterprise tools, ship with agent-yoku):

| Domain    | Sources                                     | Agent tool          |
|-----------|---------------------------------------------|---------------------|
| issues    | JIRA, Linear, Asana, Shortcut               | `filter_issues`     |
| code      | GitHub, GitLab, Bitbucket                   | `filter_code`       |
| messages  | Slack, Microsoft Teams                      | `filter_messages`   |
| meetings  | Zoom, Teams transcripts, Otter              | `filter_meetings`   |
| docs      | Confluence, Notion, SharePoint, Google Docs | `filter_docs`       |
| identity  | Okta, Azure AD, JumpCloud                   | `who_is`            |
| spend     | NetSuite, Coupa, Brex, Stripe               | `filter_spend`      |
| hardware  | Jamf, Intune, CrowdStrike, NinjaOne         | `filter_assets`     |
| software  | Okta apps, Entra, JumpCloud apps            | `filter_software`   |

**Tier 2 — Connector SDK** (companies with custom or internal sources):

```python
# Implement one interface, get full brain integration
class MyConnector:
    META = ConnectorMeta(name="salesforce", domain=Domain.CRM)

    def ingest(self, config: dict) -> Iterator[UnifiedDoc]:
        for record in fetch_from_salesforce(config):
            yield UnifiedDoc(
                key=f"salesforce/{record['id']}",
                domain="crm",
                title=record["name"],
                body=record["description"],
                author=record["owner_email"],
                updated=record["modified_at"],
            )
```

Install the package → connector appears in tenant settings UI automatically.

**Tier 3 — REST ingest API** (companies with existing data pipelines):

```
POST /api/v1/ingest
{
  "key":    "salesforce/001abc",
  "domain": "crm",
  "title":  "Acme Corp renewal",
  "body":   "...",
  "author": "alice@company.ai",
  "updated": "2024-03-15T10:00:00Z",
  "meta":   { ... }
}
```

Any existing pipeline just pushes documents. No connector code needed inside agent-yoku.

### Unified document model

Every source produces the same shape — the agent never needs to know which source is underneath:

```
documents {
  key         : "{source_type}/{native_key}"
  source_type : "jira" | "linear" | "slack" | "zoom" | ...
  domain      : "issues" | "code" | "messages" | "meetings" | ...
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

The deep agent gets generic tools — it routes by what it needs, not where data lives:

```python
search(query, domains=["all"], k=50)
filter_issues(status, assignee, label, since_days, limit)
filter_messages(channel, author, since_days, limit)
filter_meetings(participant, since_days, limit)
filter_docs(space, author, since_days, limit)
get(key)        # any document, auto-routed by source_type
linked(key)     # cross-source hops
who_knows(topic)
```

---

## Phase 2 — Knowledge Graph: Understanding Relationships

**Goal:** agent-yoku understands how documents across sources relate to each other.
This is what makes cross-domain insights possible — without the graph, you have indexed data.
With the graph, you have a brain.

### entity_links collection

Every relationship between any two documents, regardless of source:

```
entity_links {
  from_key   : "slack/C12345/1710000000"
  to_key     : "jira/AS-1234"
  link_type  : "references" | "discusses" | "assigned_to" | "attended" | "owns" | ...
  context    : "let's fix AS-1234 before the release"
  confidence : 1.0
  extractor  : "regex" | "metadata" | "llm"
}
```

**Extraction by source type:**

| Source | Method | What gets linked |
|---|---|---|
| JIRA, Linear | API metadata | Linked issues, PRs, epics |
| GitHub | Regex on branch + body | Ticket keys in PR text |
| Slack | Regex + `<@user>` mentions | Ticket refs, people mentions |
| Zoom / Teams | Participant names → identity | Who attended, what was discussed |
| Confluence / Notion | Embedded hyperlinks | Page references |
| Spend (NetSuite, Coupa) | Invoice metadata | Vendor, approver, project code |
| Unstructured prose | LLM extraction (opt-in) | People, projects, decisions, action items |

### Expanded identity hub

Every person has one record, with identities from all sources joined on email:

```
unified_users {
  user_id      : "abc123"
  email        : "alice@company.ai"
  display_name : "Alice Chen"
  identities: {
    jira   : { accountId, displayName },
    linear : { id, name },
    github : { login, name },
    slack  : { user_id, display_name },
    zoom   : { user_id, display_name },
    okta   : { uid, login },
    ...
  }
}
```

### who_knows across all sources

`who_knows(topic)` now tallies ownership signals from every source — not just assignees and PR
authors. Who talks about it in Slack, who attends meetings about it, who approves spend in that
area, who wrote the docs. Recency-weighted so current owners surface above past contributors.

---

## Phase 3 — Proactive Intelligence: The Brain That Watches

**Goal:** agent-yoku stops waiting to be asked. It continuously scans all data, identifies
problems and opportunities nobody thought to look for, and proposes specific resolutions.

This is the core differentiator. Most problems in a company exist and are visible in the data —
but nobody is looking in all the right places at the same time. agent-yoku does.

### What proactive intelligence looks like

**Conflict detection:**

```
FINDING: Duplicate work — two teams solving the same problem

Team A (JIRA):   AS-1200–AS-1210 — building SSO auth integration
Team B (Linear): LIN-88–LIN-95  — building SSO auth integration
Cross-source:    No Slack conversation between the two teams exists

RESOLUTION PROPOSED:
  Option 1: Merge workstreams — assign AS-1200 as parent, close LIN-88–LIN-95
  Option 2: Split scope — Team A handles IdP, Team B handles SP
  [Approve Option 1] [Approve Option 2] [Edit] [Dismiss]
```

```
FINDING: Ticket at risk — blocked with no escalation path

JIRA:    AS-1234 "In Progress", assigned Alice, due Friday
Slack:   Alice posted "completely blocked on auth dependency" 3 days ago
GitHub:  No PR from Alice in 2 weeks
Meeting: Auth dependency discussed — no resolution reached

RESOLUTION PROPOSED:
  Reassign AS-1234 to Bob (auth dependency owner, 2 open slots this sprint)
  or push to next sprint and unblock Alice on current auth work first
  [Reassign to Bob] [Push to next sprint] [Dismiss]
```

**Gap detection:**

```
FINDING: Decision made with no record

Meeting (Zoom, 2024-03-14): "We agreed to deprecate the v1 API by June"
JIRA:    No ticket exists for v1 API deprecation
Docs:    No migration guide written
Slack:   No announcement to affected teams

RESOLUTION PROPOSED:
  Create JIRA epic: "Deprecate v1 API" with sub-tasks pre-populated
  Draft deprecation notice for #engineering channel
  [Create epic + tasks] [Draft notice] [Dismiss]
```

**Optimization detection:**

```
FINDING: Unused software licenses — $47k/year

Okta:        200 Figma licenses active
Usage data:  47 unique logins in last 30 days (153 unused)
Renewal:     Contract renews June 15 — 18 days away

RESOLUTION PROPOSED:
  Downgrade to 50-seat plan before June 15
  Notify IT admin with renewal date + current usage data
  [Notify IT admin] [Dismiss]
```

**Risk detection:**

```
FINDING: Critical service — no owner, no tests, engineer leaving

GitHub:  payment-service — 0 test files, last commit 4 months ago
JIRA:    3 open P1 bugs assigned to Dave
HR/Okta: Dave's last day is Friday

RESOLUTION PROPOSED:
  Reassign Dave's 3 P1 tickets before Friday
  Flag payment-service for test coverage sprint
  [Reassign tickets] [Create test coverage ticket] [Dismiss]
```

### How proactive intelligence works technically

The deep agent runs in two modes:

**Reactive mode** (today): user sends a message → agent answers using all available data.

**Proactive mode** (new): scheduled scanners run on a configurable cadence, drive the agent
with analysis prompts rather than user questions, surface findings to the tenant dashboard:

```python
# Same agent, same tools — driven by scanners instead of user messages
class ConflictScanner:
    schedule = "every 4 hours"

    def prompt(self) -> str:
        return """
        Search for JIRA/Linear issues that appear to solve the same problem
        from different teams with no cross-team communication. For each pair
        found, describe the overlap and propose a resolution.
        """

class GapScanner:
    schedule = "daily at 9am"

    def prompt(self) -> str:
        return """
        Find decisions made in meetings or Slack in the last 7 days that have
        no corresponding ticket, doc, or follow-up action. For each gap found,
        propose the artifact that should be created.
        """

class OptimizationScanner:
    schedule = "weekly"

    def prompt(self) -> str:
        return """
        Compare software license counts against actual usage data. Identify
        tools with >20% unused licenses where a renewal is approaching.
        Quantify the savings and propose the downgrade action.
        """
```

Findings surface in a dedicated **Insights** panel in the UI. Each finding shows:
- What was found and which sources it came from
- Confidence level (based on data quality and link confidence)
- Proposed resolution with specific actions
- One-click approve / edit / dismiss

Approved actions can be executed by the agent directly (create JIRA ticket, send Slack message,
draft a doc) or handed off to the human for manual execution.

### Scanner library (ships with agent-yoku)

| Scanner | Sources required | What it finds |
|---|---|---|
| DuplicateWorkScanner | issues | Two teams solving the same problem |
| BlockedTicketScanner | issues + messages | Tickets blocked in Slack with no escalation |
| DecisionGapScanner | meetings + issues + docs | Decisions made with no follow-up artifact |
| SoftwareOptimizationScanner | software + identity + spend | Unused licenses before renewal |
| HardwareComplianceScanner | hardware + identity | Devices out of policy |
| OwnershipGapScanner | issues + code + identity | Critical services with no clear owner |
| SpendAnomalyScanner | spend + identity | Unusual spend patterns |
| SecurityRiskScanner | security + identity + software | Inactive users with active access |
| OnboardingGapScanner | identity + issues + docs | New employees missing access or context |
| VelocityBottleneckScanner | issues + code + meetings | Teams or services that are systemic blockers |

Custom scanners can be added via the SDK — same interface, drop a class in the scanners folder.

---

## Phase 4 — Scale and Depth

**Goal:** The brain handles large tenants, improves its own accuracy over time, and goes deeper
on the insights it surfaces.

### Atlas Vector Search (at scale)

When per-tenant corpus grows beyond what fits in RAM:
- Replace numpy in-memory matrix with MongoDB Atlas Vector Search (HNSW index on `embedding`)
- No change to search logic, BM25, RRF, adjacency, or ZeroEntropy
- Swap only the vector retrieval backend — everything else unchanged

### LLM entity extraction (opt-in, per connector)

For sources where structured identifiers don't exist (transcripts, email, unstructured docs):
- Lightweight LLM extraction prompt at ingest time
- Extracts: mentioned people, referenced projects, decisions, action items
- Writes to `entity_links` with `extractor: "llm"`, `confidence: 0.6`
- Disabled by default — enabled per connector in tenant settings

### Feedback loop

Findings that humans approve, edit, or dismiss feed back into scanner calibration:
- Approved findings → scanner weights increase for similar patterns
- Dismissed findings → scanner learns to filter that pattern for this tenant
- Edits → scanner learns the correct framing for this tenant's context

### Action execution

For approved resolutions, the agent can execute directly:
- Create JIRA / Linear ticket
- Send Slack message or channel announcement
- Create Confluence / Notion doc from template
- Trigger webhook to external system

Scope of what the agent can execute is configurable per tenant — read-only, propose-only, or
full execution with human approval required.

---

## Architecture (Target State)

```
ANY SOURCE
(built-in connector / SDK connector / REST ingest API)
     │
     ▼
documents          entity_links         unified_users
(all domains,      (typed cross-source  (N-source
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
              │                     │
         REACTIVE               PROACTIVE
      User asks question      Scanners run on schedule
      Agent searches +        Agent finds conflicts,
      reasons + answers       gaps, optimizations
                              Surfaces findings + proposals
                              Human approves / edits / dismisses
                              Agent executes approved actions
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
2. JIRA + GitHub connectors write to both existing collections AND `documents`
3. REST ingest API — external pipelines can push immediately
4. Generic domain tools alongside existing source-specific tools
5. 2–3 new built-in connectors (driven by first tenant needs)
6. `entity_links` collection + `extract_links` pipeline
7. Proactive scanner framework + first 3 scanners
8. Expand unified_users to N-source identity
9. Deprecate source-specific tools once all tenants migrated
10. Full scanner library + feedback loop + action execution

---

## Open Questions

- Which 2–3 connectors do the first tenants need most?
- Which scanners deliver the most value earliest (duplicate work? blocked tickets? license optimization)?
- For meeting transcripts: what's the transcript delivery mechanism per source (Zoom API, Teams Graph API, manual upload)?
- Action execution scope: read-only first, or propose + execute from day one?
- PII policy: Slack DMs vs public channels only? Transcript retention limits per tenant?
- Feedback loop: tenant-level calibration or global model improvement?
