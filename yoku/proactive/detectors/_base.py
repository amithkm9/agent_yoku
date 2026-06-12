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

The spec owns EVERYTHING downstream stages need to know about the gap type —
judge and orchestrator read it from here, so adding a detector is one file:

- `label` / `gap_description` — human/UI label and the phrasing the person-
  judgment prompt uses.
- `maturation_days` — hysteresis: surface only once the gap persisted this
  long ("merged ten minutes ago" never reaches a human). `recent_days` bounds
  the scan so years-old history doesn't flood the Inbox.
- `baseline_rate_field` / `baseline_n_field` — which per-person baseline
  stats mean "this pattern is their normal workflow" (None: no baseline tier).
- `compose` — (signal, target) -> the one-ask DM text; None falls back to a
  generic nudge.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Detector:
    name: str
    kind: str  # "drift" for plan-vs-code mismatches
    label: str
    description: str
    gap_description: str
    maturation_days: int
    recent_days: int
    fn: Callable[[], list[dict]]
    baseline_rate_field: str | None = None
    baseline_n_field: str | None = None
    compose: Callable[[dict, dict], str] | None = None
