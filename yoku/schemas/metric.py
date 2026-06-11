"""Pydantic schema for the weekly trend metrics collection (ds-metrics)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from yoku.schemas._fields import doc_field

#: The closed set of weekly series the metrics pipeline computes.
METRIC_NAMES = [
    "prs_opened",
    "prs_merged",
    "pr_cycle_time_days",
    "pr_cycle_time_p90_days",
    "tickets_created",
    "tickets_done",
    "signals_open",
]


class MetricPoint(BaseModel):
    """Weekly engineering trend metrics — one row per (metric, week).

    Use for trend questions: merge velocity, PR cycle time, ticket throughput,
    how the proactive gap count moves. `week` is the ISO date of that week's
    Monday; `value` is a count, except the cycle-time series (days from PR
    creation to merge, with `n` PRs behind each point): the median
    (`pr_cycle_time_days`) is dominated by fast automated merges, so use
    `pr_cycle_time_p90_days` for how long human-paced PRs take.
    `tickets_done` counts status transitions observed by the event stream, so
    it accumulates from when event capture went live rather than all history.
    """

    model_config = ConfigDict(extra="allow")

    metric: str = doc_field(
        "Series name.",
        default=...,
        display=True,
        filter_arg="metric",
        enum=METRIC_NAMES,
    )
    week: str = doc_field(
        "ISO date of the week's Monday, e.g. '2026-06-08'.", default=..., display=True
    )
    value: float = doc_field("The metric value for that week.", default=0.0, display=True)
    n: int | None = doc_field("Sample size behind aggregate metrics (e.g. PRs in the median).")
