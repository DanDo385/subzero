"""Background classification picks up what the browser extension saved."""

from __future__ import annotations

from conftest import requires_db
from subzero.importers.reclassify import unclassified_conversations

pytestmark = requires_db


async def _captured(services, cid: str, *, messages: bool = True):
    conv_id, _ = await services.sessions.ensure_conversation(
        provider="chatgpt", provider_conversation_id=cid, title=cid, imported=True
    )
    if messages:
        await services.sessions.add_messages(
            conv_id,
            [{"provider_message_id": f"{cid}-1", "role": "user", "content": "hello there"}],
            provider="chatgpt",
        )
    return conv_id


async def _event(services, conv_id, *, degraded: bool) -> None:
    await services.db.execute(
        """
        INSERT INTO classification_events (object_type, object_id, classifier, questions_version,
                                           input_hash, answers, action_taken, degraded)
        VALUES ('checkpoint', $1, 'jev', 'test', 'x', '{}'::jsonb, 'none', $2)
        """,
        conv_id,
        degraded,
    )


async def test_only_conversations_never_classified_are_picked(services):
    fresh = await _captured(services, "fresh")
    done = await _captured(services, "done")
    retry = await _captured(services, "retry")
    await _captured(services, "empty", messages=False)
    await _event(services, done, degraded=False)
    await _event(services, retry, degraded=True)  # Jev was down: try again

    picked = await unclassified_conversations(services, limit=10)
    assert set(picked) == {fresh, retry}
