# yoku docs

Focused reference docs. Start with the root [`README.md`](../README.md) for the
overview and [`CLAUDE.md`](../CLAUDE.md) if you're working on the repo with a
coding agent.

```
docs/
├── adding-a-connector.md   # onboard a new source (Confluence, Notion, …):
│                           #   connector → Mongo collection → registries → agent
├── yoku_agent.md           # proactive agent — the detailed build plan behind
│                           #   vision.md (events → judge → converse → act)
├── memory-engine.md        # how yoku learns from replies and follows through:
│                           #   episodes → understand → consolidate → recall (A–D)
├── feature-roadmap.md      # deep dive on 8 candidate features to extend yoku
├── build-plan.md           # THE agreed order — merges the 8 features + 6
│                           #   proactive phases into one milestone sequence
└── slack-app-setup.md      # enable the bot voice: scopes, event subscriptions,
                            #   team id + signing secret, verification steps
```

Each doc is single-topic and front-matter'd (`name` / `description` /
`whenToUse`) so it's both human- and agent-discoverable.
