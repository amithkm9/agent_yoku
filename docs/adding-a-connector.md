---
name: adding-a-connector
description: Add a new data source (Confluence, Notion, …) to agent-yoku so the agent can query it.
whenToUse: Use when onboarding any new external source into agent-yoku — pulling its data into Mongo and making it queryable by the deepagent.
---

# Adding a connector

A connector pulls **one** external source into Mongo and registers it so the
agent can query it. Because the toolkit is schema-driven, onboarding a source is
**a model + a SourceSpec + relationship entries** — you do **not** edit
`agent_yoku/agent/tools.py`, the agent prompt, or any tool description.

## Mental model

```
external API ──(connector)──▶ Mongo collection ──(registries)──▶ agent tools
                client.py        models/<x>.py        sources.py
                ingest.py        schema_registry      relationships.yaml
```

- **Connector** = how to fetch + upsert (`connectors/<source>/`).
- **Model** = the collection's schema *and* its self-description (docstring +
  per-field `description`/`display`/`filterable`).
- **Registries** = routing (`sources.py`) and joins (`relationships.yaml`).

The generic tools (`filter` / `get` / `linked` / `semantic_search` /
`list_collections` / `describe_collection`) read those registries, so they cover
the new source the moment it's registered.

## Steps

1. **Scaffold** — copy an existing connector folder as your starting point:
   `cp -r agent_yoku/connectors/jira agent_yoku/connectors/<source>`.
2. **`client.py`** — replace with your auth + paginated REST helpers. Decorate
   every external call with `@make_retry("<source>", log)` from
   `agent_yoku.utils`.
3. **`ingest.py`** — adapt `main()` to upsert documents into a new Mongo
   collection. No LLM calls in ingest; keep it a pure fetch→transform→upsert.
   Sources fed from a file export rather than a live API use a sibling module
   (see `connectors/slack/export_ingest.py`) — the contract is the same: land
   normalized docs in Mongo.
4. **`__init__.py`** — declare the module-level `META: ConnectorMeta = {...}`
   (see fields below). `agent-yoku list-connectors` auto-discovers it.
5. **`users_ingest.py`** *(optional)* — only if the source has a member
   directory to feed `resolve_user` / `who_knows`.
6. **CLI** — add an ingest subcommand in `agent_yoku/cli.py`.
7. **Register the collection** in
   `agent_yoku/storage/mongo.py::ALLOWED_COLLECTIONS`.
8. **Model** — add a Pydantic model in `agent_yoku/models/`. This is the
   collection's contract:
   - the **class docstring** becomes the collection description the agent sees;
   - each field carries a `description`, and `display` / `filterable` metadata
     (see `models/_fields`).
   Then map the collection name → model in
   `agent_yoku/agent/schema_registry.py::COLLECTION_MODELS`.
9. **Source registry** — add a `SourceSpec` to
   `agent_yoku/agent/sources.py::SOURCES` with: `name` (matches the `source`
   field on indexed docs), `collection` (the `schema_registry` key), `label`
   (shown in prompts/errors), `key_pattern` (regex identifying a key) +
   `key_example`, `sort_field` (recency, default `"updated"`), and `embeddable`
   (default `True` — whether it joins the semantic index).
10. **Relationships** *(if it links to existing data)* — add an entry to
    `agent_yoku/agent/relationships.yaml` so `linked` can join it. Each entry is
    `entity1`/`entity2` (collection names) + a `join` with `local_field` (the
    field on `entity1` holding `entity2` keys) and `foreign_field` (the matched
    field on `entity2`). Example: a `slack_messages` doc whose `jira_keys`
    references a `jira_tickets` `key`.

## `ConnectorMeta` fields (`connectors/base.py`)

| key | meaning |
|-----|---------|
| `name` | short id, e.g. `"slack"` |
| `source` | external system human name |
| `display_name` | UI label |
| `description` | one line |
| `entity` | primary entity, e.g. `"messages"` |
| `sync_summary` | what a sync ingests |
| `setup_steps` | admin-facing setup checklist |
| `field_help` | field name → explanation for setup UIs |

## Do NOT

- Edit `agent_yoku/agent/tools.py` to teach it about the collection — the tools
  are source-agnostic by design.
- Add the collection's description or fields to a prompt — they come from the
  model.
- Hardcode key shapes anywhere but the `SourceSpec`.

## Verify

```bash
agent-yoku list-connectors          # new source appears
agent-yoku <your-ingest-cmd>        # data lands in Mongo
make schemas                        # regenerate JSON Schema docs
make agent-smoke                    # agent can see + query the source
```

If `list_collections` / `describe_collection` show the new collection with the
right description and filterable fields, the registries are wired correctly.
