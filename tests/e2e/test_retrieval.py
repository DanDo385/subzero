"""Retrieval: bootstrap handoff order and stale decisions on the eval fixture."""

from __future__ import annotations

from support.eval import bootstrap, item_keys, layer


async def test_handoff_comes_before_search_results(seeded_mcp_world) -> None:
    client, seeded = seeded_mcp_world
    package = await bootstrap(
        client, project="harbor-notes", query="open source", token_budget=1600
    )
    keys = item_keys(package, seeded)
    handoff = layer(package, "handoff")
    assert handoff["status"] == "ran"
    assert keys[0] == "s_hn_untagged"
    layers = [item["layer"] for item in package["items"]]
    assert "relevant" in layers
    assert all(entry == "handoff" for entry in layers[: layers.index("relevant")])


async def test_superseded_decision_is_not_silently_preferred(seeded_mcp_world) -> None:
    client, seeded = seeded_mcp_world
    found = await client.call_tool("context_search", {"query": "store data in SQLite"})
    items = {seeded.key_for(item["id"]): item for item in found.structured_content["result"]}
    old_decision = items["d_hn_sqlite"]
    assert old_decision["status"] == "superseded"
    assert old_decision["superseded_by"] == str(seeded.ids["d_hn_postgres"])
