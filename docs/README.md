# yoku docs

Focused reference docs. Start with the root [`README.md`](../README.md) for the
overview and [`CLAUDE.md`](../CLAUDE.md) if you're working on the repo with a
coding agent.

```
docs/
└── adding-a-connector.md   # onboard a new source (Confluence, Notion, …):
                            #   connector → Mongo collection → registries → agent
```

Each doc is single-topic and front-matter'd (`name` / `description` /
`whenToUse`) so it's both human- and agent-discoverable.
