"""Proactive engine — the background loop behind docs/yoku_agent.md.

Phase 1 (events) lives here; detectors, judgment, and the orchestrator land in
later phases. Everything in this package is engine-internal: its collections
stay off the agent's `ALLOWED_COLLECTIONS` whitelist.
"""
