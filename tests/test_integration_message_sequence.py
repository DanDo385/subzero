"""Two writers adding messages to one conversation never pick the same numbers.

``add_messages`` numbered new messages from ``max(sequence) + 1`` without
locking the conversation, so a second writer computed the same numbers as a
first one still in flight and failed on ``messages_conv_seq_key`` once the
first committed. Two browsers, or periodic sync racing a manual capture, did
exactly that.
"""

from __future__ import annotations

import asyncio

from conftest import requires_db

pytestmark = requires_db


def _batch(tag: str) -> list[dict]:
    return [{"provider_message_id": f"{tag}-{n}", "role": "user", "content": tag} for n in range(3)]


async def test_concurrent_writers_get_distinct_sequences(services) -> None:
    conv_id, _ = await services.sessions.ensure_conversation(
        provider="chatgpt", provider_conversation_id="race-1", title="Race"
    )
    first_wrote = asyncio.Event()
    release_first = asyncio.Event()

    async def first() -> int:
        async with services.db.transaction() as conn:
            n = await services.sessions.add_messages(
                conv_id, _batch("a"), provider="chatgpt", conn=conn
            )
            first_wrote.set()
            await release_first.wait()
            return n

    async def second() -> int:
        await first_wrote.wait()
        async with services.db.transaction() as conn:
            return await services.sessions.add_messages(
                conv_id, _batch("b"), provider="chatgpt", conn=conn
            )

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await first_wrote.wait()
    await asyncio.sleep(0.2)  # the second writer is now waiting on the first
    release_first.set()

    assert await asyncio.gather(first_task, second_task) == [3, 3]
    sequences = await services.db.fetch(
        "SELECT sequence FROM messages WHERE conversation_id = $1 ORDER BY sequence", conv_id
    )
    assert [r["sequence"] for r in sequences] == list(range(6))
