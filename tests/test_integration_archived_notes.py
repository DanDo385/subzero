"""A note filed as ``archive`` is history: search and bootstrap stop returning it.

Filing a note under the archive class moved it to ``99-Archive`` in the vault,
but retrieval still ranked it beside live notes, so a retired tool's setup
notes kept arriving in every new chat as if they were current.
"""

from __future__ import annotations

from conftest import requires_db

pytestmark = requires_db


async def test_an_archived_note_is_not_retrieved(services) -> None:
    note_id, _, _ = await services.notes.upsert(
        title="Gateway profiles",
        content="The gateway multiplexes five agent profiles from one process.",
        note_class="resource",
    )

    assert note_id in {i.id for i in await services.retrieval.search("gateway profiles")}

    await services.db.execute("UPDATE notes SET note_class = 'archive' WHERE id = $1", note_id)

    assert note_id not in {i.id for i in await services.retrieval.search("gateway profiles")}
