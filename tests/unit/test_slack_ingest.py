"""Unit tests for the Slack connector client + ingest helpers.

Hermetic — no network calls, no mongo. All Slack API responses are stubbed.
"""

from __future__ import annotations

import pytest

from yoku.connectors._runtime import slack_config_from_dict, use_slack
from yoku.connectors.slack.client import message_to_doc

_USER_MAP = {
    "U001": {"display_name": "Alice Chen", "email": "alice@co.ai"},
    "U002": {"display_name": "Bob Smith", "email": "bob@co.ai"},
}


def _make_msg(**kwargs) -> dict:
    base = {
        "type": "message",
        "user": "U001",
        "text": "hello world",
        "ts": "1710000000.123456",
    }
    return {**base, **kwargs}


@pytest.mark.unit
def test_basic_fields():
    doc = message_to_doc(_make_msg(), "C123", "engineering", _USER_MAP, "acme")
    assert doc["key"] == "C123/1710000000.123456"
    assert doc["channel_id"] == "C123"
    assert doc["channel_name"] == "engineering"
    assert doc["author_id"] == "U001"
    assert doc["author_name"] == "Alice Chen"
    assert doc["author_email"] == "alice@co.ai"
    assert doc["source"] == "slack"


@pytest.mark.unit
def test_embed_text_includes_channel_and_author():
    doc = message_to_doc(
        _make_msg(text="the auth service is broken"), "C123", "engineering", _USER_MAP, "acme"
    )
    assert doc["text"] == "[#engineering] Alice Chen: the auth service is broken"


@pytest.mark.unit
def test_summary_truncated_at_200_chars():
    long_text = "x" * 300
    doc = message_to_doc(_make_msg(text=long_text), "C123", "general", _USER_MAP, "acme")
    assert len(doc["summary"]) == 200
    assert doc["summary"] == "x" * 200


@pytest.mark.unit
def test_jira_keys_extracted():
    doc = message_to_doc(
        _make_msg(text="fixing AS-1234 and AS-5678 before the release"),
        "C123",
        "engineering",
        _USER_MAP,
        "acme",
    )
    assert set(doc["jira_keys"]) == {"AS-1234", "AS-5678"}


@pytest.mark.unit
def test_no_jira_keys_when_none_present():
    doc = message_to_doc(_make_msg(text="let's ship it"), "C123", "general", _USER_MAP, "acme")
    assert doc["jira_keys"] == []


@pytest.mark.unit
def test_thread_reply_flag_set_when_reply():
    doc = message_to_doc(
        _make_msg(ts="1710000001.000000", thread_ts="1710000000.000000"),
        "C123",
        "eng",
        _USER_MAP,
        "acme",
    )
    assert doc["is_thread_reply"] is True
    assert doc["thread_ts"] == "1710000000.000000"


@pytest.mark.unit
def test_top_level_message_not_marked_as_reply():
    doc = message_to_doc(
        _make_msg(ts="1710000000.000000", thread_ts="1710000000.000000"),
        "C123",
        "eng",
        _USER_MAP,
        "acme",
    )
    assert doc["is_thread_reply"] is False


@pytest.mark.unit
def test_url_format():
    doc = message_to_doc(_make_msg(ts="1710000000.123456"), "C123", "eng", _USER_MAP, "acme")
    assert doc["url"] == "https://acme.slack.com/archives/C123/p1710000000123456"


@pytest.mark.unit
def test_unknown_user_falls_back_to_user_id():
    doc = message_to_doc(_make_msg(user="U999"), "C123", "eng", _USER_MAP, "acme")
    assert doc["author_name"] == "U999"
    assert doc["author_email"] is None


@pytest.mark.unit
def test_reactions_count_summed():
    msg = _make_msg(reactions=[{"name": "thumbsup", "count": 3}, {"name": "fire", "count": 2}])
    doc = message_to_doc(msg, "C123", "eng", _USER_MAP, "acme")
    assert doc["reactions_count"] == 5


@pytest.mark.unit
def test_slack_config_from_dict_defaults():
    cfg = slack_config_from_dict({"bot_token": "xoxb-test", "workspace": "acme"})
    assert cfg.bot_token == "xoxb-test"
    assert cfg.workspace == "acme"
    assert cfg.lookback_days == 90
    assert cfg.channel_types == "public_channel"


@pytest.mark.unit
def test_slack_config_from_dict_overrides():
    cfg = slack_config_from_dict(
        {
            "bot_token": "xoxb-x",
            "workspace": "myco",
            "lookback_days": "30",
            "channel_types": "public_channel,private_channel",
        }
    )
    assert cfg.lookback_days == 30
    assert cfg.channel_types == "public_channel,private_channel"


@pytest.mark.unit
def test_current_slack_config_raises_when_unbound():
    from yoku.connectors._runtime import current_slack_config

    with pytest.raises(RuntimeError, match="no Slack config bound"):
        current_slack_config()


@pytest.mark.unit
def test_use_slack_binds_and_unbinds():
    from yoku.connectors._runtime import current_slack_config

    cfg = slack_config_from_dict({"bot_token": "xoxb-test", "workspace": "acme"})
    with use_slack(cfg):
        bound = current_slack_config()
        assert bound.bot_token == "xoxb-test"
    with pytest.raises(RuntimeError):
        current_slack_config()
