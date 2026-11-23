"""The background classifier claims its work from the durable queue.

It used to rediscover that work every tick by scanning every imported
conversation, which cost a full anti-join whether or not anything was waiting,
and handed the same conversation to every instance that ran the loop.
"""

from __future__ import annotations

from conftest import requires_db
from subzero.importers.reclassify import clear_classified, due_conversations

pytestmark = requires_db


async def _captured(services, cid: str):
    """A conversation the extension saved, queued the way ingest queues it."""
    conv_id, _ = await services.sessions.ensure_conversation(
        provider="chatgpt", provider_conversation_id=cid, title=cid, imported=True
    )
    await services.sessions.add_messages(
        conv_id,
        [{"provider_message_id": f"{cid}-1", "role": "user", "content": "hello there"}],
        provider="chatgpt",
    )
    await services.classification.queue_reclassification(
        object_type="checkpoint", object_id=conv_id, reason="never_classified"
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


async def test_a_claim_reserves_what_it_returns(services):
    first = await _captured(services, "first")
    second = await _captured(services, "second")

    claimed = await due_conversations(services, limit=10)
    assert set(claimed) == {first, second}

    # The claim pushed next_attempt_at forward, so the next tick does not pick
    # the same conversations up again while the first run is still working.
    assert await due_conversations(services, limit=10) == []


async def test_a_held_claim_is_skipped_not_waited_for(services):
    queued = await _captured(services, "held")

    async with services.db.transaction() as conn:
        await conn.fetch("SELECT id FROM reclassification_queue FOR UPDATE")
        # Two servers running the loop must not both be handed this
        # conversation, and the second must not block on the first.
        assert await due_conversations(services, limit=10) == []

    assert await due_conversations(services, limit=10) == [queued]


async def test_only_a_usable_classification_clears_the_queue(services):
    degraded = await _captured(services, "degraded")
    classified = await _captured(services, "classified")
    await _event(services, degraded, degraded=True)
    await _event(services, classified, degraded=False)

    await clear_classified(services, [degraded, classified])

    rows = await services.db.fetch(
        "SELECT object_id FROM reclassification_queue WHERE object_type = 'checkpoint'"
    )
    # A degraded answer is not a classification: it stays queued for the retry
    # the backoff already scheduled, instead of being dropped and forgotten.
    assert {row["object_id"] for row in rows} == {degraded}


async def test_requeueing_pushes_the_next_attempt_out(services):
    conv_id = await _captured(services, "retry")
    before = await services.db.fetchval(
        "SELECT next_attempt_at FROM reclassification_queue WHERE object_id = $1", conv_id
    )

    await services.classification.queue_reclassification(
        object_type="checkpoint", object_id=conv_id, reason="classifier_degraded"
    )

    after = await services.db.fetchval(
        "SELECT next_attempt_at FROM reclassification_queue WHERE object_id = $1", conv_id
    )
    assert after > before
    assert await due_conversations(services, limit=10) == []
