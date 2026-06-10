"""Detector contract — one frozen spec per gap type.

A detector is a cheap pure scan over the synced collections that emits
candidate gap drafts. It NEVER decides to message anyone (that is Phase 3+
judgment); it only nominates. Drafts are dicts:

    {
      "item_key":     "jira/AS-4396",          # canonical key of the gapped item
      "title":        "Add rate limiting",      # display line for the Inbox
      "person_query": {"jira.displayName": …},  # ds-unified-users lookup, or None
      "person_name_raw": "Priya Sharma",        # fallback label when unresolved
      "evidence":     {…, "gap_age_days": 14},  # gap_age_days drives maturation
      "confidence":   0.7,
      "url":          "https://…",              # optional deep link
    }

`maturation_days` is the hysteresis window: a signal is only surfaced once the
gap has persisted that long (per `gap_age_days` or time since first seen) —
transient states ("merged ten minutes ago") never reach a human.
`recent_days` bounds the scan to recently-touched docs so years-old history
doesn't flood the Inbox on day one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Detector:
    name: str
    kind: str  # "drift" for plan-vs-code mismatches
    description: str
    maturation_days: int
    recent_days: int
    fn: Callable[[], list[dict]]
