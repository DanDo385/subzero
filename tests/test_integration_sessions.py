"""Live session lifecycle: open once, update as you go, list, close."""

from __future__ import annotations

import uuid

import pytest

from conftest import requires_db
from subzero.auth.principal import Principal

pytestmark = requires_db


async def test_open_is_idempotent_per_client_id(services) -> None:
    first, created = await services.sessions.open_session(
        provider="claude", client_session_id="chat-1", title="First chat"
    )
    assert created is True
    second, created_again = await services.sessions.open_session(
        provider="claude", client_session_id="chat-1", title="First chat"
    )
    assert first == second and created_again is False


async def test_bad_client_ids_are_refused(services) -> None:
    from subzero.errors import ValidationError

    with pytest.raises(ValidationError):
        await services.sessions.open_session(provider="claude", client_session_id="")


async def test_updates_append_and_dedupe_by_message_id(services) -> None:
    session_id, _ = await services.sessions.open_session(
        provider="chatgpt", client_session_id="chat-2"
    )
    messages = [
        {"role": "user", "content": "Hello.", "message_id": "m1"},
        {"role": "assistant", "content": "Hi there.", "message_id": "m2"},
    ]
    assert await services.sessions.append_session_messages(session_id, messages) == 2
    assert await services.sessions.append_session_messages(session_id, messages) == 0
    rows = await services.db.fetch(
        "SELECT role, content FROM messages WHERE conversation_id = $1 ORDER BY sequence",
        session_id,
    )
    assert [(r["role"], r["content"]) for r in rows] == [
        ("user", "Hello."),
        ("assistant", "Hi there."),
    ]


async def test_unknown_session_and_bad_role_are_refused(services) -> None:
    from subzero.errors import NotFoundError, ValidationError

    with pytest.raises(NotFoundError):
        await services.sessions.append_session_messages(uuid.uuid4(), [])
    session_id, _ = await services.sessions.open_session(
        provider="grok", client_session_id="chat-3"
    )
    with pytest.raises(ValidationError, match="Role"):
        await services.sessions.append_session_messages(
            session_id, [{"role": "shouter", "content": "HI"}]
        )


async def test_list_orders_by_last_message_and_hides_ended(services) -> None:
    first, _ = await services.sessions.open_session(provider="claude", client_session_id="a")
    second, _ = await services.sessions.open_session(provider="claude", client_session_id="b")
    await services.sessions.append_session_messages(
        first, [{"role": "user", "content": "First.", "message_id": "m1"}]
    )
    await services.sessions.append_session_messages(
        second, [{"role": "user", "content": "Second.", "message_id": "m1"}]
    )
    rows = await services.sessions.list_sessions(provider="claude")
    assert [r["provider"] for r in rows] == ["claude", "claude"]
    assert rows[0]["id"] == second  # most recent message first
    assert rows[0]["message_count"] == 1 and rows[0]["last_message_at"] is not None
    await services.sessions.close_session(second, reason="done")
    live = await services.sessions.list_sessions(provider="claude")
    assert [r["id"] for r in live] == [first]
    all_rows = await services.sessions.list_sessions(provider="claude", include_ended=True)
    assert {r["id"] for r in all_rows} == {first, second}


async def test_checkpoint_lands_on_the_open_session(services) -> None:
    session_id, _ = await services.sessions.open_session(
        provider="claude", client_session_id="cp-1", title="Live work"
    )
    result = await services.sessions.checkpoint(
        provider="claude",
        principal=Principal(subject="tester", provider="claude"),
        session_id=session_id,
        summary="Settled the launch date.",
        decisions=[],
    )
    assert result.conversation_id == session_id
    row = await services.db.fetchrow(
        "SELECT count(*) AS n FROM session_summaries WHERE conversation_id = $1", session_id
    )
    assert row["n"] == 1


async def test_checkpoint_with_an_unknown_session_id_is_refused(services) -> None:
    from subzero.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await services.sessions.checkpoint(
            provider="claude",
            principal=Principal(subject="tester", provider="claude"),
            session_id=uuid.uuid4(),
            summary="Settled the launch date.",
        )


async def test_close_is_idempotent_and_validates(services) -> None:
    session_id, _ = await services.sessions.open_session(provider="gemini", client_session_id="c")
    assert await services.sessions.close_session(session_id, reason="done") is True
    assert await services.sessions.close_session(session_id) is True
    assert await services.sessions.close_session(uuid.uuid4()) is False
