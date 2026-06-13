"""The speak-first loop (docs/yoku_agent.md Phase 5) — yoku opens its mouth.

Runs after the judge each sync. For every judged-real, matured signal it
walks the escalating filters — every stage a chance to STOP:

    conversation already exists for this gap?            → skip (dedupe)
    person routable to a Slack identity?                 → no: stay in Inbox
    quiet hours / per-person daily cap?                  → defer to next sync
    compose ONE clear ask, citing the key
    all three send keys present?                         → DM, else SHADOW

Send keys: `settings.proactive_speak_enabled` (global kill switch) AND the
tenant's `proactive_send_enabled` Slack-config flag AND a stored bot token.
Default tenant state is shadow: the drafted DM lands on the signal
(`proposed_message`, status `shadow`) and in `conversations`, reviewable in
the Inbox. Flip the tenant flag only once shadow output earns trust.

Messages are deterministic templates — a teammate's nudge, not a report:
short, specific, one question, citing the key. LLM phrasing can come later;
predictability matters more while trust is being earned.

`process_inbound_replies` closes the loop: verified DM replies captured by
M6 (`slack_inbound`) are threaded onto the person's awaiting conversation.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

from pymongo.errors import DuplicateKeyError

from yoku.config import settings
from yoku.db.mongo import (
    conversations_collection,
    signals_collection,
    slack_inbound_collection,
)
from yoku.logging import get_logger
from yoku.proactive.routing import route_target

log = get_logger("orchestrator")


def compose_message(signal: dict, target: dict) -> str:
    """One short, specific ask, citing the key — each Detector owns its
    template; signals from a since-removed detector get the generic nudge."""
    from yoku.proactive.detectors import discover_detectors

    spec = next((d for d in discover_detectors() if d.name == signal["detector"]), None)
    if spec is not None and spec.compose is not None:
        return spec.compose(signal, target)
    key = signal["item_key"].split("/", 1)[-1]
    return (
        f"Hey {target['first_name']} — I noticed a gap on {key} "
        f"({signal['detector']}). Can you take a look?"
    )


def _in_quiet_hours(now: datetime) -> bool:
    spec = settings.proactive_quiet_utc
    if not spec:
        return False
    try:
        start_s, end_s = spec.split("-", 1)
        start, end = int(start_s), int(end_s)
    except ValueError:
        log.warning("bad proactive_quiet_utc %r — ignoring", spec)
        return False
    h = now.hour
    if start <= end:
        return start <= h < end
    return h >= start or h < end  # window wraps midnight


def _sends_today(person_user_id: str, now: datetime) -> int:
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return conversations_collection().count_documents(
        {
            "person_user_id": person_user_id,
            "state": {"$ne": "shadow"},  # only real DMs count against the cap
            "opened_at": {"$gte": day_start},
        }
    )


def _tenant_send_enabled() -> tuple[bool, dict | None]:
    """(can send, decrypted slack config) — all three send keys checked."""
    if not settings.proactive_speak_enabled:
        return False, None
    from yoku.db import connector_configs as cc

    decrypted = cc.get_config_decrypted("slack") or {}
    cfg = (cc.get_config("slack") or {}).get("config") or {}
    if not cfg.get("proactive_send_enabled") or not decrypted.get("bot_token"):
        return False, None
    return True, decrypted


def run_proactive_loop(now: datetime | None = None) -> dict[str, int]:
    """Walk judged-real signals through the filters; shadow or send."""
    now = now or datetime.now(UTC)
    t0 = time.monotonic()
    signals = signals_collection()
    convos = conversations_collection()

    eligible = list(
        signals.find({"status": "open", "matured_at": {"$ne": None}, "verdict.real": True}).sort(
            [("matured_at", 1)]
        )
    )
    counts = {
        "eligible": len(eligible),
        "shadowed": 0,
        "sent": 0,
        "skipped_no_target": 0,
        "deferred": 0,
        "errors": 0,
    }
    can_send, slack_cfg = _tenant_send_enabled()
    quiet = _in_quiet_hours(now)

    for s in eligible:
        if convos.find_one({"signal_id": s["signal_id"]}, {"_id": 1}):
            continue  # never two threads on one gap

        target = route_target(s)
        if target is None:
            counts["skipped_no_target"] += 1
            continue  # stays open in the Inbox for humans

        message = compose_message(s, target)
        sending = can_send
        if sending and (
            quiet or _sends_today(target["user_id"], now) >= settings.proactive_daily_dm_cap
        ):
            counts["deferred"] += 1
            continue  # try again next sync — drafts don't jump the guardrails

        convo = {
            "conversation_id": uuid.uuid4().hex,
            "signal_id": s["signal_id"],
            "detector": s["detector"],
            "item_key": s["item_key"],
            "person_user_id": target["user_id"],
            "slack_user_id": target["slack_user_id"],
            "message": message,
            "state": "shadow",
            "slack_channel": None,
            "slack_ts": None,
            "replies": [],
            "opened_at": now,
            "last_message_at": now,
        }

        if sending:
            try:
                from yoku.connectors._runtime import slack_config_from_dict, use_slack
                from yoku.connectors.slack.client import open_dm, post_message

                with use_slack(slack_config_from_dict(slack_cfg)):
                    channel = open_dm(target["slack_user_id"])
                    posted = post_message(channel, message)
                convo.update(
                    state="awaiting_reply",
                    slack_channel=posted.get("channel"),
                    slack_ts=posted.get("ts"),
                )
                signals.update_one(
                    {"_id": s["_id"]},
                    {"$set": {"status": "sent", "proposed_message": message}},
                )
                counts["sent"] += 1
                log.info("DM sent for %s to %s", s["item_key"], target["name"])
            except Exception:
                log.exception("send failed for %s — falling back to shadow", s["item_key"])
                counts["errors"] += 1
                signals.update_one(
                    {"_id": s["_id"]},
                    {"$set": {"status": "shadow", "proposed_message": message}},
                )
                counts["shadowed"] += 1
        else:
            signals.update_one(
                {"_id": s["_id"]},
                {"$set": {"status": "shadow", "proposed_message": message}},
            )
            counts["shadowed"] += 1

        try:
            convos.insert_one(convo)
            # Episodic memory: yoku just spoke (really or in shadow). Recorded
            # only when this run actually opened the conversation, so a
            # concurrent run losing the insert race below doesn't double-count.
            from yoku.proactive.episodes import (
                KIND_SENT,
                SOURCE_ENGINE,
                SOURCE_SLACK,
                record_episode,
            )

            real = convo["state"] == "awaiting_reply"
            record_episode(
                kind=KIND_SENT,
                text=message,
                person_user_id=target["user_id"],
                signal_id=s["signal_id"],
                item_key=s["item_key"],
                detector=s["detector"],
                source=SOURCE_SLACK if real else SOURCE_ENGINE,
                outcome="sent" if real else "shadow",
                now=now,
            )
        except DuplicateKeyError:
            # A concurrent run (scheduler + on-demand sync) won the race for
            # this gap — the unique signal_id index is the real dedupe; the
            # other run's conversation stands.
            log.info("conversation for %s already opened by a concurrent run", s["signal_id"])

    replies = process_inbound_replies(now)
    counts["replies_threaded"] = replies
    log.info("proactive loop %s elapsed=%.1fs", counts, time.monotonic() - t0)
    return counts


# Conservative affirmatives for flag-gated DM approval — a reply must BE one
# of these (modulo punctuation/emoji), not merely contain one.
_AFFIRMATIVE = {"yes", "yep", "yeah", "go ahead", "do it", "approved", "close it", "link it", "ok"}


def _is_affirmative(text: str) -> bool:
    cleaned = "".join(c for c in (text or "").lower() if c.isalnum() or c.isspace()).strip()
    return cleaned in _AFFIRMATIVE


def _maybe_approve_pending_action(convo: dict, reply_text: str) -> None:
    """Phase 6, flag-gated: an affirmative reply from the person yoku asked
    approves THAT gap's single pending action. Anything ambiguous → web UI."""
    if not settings.dm_approval_enabled or not _is_affirmative(reply_text):
        return
    from yoku.actions import executor
    from yoku.db.mongo import action_log_collection

    pending = list(
        action_log_collection()
        .find({"status": "proposed", "signal_id": convo.get("signal_id")})
        .limit(2)
    )
    if len(pending) != 1:
        return  # zero or ambiguous — never guess which action a "yes" means
    try:
        executor.execute(pending[0]["action_id"], approved_by=f"dm:{convo['person_user_id']}")
        log.info("DM reply approved action %s", pending[0]["action_id"])
    except Exception:
        log.exception("DM-approved action failed (audit row records it)")


# A commitment with no explicit due date is considered lapsed this many days
# after it was made; each commitment is chased at most this many times.
COMMITMENT_GRACE_DAYS = 2
MAX_FOLLOWUPS = 1
# Per-run cursor caps so a backlog can't fan out unbounded work in one sync.
_FOLLOWUP_BUDGET_PER_RUN = 50
_INBOUND_BUDGET_PER_RUN = 200


def _commitment_due(commitment: dict, now: datetime) -> bool:
    """True when a promised fix is past its (explicit or implied) deadline."""
    from datetime import date

    due = commitment.get("due")
    if due:
        try:
            return date.fromisoformat(due) <= now.date()
        except (ValueError, TypeError):
            pass
    made = commitment.get("made_at")
    if made is not None:
        if made.tzinfo is None:
            made = made.replace(tzinfo=UTC)
        return (now - made) >= timedelta(days=COMMITMENT_GRACE_DAYS)
    return False


def compose_followup(signal: dict, commitment: dict, target: dict) -> str:
    """A gentle, specific chase that remembers what they promised."""
    key = signal["item_key"].split("/", 1)[-1]
    what = (commitment.get("text") or "").strip()
    tail = f" you'd {what}" if what else " you'd sort this"
    return (
        f"Hey {target['first_name']} — earlier{tail} on {key}. "
        f"Still on track, or want me to take it off your plate?"
    )


def run_commitment_followups(now: datetime | None = None) -> dict[str, int]:
    """Chase commitments whose deadline has passed while the gap is still live.

    A promise made in a reply (Phase B) becomes `signal.commitment`; if the gap
    has not healed by the due date this nudges once more — yoku remembering what
    you said you'd do. Same send keys, quiet hours and shadow default as the
    speak loop; bounded by MAX_FOLLOWUPS so it never becomes nagging.
    """
    now = now or datetime.now(UTC)
    if not settings.commitment_followups_enabled:
        return {"due": 0, "sent": 0, "shadowed": 0, "disabled": True}

    signals = signals_collection()
    convos = conversations_collection()
    counts = {
        "due": 0,
        "sent": 0,
        "shadowed": 0,
        "skipped_no_target": 0,
        "deferred": 0,
        "errors": 0,
    }
    can_send, slack_cfg = _tenant_send_enabled()
    quiet = _in_quiet_hours(now)

    # Only signals actually DM'd (status "sent") can carry a real promise — a
    # commitment comes from a Slack reply, which only threads onto a delivered
    # nudge. Shadow-mode drafts were never seen by the person, so never chased.
    candidates = signals.find({"status": "sent", "commitment": {"$ne": None}}).limit(
        _FOLLOWUP_BUDGET_PER_RUN
    )
    for s in candidates:
        commitment = s.get("commitment") or {}
        if commitment.get("followups", 0) >= MAX_FOLLOWUPS:
            continue
        if not _commitment_due(commitment, now):
            continue
        counts["due"] += 1

        target = route_target(s)
        if target is None:
            counts["skipped_no_target"] += 1
            continue

        message = compose_followup(s, commitment, target)
        # Respect the same guardrails as the speak loop: never exceed the
        # per-person daily DM cap, and stay out of quiet hours.
        sending = (
            can_send
            and not quiet
            and _sends_today(target["user_id"], now) < settings.proactive_daily_dm_cap
        )
        state = "sent"
        if sending:
            try:
                from yoku.connectors._runtime import slack_config_from_dict, use_slack
                from yoku.connectors.slack.client import open_dm, post_message

                with use_slack(slack_config_from_dict(slack_cfg)):
                    channel = open_dm(target["slack_user_id"])
                    post_message(channel, message)
                counts["sent"] += 1
            except Exception:
                log.exception("followup send failed for %s — shadowing", s["item_key"])
                counts["errors"] += 1
                state = "shadow"
                counts["shadowed"] += 1
        else:
            state = "shadow"
            counts["shadowed"] += 1

        signals.update_one(
            {"_id": s["_id"]},
            {
                "$set": {
                    "commitment.followups": commitment.get("followups", 0) + 1,
                    "commitment.last_followup_at": now,
                }
            },
        )
        convos.update_one(
            {"signal_id": s["signal_id"]},
            {
                "$push": {"followups": {"message": message, "ts": now, "state": state}},
                "$set": {"last_message_at": now},
            },
        )
        from yoku.proactive.episodes import (
            KIND_FOLLOWUP,
            SOURCE_ENGINE,
            SOURCE_SLACK,
            record_episode,
        )

        record_episode(
            kind=KIND_FOLLOWUP,
            text=message,
            person_user_id=target["user_id"],
            signal_id=s["signal_id"],
            item_key=s["item_key"],
            detector=s["detector"],
            source=SOURCE_SLACK if state == "sent" else SOURCE_ENGINE,
            outcome=state,
            now=now,
        )

    log.info("commitment followups %s", counts)
    return counts


def process_inbound_replies(now: datetime | None = None) -> int:
    """Thread verified inbound DMs onto the sender's awaiting conversation."""
    now = now or datetime.now(UTC)
    convos = conversations_collection()
    inbound = slack_inbound_collection()
    threaded = 0

    for row in (
        inbound.find({"processed": False, "event_type": "dm"})
        .sort("received_at", 1)
        .limit(_INBOUND_BUDGET_PER_RUN)
    ):
        convo = convos.find_one(
            {"slack_user_id": row.get("slack_user_id"), "state": "awaiting_reply"},
            sort=[("opened_at", -1)],
        )
        if convo is not None:
            convos.update_one(
                {"_id": convo["_id"]},
                {
                    "$push": {
                        "replies": {
                            "text": row.get("text"),
                            "ts": row.get("ts"),
                            "received_at": row.get("received_at"),
                        }
                    },
                    "$set": {"state": "replied", "last_message_at": now},
                },
            )
            threaded += 1
            # Episodic memory: the person answered. Phase B will parse this text
            # into an outcome; Phase A just captures it verbatim.
            from yoku.proactive.episodes import KIND_REPLY, SOURCE_SLACK, record_episode

            record_episode(
                kind=KIND_REPLY,
                text=row.get("text") or "",
                person_user_id=convo.get("person_user_id"),
                signal_id=convo.get("signal_id"),
                item_key=convo.get("item_key"),
                detector=convo.get("detector"),
                source=SOURCE_SLACK,
                now=now,
            )
            _maybe_approve_pending_action(convo, row.get("text") or "")
        # Mark processed either way: a DM with no awaiting conversation is
        # ordinary chat with the bot, not a reply — Phase 6 may route those
        # into the reactive agent.
        inbound.update_one({"_id": row["_id"]}, {"$set": {"processed": True}})

    if threaded:
        log.info("threaded %d inbound replies", threaded)
    return threaded
