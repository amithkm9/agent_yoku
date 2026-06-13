"""People API — the "What yoku knows" surface.

Makes the memory engine visible: a list of everyone yoku has accumulated memory
about, and a per-person view of the beliefs it has formed, the commitments they
owe, and the full interaction timeline. Reads the same engine collections as the
agent's `recall_history` tool (beliefs, episodes, signals) — all tenant-scoped,
all off the agent's mongo_query whitelist.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from yoku.auth import CurrentUser
from yoku.db.mongo import (
    beliefs_collection,
    ds_unified_users_collection,
    episodes_collection,
    signals_collection,
)
from yoku.proactive.beliefs import get_beliefs
from yoku.proactive.episodes import get_episodes
from yoku.schemas.api import (
    BeliefOut,
    CommitmentOut,
    EpisodeOut,
    PeopleResponse,
    PersonMemoryOut,
    PersonSummary,
)

router = APIRouter(prefix="/people", tags=["people"])

_LIVE = ("open", "sent", "shadow")


def _display_name(doc: dict | None, fallback: str) -> str:
    if not doc:
        return fallback
    return (
        (doc.get("jira") or {}).get("displayName")
        or (doc.get("github") or {}).get("name")
        or (doc.get("github") or {}).get("login")
        or (doc.get("slack") or {}).get("display_name")
        or doc.get("email")
        or fallback
    )


def _commitments_for(user_id: str) -> list[CommitmentOut]:
    rows = signals_collection().find(
        {"person_user_id": user_id, "commitment": {"$ne": None}},
        {"_id": 0, "item_key": 1, "detector": 1, "status": 1, "commitment": 1},
    )
    out: list[CommitmentOut] = []
    for s in rows:
        c = s.get("commitment") or {}
        out.append(
            CommitmentOut(
                item_key=s.get("item_key"),
                detector=s.get("detector"),
                status=s.get("status"),
                text=c.get("text"),
                due=c.get("due"),
                made_at=c.get("made_at"),
                followups=c.get("followups"),
            )
        )
    return out


@router.get("", response_model=PeopleResponse)
async def list_people(_: CurrentUser) -> PeopleResponse:
    """Everyone yoku has memory about — anyone with a belief, an episode, or a
    commitment — most recently active first."""
    episodes = episodes_collection()
    signals = signals_collection()

    user_ids: set[str] = set()
    user_ids.update(episodes.distinct("person_user_id", {"person_user_id": {"$ne": None}}))
    user_ids.update(beliefs_collection().distinct("user_id"))
    user_ids.update(
        signals.distinct(
            "person_user_id", {"commitment": {"$ne": None}, "person_user_id": {"$ne": None}}
        )
    )

    users = ds_unified_users_collection()
    summaries: list[PersonSummary] = []
    for uid in user_ids:
        if not uid:
            continue
        doc = users.find_one({"user_id": uid}, {"_id": 0, "embedding": 0})
        last = list(
            episodes.find({"person_user_id": uid}, {"_id": 0, "ts": 1}).sort([("ts", -1)]).limit(1)
        )
        summaries.append(
            PersonSummary(
                user_id=uid,
                name=_display_name(doc, uid),
                email=(doc or {}).get("email"),
                belief_count=beliefs_collection().count_documents({"user_id": uid}),
                open_commitments=signals.count_documents(
                    {"person_user_id": uid, "commitment": {"$ne": None}, "status": {"$in": _LIVE}}
                ),
                last_interaction_at=last[0]["ts"] if last else None,
            )
        )

    # Most recently active first; people with no episodes sink to the bottom.
    summaries.sort(
        key=lambda p: (p.last_interaction_at is not None, p.last_interaction_at), reverse=True
    )
    return PeopleResponse(people=summaries)


@router.get("/{user_id}", response_model=PersonMemoryOut)
async def get_person(user_id: str, _: CurrentUser) -> PersonMemoryOut:
    """One person's full memory: beliefs, commitments, and timeline."""
    doc = ds_unified_users_collection().find_one({"user_id": user_id}, {"_id": 0, "embedding": 0})
    beliefs = get_beliefs(user_id)
    episodes = get_episodes(person_user_id=user_id, limit=50)
    commitments = _commitments_for(user_id)

    if doc is None and not beliefs and not episodes and not commitments:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no memory for {user_id!r}")

    return PersonMemoryOut(
        user_id=user_id,
        name=_display_name(doc, user_id),
        email=(doc or {}).get("email"),
        beliefs=[
            BeliefOut(
                pattern=b["pattern"],
                claim=b["claim"],
                confidence=b["confidence"],
                evidence_count=b["evidence_count"],
            )
            for b in beliefs
        ],
        commitments=commitments,
        episodes=[
            EpisodeOut(
                kind=e.get("kind", ""),
                text=e.get("text"),
                detector=e.get("detector"),
                item_key=e.get("item_key"),
                outcome=(e.get("understanding") or {}).get("outcome") or e.get("outcome"),
                ts=e.get("ts"),
            )
            for e in episodes
        ],
    )
