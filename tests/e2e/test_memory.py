"""Memory path over MCP: save, search, checkpoint, sigils, read-only refusal."""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError


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


async def test_lesson_sigil_routes_home_through_checkpoint(mcp_client, services) -> None:
    result = await mcp_client.call_tool(
        "session_checkpoint",
        {
            "provider": "claude",
            "provider_conversation_id": "e2e-sigil-1",
            "title": "^cfa2/fixed-income duration",
            "summary": "Worked through #convexity.",
        },
    )
    assert result.data
    metadata = await services.db.fetchval(
        "SELECT metadata FROM conversations WHERE provider_conversation_id = $1",
        "e2e-sigil-1",
    )
    assert metadata["home"] == "lesson/cfa2"


async def test_unknown_project_sigil_does_not_create_project(mcp_client, services) -> None:
    await mcp_client.call_tool(
        "session_checkpoint",
        {
            "provider": "claude",
            "provider_conversation_id": "e2e-sigil-2",
            "title": "%Code/does-not-exist planning",
            "summary": "Sketched the idea.",
        },
    )
    count = await services.db.fetchval(
        "SELECT count(*) FROM projects WHERE slug = 'does-not-exist'"
    )
    assert count == 0


async def test_read_only_client_cannot_save_memory(read_only_mcp_client) -> None:
    names = {t.name for t in await read_only_mcp_client.list_tools()}
    assert "memory_save" not in names

    with pytest.raises(ToolError):
        await read_only_mcp_client.call_tool(
            "memory_save", {"content": "should never be written"}
        )