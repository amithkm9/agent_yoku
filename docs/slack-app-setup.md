---
name: slack-app-setup
description: Configure the Slack app so yoku can speak (DM people) and listen (receive replies and @mentions) — the Phase 4b bot voice.
whenToUse: Follow when enabling yoku's proactive Slack voice for a tenant — after the basic Slack connector (ingest) already works.
---

# Slack app setup — yoku's bot voice

The ingest connector only *reads* channels. The bot voice additionally lets
yoku **DM the right person** about a confirmed gap and **hear the reply**.
Everything is per-tenant and opt-in: tenants without these settings keep
ingest-only behavior.

## 1. Scopes (OAuth & Permissions → Bot Token Scopes)

Already required for ingest:
`channels:history`, `channels:read`, `users:read`, `users:read.email`

Add for the voice:

| scope | why |
|---|---|
| `chat:write` | post messages |
| `im:write` | open DM conversations |
| `im:history` | receive DM events |
| `app_mentions:read` | receive @yoku mentions |

Reinstall the app to the workspace after adding scopes (token may rotate —
update it in Settings if so).

## 2. Event subscriptions

In the app's **Event Subscriptions**:

1. Enable events, set the Request URL to
   `https://<your-api-host>/api/slack/events`
   (local dev: expose :8000 with `ngrok http 8000` and use the ngrok URL).
   Slack sends a `url_verification` challenge — yoku answers it automatically.
2. Subscribe to bot events: **`message.im`** and **`app_mention`**.

## 3. Tenant settings (yoku → Settings → Slack)

- **Team ID** — the workspace id starting with `T` (Slack → workspace name →
  About, or from `auth.test`). Routes inbound events to the right tenant.
- **Signing secret** — app **Basic Information → Signing Secret**. Verifies
  every inbound request really came from Slack (HMAC v0, 5-minute replay
  window). Stored encrypted, like the bot token.

## 4. Verify

```bash
# dry run — resolves the person and shows the message without posting
poetry run yoku --tenant <tenant> slack-test-dm "your.name@company.com"

# real DM (send it to yourself first)
poetry run yoku --tenant <tenant> slack-test-dm "your.name@company.com" --send
```

Reply to the DM in Slack, then confirm the reply landed:

```bash
mongosh --quiet --eval 'db.getSiblingDB("yoku_<tenant>").slack_inbound.find().sort({received_at:-1}).limit(1)'
```

That round-trip — DM out, reply captured in `slack_inbound` — is the M6
acceptance test. Phase 5 (build-plan M7) consumes `slack_inbound` to thread
replies into open conversations.

## Guardrails already enforced

- Outbound resolves people **only** via `ds-unified-users` Slack identity —
  no identity, no DM (never guess a person). Bots are never DM'd.
- `send_dm` defaults to dry-run; callers opt into sending explicitly.
- Inbound: signature-verified per tenant, deduped by Slack `event_id`,
  bot-echo filtered so yoku can't talk to itself.
