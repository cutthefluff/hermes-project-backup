"""Tests that /new (and its /reset alias) clears session-scoped overrides."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.account_usage import AccountUsageSnapshot, AccountUsageWindow
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource, build_session_key


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(text=text, source=_make_source(), message_id="m1")


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner._pending_model_notes = {}
    runner._background_tasks = set()

    session_key = build_session_key(_make_source())
    session_entry = SessionEntry(
        session_key=session_key,
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store.reset_session.return_value = session_entry
    runner.session_store._entries = {session_key: session_entry}
    runner.session_store._generate_session_key.return_value = session_key
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._agent_cache_lock = None  # disables _evict_cached_agent lock path
    runner._is_user_authorized = lambda _source: True
    runner._format_session_info = lambda: ""
    runner._telegram_topic_new_header = lambda source: None
    runner._is_telegram_topic_lane = lambda source: False
    runner._record_telegram_topic_binding = MagicMock()
    runner._release_running_agent_state = MagicMock()
    runner._cleanup_agent_resources = MagicMock()
    runner._evict_cached_agent = MagicMock()
    runner._clear_session_boundary_security_state = MagicMock()
    def _clear_reasoning_override(session_key, reasoning_config):
        runner._session_reasoning_overrides.pop(session_key, None)
        return None

    runner._set_session_reasoning_override = _clear_reasoning_override

    return runner


@pytest.mark.asyncio
async def test_new_command_clears_session_model_override():
    """/new must remove the session-scoped model override for that session."""
    runner = _make_runner()
    session_key = build_session_key(_make_source())

    # Simulate a prior /model switch stored as a session override
    runner._session_model_overrides[session_key] = {
        "model": "gpt-4o",
        "provider": "openai",
        "api_key": "***",
        "base_url": "",
        "api_mode": "openai",
    }
    runner._session_reasoning_overrides[session_key] = {"enabled": True, "effort": "high"}
    runner._pending_model_notes[session_key] = "[Note: switched to gpt-4o.]"

    await runner._handle_reset_command(_make_event("/new"))

    assert session_key not in runner._session_model_overrides
    assert session_key not in runner._session_reasoning_overrides
    assert session_key not in runner._pending_model_notes


@pytest.mark.asyncio
async def test_new_command_no_override_is_noop():
    """/new with no prior model override must not raise."""
    runner = _make_runner()
    session_key = build_session_key(_make_source())

    assert session_key not in runner._session_model_overrides
    assert session_key not in runner._session_reasoning_overrides

    await runner._handle_reset_command(_make_event("/new"))

    assert session_key not in runner._session_model_overrides
    assert session_key not in runner._session_reasoning_overrides


@pytest.mark.asyncio
async def test_new_command_only_clears_own_session():
    """/new must only clear the override for the session that triggered it."""
    runner = _make_runner()
    session_key = build_session_key(_make_source())
    other_key = "other_session_key"

    runner._session_model_overrides[session_key] = {
        "model": "gpt-4o",
        "provider": "openai",
        "api_key": "sk-test",
        "base_url": "",
        "api_mode": "openai",
    }
    runner._session_model_overrides[other_key] = {
        "model": "claude-sonnet-4-6",
        "provider": "anthropic",
        "api_key": "***",
        "base_url": "",
        "api_mode": "anthropic",
    }
    runner._session_reasoning_overrides[session_key] = {"enabled": True, "effort": "high"}
    runner._session_reasoning_overrides[other_key] = {"enabled": True, "effort": "low"}
    runner._pending_model_notes[session_key] = "[Note: switched to gpt-4o.]"
    runner._pending_model_notes[other_key] = "[Note: switched to claude-sonnet-4-6.]"

    await runner._handle_reset_command(_make_event("/new"))

    assert session_key not in runner._session_model_overrides
    assert other_key in runner._session_model_overrides
    assert session_key not in runner._session_reasoning_overrides
    assert other_key in runner._session_reasoning_overrides
    assert session_key not in runner._pending_model_notes
    assert other_key in runner._pending_model_notes


@pytest.mark.asyncio
async def test_reset_reply_keeps_header_session_info_limits_and_tip(monkeypatch):
    """Codex usage limits augment the normal /reset info, they do not replace it."""
    runner = _make_runner()
    runner._format_session_info = lambda: "◆ Model: gpt-5.5\n◆ Provider: openai-codex\n◆ Context: 272K tokens (detected)"

    now = datetime.now(timezone.utc)
    snapshot = AccountUsageSnapshot(
        provider="openai-codex",
        source="test",
        fetched_at=now,
        windows=(
            AccountUsageWindow("session", used_percent=100.0, reset_at=now + timedelta(hours=2, minutes=32)),
            AccountUsageWindow("weekly", used_percent=20.0, reset_at=now + timedelta(days=6, hours=21)),
        ),
        free_resets_available=2,
    )

    monkeypatch.setattr("gateway.run._load_gateway_config", lambda: {"model": {"provider": "openai-codex"}})
    monkeypatch.setattr("gateway.slash_commands.fetch_account_usage", lambda provider: snapshot)
    monkeypatch.setattr(
        "hermes_cli.tips.get_random_tip",
        lambda: "GPT and Codex models get special system prompt guidance for tool discipline and mandatory tool use.",
    )

    reply = await runner._handle_reset_command(_make_event("/reset"))
    text = str(reply)

    assert text.startswith(
        "✨ Session reset! Starting fresh.\n\n"
        "◆ Model: gpt-5.5\n"
        "◆ Provider: openai-codex\n"
        "◆ Context: 272K tokens (detected)\n\n"
    )
    assert "Session: 0% left • resets" in text
    assert "Weekly: 80% left • resets" in text
    assert (
        "Free resets: 2\n✦ Tip: GPT and Codex models get special system prompt guidance "
        "for tool discipline and mandatory tool use."
    ) in text
