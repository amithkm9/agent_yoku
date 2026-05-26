"""Streamlit UI for the agent_yoku deepagent.

Conversation state is persisted in mongo, keyed by session_id (URL query param)
and turn_id. New Session button generates a fresh session_id.

Storage policy:
- Full agent trace (incl. tool calls) -> mongo, used for UI replay.
- Compact slice (user + final-AI per prior turn) -> agent input on follow-ups.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import sessions
import streamlit as st
from langchain_core.messages import HumanMessage

from agent_yoku.agent.agent import build_agent
from agent_yoku.config import AGENT_MODEL_ID, github_prs_collection, tickets_collection
from agent_yoku.log import get_logger, set_session

log = get_logger("app")

st.set_page_config(page_title="agent_yoku — agent", page_icon="🤖", layout="wide")


# =================== Session bootstrap (URL query param) ===================


def _get_or_create_session_id() -> str:
    sid = st.query_params.get("session")
    if sid:
        return sid
    sid = sessions.start_session()
    st.query_params["session"] = sid
    return sid


def _start_new_session() -> None:
    sid = sessions.start_session()
    st.query_params["session"] = sid
    st.session_state["turns_cache_for"] = None
    st.rerun()


session_id = _get_or_create_session_id()
set_session(session_id)


# =================== Cached resources ===================


@st.cache_resource(show_spinner="Building deep agent…")
def _agent():
    return build_agent()


@st.cache_data(ttl=60, show_spinner=False)
def _counts():
    jira = tickets_collection().count_documents({"embedding": {"$ne": None}})
    gh = github_prs_collection().count_documents({"embedding": {"$ne": None}})
    return jira, gh


agent = _agent()
jira_n, gh_n = _counts()


# =================== Sidebar ===================


def _relative_time(dt) -> str:
    if not dt:
        return "—"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    days = secs // 86400
    if days < 30:
        return f"{days}d ago"
    return dt.strftime("%b %d")


def _switch_to(sid: str) -> None:
    log.info("session switch -> %s", sid)
    st.query_params["session"] = sid
    st.session_state["turns_cache_for"] = None
    st.rerun()


def _delete_session(sid: str) -> None:
    log.info("session delete -> %s", sid)
    sessions.delete_session(sid)
    st.rerun()


with st.sidebar:
    st.title("agent_yoku")
    st.caption(f"`{AGENT_MODEL_ID}`")

    if st.button("➕  New session", type="primary", use_container_width=True):
        _start_new_session()

    st.divider()

    cs1, cs2 = st.columns(2)
    cs1.metric("JIRA", jira_n)
    cs2.metric("GH PRs", gh_n)

    st.divider()

    st.subheader("Sessions")
    sess_list = sessions.list_sessions(limit=25)
    # pin current session to the top
    sess_list.sort(key=lambda s: 0 if s["session_id"] == session_id else 1)

    if not sess_list:
        st.caption("_no sessions yet_")

    for s in sess_list:
        sid = s["session_id"]
        active = sid == session_id
        title = (s.get("title") or "Untitled chat").strip() or "Untitled chat"
        rel = _relative_time(s.get("last_active_at"))
        turns = s.get("turn_count", 0)
        meta = f"{rel} · {turns} turn{'s' if turns != 1 else ''}"

        with st.container(border=active):
            if active:
                st.markdown(f"🔵 **{title[:60]}**")
                st.caption(f"{meta} · current")
            else:
                # Full-width switch button stacked above a small meta+delete row.
                if st.button(
                    f"💬  {title[:54]}",
                    key=f"sw_{sid}",
                    use_container_width=True,
                ):
                    _switch_to(sid)
                meta_col, del_col = st.columns([5, 2])
                meta_col.caption(meta)
                if del_col.button("🗑 delete", key=f"del_{sid}"):
                    _delete_session(sid)

    st.divider()
    st.markdown("**Sub-agents**")
    st.markdown("- `jira_researcher` — JIRA-only deep dives")
    st.markdown("- `github_researcher` — PR-only deep dives")


# =================== Rendering helpers ===================


def _extract_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    parts.append(block["text"])
                elif "content" in block:
                    parts.append(_extract_text(block["content"]))
            elif isinstance(block, str):
                parts.append(block)
        return "\n\n".join(p for p in parts if p)
    return str(content)


def _truncate(s: str, n: int = 600) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"\n…[truncated {len(s) - n} chars]"


def _short_args(args: dict) -> str:
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        sv = repr(v) if not isinstance(v, str) else f"'{v[:40]}'"
        parts.append(f"{k}={sv}")
    return ", ".join(parts)[:120]


_STATUS_MARK = {"completed": "✅", "in_progress": "⏳", "pending": "◻️"}


def _render_live_msg(msg, trace, answer_ph, pending: dict) -> None:
    """Render a LangChain BaseMessage during live streaming."""
    if msg.type in ("human", "system"):
        return

    if msg.type == "ai":
        calls = getattr(msg, "tool_calls", None) or []
        for c in calls:
            pending[c["id"]] = {"name": c["name"], "args": c.get("args", {})}
            name = c["name"]
            args = c.get("args", {})
            if name == "write_todos":
                with trace, st.expander("📋 Plan", expanded=True):
                    for t in args.get("todos", []):
                        mark = _STATUS_MARK.get(t.get("status"), "·")
                        st.markdown(f"{mark} {t.get('content', '')}")
            elif name == "task":
                sub = args.get("subagent_type") or "subagent"
                with trace:
                    st.markdown(f"🛰️ delegating to **`{sub}`**…")
        if not calls:
            text = _extract_text(msg.content)
            if text.strip():
                answer_ph.markdown(text)

    elif msg.type == "tool":
        call = pending.pop(getattr(msg, "tool_call_id", None), None)
        name = call["name"] if call else getattr(msg, "name", "tool")
        args = call.get("args", {}) if call else {}
        raw = msg.content
        content = raw if isinstance(raw, str) else json.dumps(raw, default=str, indent=2)

        if name == "write_todos":
            return

        with trace:
            if name == "task":
                sub = args.get("subagent_type") or "subagent"
                with st.expander(f"🛰️  ← `{sub}` result", expanded=False):
                    st.code(_truncate(content, 2000), language="markdown")
            else:
                with st.expander(f"🔧 {name}({_short_args(args)})", expanded=False):
                    if args:
                        st.json(args)
                    st.code(_truncate(content), language="json")


def _render_persisted_msg(doc: dict, trace, answer_ph, pending: dict) -> None:
    """Render a serialized mongo doc (replay path)."""
    role = doc.get("role")
    if role in ("human", "system"):
        return
    if role == "ai":
        calls = doc.get("tool_calls") or []
        for c in calls:
            pending[c["id"]] = {"name": c["name"], "args": c.get("args", {})}
            name = c["name"]
            args = c.get("args", {})
            if name == "write_todos":
                with trace, st.expander("📋 Plan", expanded=False):
                    for t in args.get("todos", []):
                        mark = _STATUS_MARK.get(t.get("status"), "·")
                        st.markdown(f"{mark} {t.get('content', '')}")
            elif name == "task":
                sub = args.get("subagent_type") or "subagent"
                with trace:
                    st.markdown(f"🛰️ delegated to **`{sub}`**")
        if not calls:
            text = _extract_text(doc.get("content"))
            if text.strip():
                answer_ph.markdown(text)
    elif role == "tool":
        call = pending.pop(doc.get("tool_call_id"), None)
        name = call["name"] if call else doc.get("name", "tool")
        args = call.get("args", {}) if call else {}
        raw = doc.get("content")
        content = raw if isinstance(raw, str) else json.dumps(raw, default=str, indent=2)
        if name == "write_todos":
            return
        with trace:
            if name == "task":
                sub = args.get("subagent_type") or "subagent"
                with st.expander(f"🛰️  ← `{sub}` result", expanded=False):
                    st.code(_truncate(content, 2000), language="markdown")
            else:
                with st.expander(f"🔧 {name}({_short_args(args)})", expanded=False):
                    if args:
                        st.json(args)
                    st.code(_truncate(content), language="json")


# =================== Replay session history from mongo ===================

if st.session_state.get("turns_cache_for") != session_id:
    docs = sessions.load_all_messages(session_id)
    # Group docs by turn_seq for chat-bubble structure.
    grouped: dict[int, list[dict]] = {}
    for d in docs:
        grouped.setdefault(d["turn_seq"], []).append(d)
    st.session_state["turns_cache"] = [grouped[k] for k in sorted(grouped)]
    st.session_state["turns_cache_for"] = session_id

for turn_docs in st.session_state["turns_cache"]:
    user_doc = next((d for d in turn_docs if d["role"] == "human"), None)
    if user_doc:
        with st.chat_message("user"):
            st.markdown(_extract_text(user_doc.get("content")))
    with st.chat_message("assistant"):
        pending: dict = {}
        trace = st.container()
        answer_ph = st.empty()
        for d in turn_docs:
            _render_persisted_msg(d, trace, answer_ph, pending)


# =================== New turn ===================


def _run_streaming(prior: list, query: str) -> list:
    """Stream a turn. Returns ALL messages produced this turn (excluding prior)."""
    pending: dict = {}
    trace = st.container()
    answer_ph = st.empty()
    status = st.status("Agent planning + drilling…", expanded=False)

    baseline = len(prior) + 1  # prior + the new HumanMessage
    seen = baseline
    final_messages: list = []

    try:
        for state in agent.stream(
            {"messages": prior + [HumanMessage(content=query)]},
            stream_mode="values",
        ):
            messages = state.get("messages", [])
            for msg in messages[seen:]:
                _render_live_msg(msg, trace, answer_ph, pending)
            seen = len(messages)
            final_messages = messages
        status.update(
            label=f"Done · {len(final_messages) - baseline + 1} new messages", state="complete"
        )
    except Exception as e:
        status.update(label=f"Error: {e}", state="error")
        raise

    # New-this-turn slice: from the HumanMessage onward.
    return final_messages[len(prior) :]


query = st.chat_input("Ask about JIRA tickets or GitHub PRs…")
if query:
    log.info("ui submit session=%s query=%r", session_id, query)
    with st.chat_message("user"):
        st.markdown(query)

    prior = sessions.load_agent_history(session_id)
    with st.chat_message("assistant"):
        turn_messages = _run_streaming(prior, query)

    turn_id = sessions.new_turn_id()
    sessions.save_turn(session_id, turn_id, turn_messages, user_query=query)
    st.session_state["turns_cache_for"] = None  # force replay refresh on next rerun
