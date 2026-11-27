"""Inbox tool: park uncertain notes and open a review item."""

from __future__ import annotations


async def test_inbox_add_queues_review(mcp_client, services) -> None:
    out = await mcp_client.call_tool(
        "inbox_add",
        {
            "title": "Uncertain routing item",
            "content": "Not sure which project owns this yet.",
            "why_uncertain": "Mentions two products with no clear primary.",
        },
    )
    data = out.data if isinstance(out.data, dict) else out.data.model_dump()
    assert data.get("review_id")
    note_id = data["note_id"]
    note_class = await services.db.fetchval(
        "SELECT note_class FROM notes WHERE id = $1::uuid", note_id
    )
    assert note_class == "inbox"
