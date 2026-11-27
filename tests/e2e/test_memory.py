"""Memory path over MCP: save, search, checkpoint, handoff.

Read-only scope: tests/e2e/test_mcp.py.
"""

from __future__ import annotations


async def test_memory_save_and_search_round_trip(mcp_client) -> None:
    saved = await mcp_client.call_tool(
        "memory_save",
        {
            "content": "Supabase is canonical structured state.",
            "subject": "architecture",
            "provenance": "user_statement",
        },
    )
    assert saved.data.outcome == "created"

    found = await mcp_client.call_tool("memory_search", {"query": "canonical structured state"})
    assert any("Supabase is canonical" in m.content for m in found.data)


async def test_checkpoint_then_bootstrap(mcp_client) -> None:
    await mcp_client.call_tool(
        "session_checkpoint",
        {
            "provider": "claude",
            "provider_conversation_id": "e2e-checkpoint-1",
            "title": "Memory architecture",
            "summary": "Settled the ownership model for structured state.",
            "decisions": [{"statement": "Postgres holds structured state."}],
        },
    )
    pkg = await mcp_client.call_tool(
        "context_bootstrap",
        {"query": "ownership model structured state", "token_budget": 800},
    )
    blob = str(pkg.data)
    assert "structured state" in blob.lower() or "Postgres" in blob


async def test_lesson_handoff_uses_the_explicit_home(mcp_client, services) -> None:
    result = await mcp_client.call_tool(
        "session_checkpoint",
        {
            "provider": "claude",
            "provider_conversation_id": "e2e-lesson",
            "title": "Fixed income duration",
            "summary": "Worked through convexity.",
            "home": "lesson/cfa2",
            "concepts": ["convexity"],
        },
    )
    assert result.data
    metadata = await services.db.fetchval(
        "SELECT metadata FROM conversations WHERE provider_conversation_id = $1",
        "e2e-lesson",
    )
    assert metadata["home"] == "lesson/cfa2"
    assert "concept/convexity" in metadata["tags"]
    package = await mcp_client.call_tool("context_bootstrap", {"home": "lesson/cfa2"})
    assert package.structured_content
    assert any(
        item["layer"] == "handoff" and "convexity" in item["content"]
        for item in package.structured_content["items"]
    )


async def test_unknown_project_does_not_create_project(mcp_client, services) -> None:
    await mcp_client.call_tool(
        "session_checkpoint",
        {
            "provider": "claude",
            "provider_conversation_id": "e2e-unknown",
            "title": "Planning",
            "project": "does-not-exist",
            "summary": "Sketched the idea.",
        },
    )
    count = await services.db.fetchval(
        "SELECT count(*) FROM projects WHERE slug = 'does-not-exist'"
    )
    assert count == 0


