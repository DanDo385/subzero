"""Named skill actions map to the underlying MCP tools."""

from __future__ import annotations


async def test_checkpoint_skill_fields_match_session_checkpoint(mcp_client) -> None:
    tools = {t.name: t for t in await mcp_client.list_tools()}
    checkpoint = tools["session_checkpoint"]
    props = checkpoint.inputSchema["properties"]
    assert {"home", "concepts", "decisions", "facts", "tasks"} <= set(props.keys())

    await mcp_client.call_tool(
        "session_checkpoint",
        {
            "provider": "claude",
            "provider_conversation_id": "e2e-skill-1",
            "title": "Skill wiring",
            "summary": "Checked that checkpoint accepts the skill field set.",
        },
    )
    found = await mcp_client.call_tool(
        "context_search", {"query": "skill field set", "limit": 5}
    )
    assert found.data