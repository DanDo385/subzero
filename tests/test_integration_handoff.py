"""Cross-provider handoff: a session saved in one provider's chat resumes in another's.

The transfer object is the context_bootstrap package: it carries no
provider state of its own, only provenance labels saying who said what.
These tests checkpoint as one provider and bootstrap fresh (the way a new
session in a different app would), proving the handoff survives the switch
with provenance and open questions intact.
"""

from __future__ import annotations

from typing import Any

from conftest import requires_db
from support.eval import bootstrap
from support.eval import item_keys as _keys

pytestmark = requires_db


async def _checkpoint(client: Any, **kwargs: Any) -> None:
    result = await client.call_tool("session_checkpoint", kwargs)
    assert not result.is_error, result.content


async def test_handoff_carries_across_providers(seeded_mcp_world) -> None:
    client, seeded = seeded_mcp_world
    # Session 1, as if in Claude: settle something durable.
    await _checkpoint(
        client,
        provider="claude",
        provider_conversation_id="handoff-1",
        title="Tombstone follow-up",
        summary="Decided deleted paragraphs keep a tombstone for thirty days.",
        project="harbor-notes",
        decisions=[
            {
                "statement": "Deleted paragraphs keep a thirty-day tombstone.",
                "provenance": "user_statement",
            }
        ],
        open_questions=["Should tombstones sync to phones?"],
    )
    # Session 2, as if in a different app: a fresh bootstrap, no provider filter.
    package = await bootstrap(client, query="tombstone harbor notes", project="harbor-notes")
    keys = _keys(package, seeded)
    assert "Should tombstones sync to phones?" in [
        q for i in package["items"] for q in i.get("open_questions", [])
    ]
    by_key = {seeded.key_for(i["id"]): i for i in package["items"]}
    assert any(i.get("provenance", {}).get("provider") == "claude" for i in by_key.values()), keys


async def test_provenance_survives_the_transfer(seeded_mcp_world) -> None:
    client, _ = seeded_mcp_world
    await _checkpoint(
        client,
        provider="chatgpt",
        provider_conversation_id="handoff-2",
        title="Queue comparison",
        summary="Compared queues; the durable one won on paper.",
        project="harbor-notes",
        decisions=[
            {
                "statement": "Use a durable job queue for Harbor Notes exports.",
                "provenance": "assistant_generated",
            },
            {"statement": "Exports run nightly for Harbor Notes."},
        ],
    )
    found = await client.call_tool("context_search", {"query": "harbor notes exports"})
    kinds = {i["content"]: i["provenance"]["kind"] for i in found.structured_content["result"]}
    assert kinds["Use a durable job queue for Harbor Notes exports."] == "assistant_generated"
    assert kinds["Exports run nightly for Harbor Notes."] == "user_statement"
