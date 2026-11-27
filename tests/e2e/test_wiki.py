"""Wiki: title mention creates a link without a model call."""

from __future__ import annotations

from subzero.wiki.service import WikiService


async def test_title_mention_links_without_jev(services) -> None:
    source_id, _, _ = await services.notes.upsert(
        title="MCP OAuth",
        content="Clients use PKCE during the flow.",
        note_class="wiki",
    )
    target_id, _, _ = await services.notes.upsert(
        title="PKCE",
        content="Proof Key for Code Exchange.",
        note_class="wiki",
    )
    for note_id, title in ((source_id, "MCP OAuth"), (target_id, "PKCE")):
        path = services.vault_paths.note(note_class="wiki", title=title)
        await services.db.execute(
            "UPDATE notes SET vault_path = $2 WHERE id = $1", note_id, path
        )

    report = await WikiService(services).build_links()
    edge = await services.db.fetchrow(
        """
        SELECT status, source FROM note_edges
        WHERE source_note_id = $1 AND target_note_id = $2
        """,
        source_id,
        target_id,
    )
    assert edge is not None
    assert edge["status"] == "accepted" and edge["source"] == "title_mention"
    assert report.title_links >= 1