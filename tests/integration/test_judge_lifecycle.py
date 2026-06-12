"""Judgment + memory lifecycle (M5) against a scratch mongo db.

LLM tier is always stubbed — these tests pin the deterministic machinery:
tier order, baseline suppression, memory suppression, the AND-gate, budget
deferral, and the done-when criteria from docs/yoku_agent.md Phase 3.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)


def _iso(days_ago: int) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat()


def _seed_person(user_id: str, jira_name: str, gh_login: str | None = None) -> None:
    from yoku.db.mongo import ds_unified_users_collection

    ds_unified_users_collection().insert_one(
        {
            "user_id": user_id,
            "jira": {"displayName": jira_name},
            "github": {"login": gh_login} if gh_login else None,
            "email": f"{user_id}@x.io",
        }
    )


def _seed_done_tickets(assignee: str, with_pr: int, without_pr: int, key_prefix: str) -> None:
    from yoku.db.mongo import dc_jira_collection

    docs = []
    for i in range(with_pr):
        docs.append(
            {
                "key": f"{key_prefix}-P{i}",
                "status": "Done",
                "assignee": assignee,
                "linked_prs": [{"key": f"o/r#{i}"}],
                "updated": _iso(5),
                "summary": "code work",
            }
        )
    for i in range(without_pr):
        docs.append(
            {
                "key": f"{key_prefix}-N{i}",
                "status": "Done",
                "assignee": assignee,
                "linked_prs": [],
                "updated": _iso(5),
                "summary": "work item",
            }
        )
    dc_jira_collection().insert_many(docs)


@pytest.mark.integration
def test_baselines_compute_rates(tenant):
    from yoku.proactive.baselines import compute_baselines, get_baseline

    _seed_person("u_pm", "Pia Manager")
    _seed_person("u_eng", "Evan Engineer")
    _seed_done_tickets("Pia Manager", with_pr=1, without_pr=9, key_prefix="PM")
    _seed_done_tickets("Evan Engineer", with_pr=9, without_pr=1, key_prefix="ENG")

    assert compute_baselines(now=_NOW) == 2
    assert get_baseline("u_pm")["done_no_pr_rate"] == 0.9
    assert get_baseline("u_eng")["done_no_pr_rate"] == 0.1
    assert get_baseline("u_pm")["done_no_pr_n"] == 10


@pytest.mark.integration
def test_normal_for_person_suppressed_while_firing_for_others(tenant):
    """The Phase 3 done-when: Pia's pattern is her workflow (suppressed,
    zero LLM calls); the same pattern on Evan goes to the LLM gate."""
    from yoku.proactive.baselines import compute_baselines
    from yoku.proactive.judge import judge_signals
    from yoku.proactive.signals import run_detectors

    _seed_person("u_pm", "Pia Manager")
    _seed_person("u_eng", "Evan Engineer")
    _seed_done_tickets("Pia Manager", with_pr=1, without_pr=9, key_prefix="PM")
    _seed_done_tickets("Evan Engineer", with_pr=9, without_pr=1, key_prefix="ENG")

    compute_baselines(now=_NOW)
    run_detectors()  # PM-N* and ENG-N0 become matured done_no_pr signals

    llm_calls: list[str] = []

    def stub(kind: str, prompt: str) -> dict:
        llm_calls.append(kind)
        return (
            {"real": True, "reason": "looks like code work"}
            if kind == "item"
            else {"normal_for_person": False, "reason": "rare for them"}
        )

    counts = judge_signals(now=_NOW, llm=stub, budget=50)
    assert counts["suppressed_baseline"] == 9  # all of Pia's
    assert counts["kept"] == 1  # Evan's one gap survives the AND-gate

    from yoku.db.mongo import signals_collection

    evan = signals_collection().find_one({"item_key": "jira/ENG-N0"})
    assert evan["status"] == "open" and evan["verdict"]["real"] is True
    pia = signals_collection().find_one({"item_key": "jira/PM-N0"})
    assert pia["status"] == "judged"
    assert pia["verdict"]["suppressed_by"] == "baseline"
    # Pia's signals never reached the LLM — only Evan's item+person calls.
    assert llm_calls == ["item", "person"]


@pytest.mark.integration
def test_human_confirmation_outranks_all_automated_tiers(tenant):
    """A confirmed label keeps the signal even when baselines would suppress."""
    from yoku.db.mongo import signals_collection
    from yoku.proactive.baselines import compute_baselines
    from yoku.proactive.judge import judge_signals
    from yoku.proactive.signals import label_signal, run_detectors

    _seed_person("u_pm", "Pia Manager")
    _seed_done_tickets("Pia Manager", with_pr=0, without_pr=9, key_prefix="PM")
    compute_baselines(now=_NOW)  # 100% rate -> baseline tier would suppress
    run_detectors()

    confirmed = signals_collection().find_one({"item_key": "jira/PM-N0"})
    label_signal(confirmed["signal_id"], "confirmed", "u_admin")

    counts = judge_signals(now=_NOW, llm=lambda k, p: {}, budget=0)
    assert counts["kept"] == 1  # the confirmed one
    s = signals_collection().find_one({"item_key": "jira/PM-N0"})
    assert s["status"] == "open"
    assert s["verdict"]["real"] is True and s["verdict"]["confirmed_by"] == "u_admin"
    # The rest of Pia's signals are still baseline-suppressed as usual.
    assert counts["suppressed_baseline"] == 8


@pytest.mark.integration
def test_repeated_dismissals_suppress_pattern_via_memory(tenant):
    from yoku.db.mongo import signals_collection
    from yoku.proactive.judge import judge_signals
    from yoku.proactive.signals import label_signal, run_detectors

    _seed_person("u_eng", "Evan Engineer")
    _seed_done_tickets("Evan Engineer", with_pr=0, without_pr=3, key_prefix="ENG")
    run_detectors()

    # A human dismisses two of Evan's done_no_pr signals → memory threshold met.
    sigs = list(signals_collection().find({"detector": "done_no_pr"}).limit(2))
    for s in sigs:
        label_signal(s["signal_id"], "dismissed", "u_admin")

    def stub(kind: str, prompt: str) -> dict:  # pragma: no cover — must not run
        raise AssertionError("LLM tier must not be reached when memory suppresses")

    counts = judge_signals(now=_NOW, llm=stub, budget=50)
    assert counts["suppressed_memory"] == 1  # the remaining undismissed signal
    third = signals_collection().find_one({"status": "judged"})
    assert third["verdict"]["suppressed_by"] == "memory"


@pytest.mark.integration
def test_and_gate_rejects_when_person_says_normal(tenant):
    from yoku.db.mongo import signals_collection
    from yoku.proactive.judge import judge_signals
    from yoku.proactive.signals import run_detectors

    _seed_person("u_eng", "Evan Engineer")
    _seed_done_tickets("Evan Engineer", with_pr=0, without_pr=1, key_prefix="ENG")
    run_detectors()

    def stub(kind: str, prompt: str) -> dict:
        if kind == "item":
            return {"real": True, "reason": "engineering work"}
        return {"normal_for_person": True, "reason": "they always close without PRs"}

    counts = judge_signals(now=_NOW, llm=stub, budget=50)
    assert counts["rejected"] == 1 and counts["kept"] == 0
    s = signals_collection().find_one({})
    assert s["status"] == "judged" and s["verdict"]["real"] is False
    assert s["verdict"]["person"]["normal_for_person"] is True


@pytest.mark.integration
def test_budget_defers_and_drains_next_run(tenant):
    from yoku.proactive.judge import judge_signals
    from yoku.proactive.signals import run_detectors

    _seed_person("u_eng", "Evan Engineer")
    _seed_done_tickets("Evan Engineer", with_pr=0, without_pr=3, key_prefix="ENG")
    run_detectors()

    def stub(kind: str, prompt: str) -> dict:
        return (
            {"real": False, "reason": "non-code"}
            if kind == "item"
            else {"normal_for_person": False, "reason": ""}
        )

    first = judge_signals(now=_NOW, llm=stub, budget=1)
    assert first["rejected"] == 1 and first["deferred"] == 2
    second = judge_signals(now=_NOW, llm=stub, budget=5)
    assert second["candidates"] == 2 and second["deferred"] == 0


@pytest.mark.integration
def test_judged_signal_resolves_when_gap_heals(tenant):
    from yoku.db.mongo import dc_jira_collection, signals_collection
    from yoku.proactive.judge import judge_signals
    from yoku.proactive.signals import run_detectors

    _seed_person("u_eng", "Evan Engineer")
    _seed_done_tickets("Evan Engineer", with_pr=0, without_pr=1, key_prefix="ENG")
    run_detectors()
    judge_signals(
        now=_NOW,
        llm=lambda k, p: (
            {"real": False, "reason": "x"} if k == "item" else {"normal_for_person": False}
        ),
        budget=5,
    )
    assert signals_collection().find_one({})["status"] == "judged"

    # A PR links up — the gap heals; the rejected signal records its ending.
    dc_jira_collection().update_one({"key": "ENG-N0"}, {"$set": {"linked_prs": [{"key": "o/r#9"}]}})
    run_detectors()
    s = signals_collection().find_one({})
    assert s["status"] == "resolved" and s["resolution"] == "self_healed"
