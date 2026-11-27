"""MCP surface: tools, prompts, resources, and read-only scope policy."""

from __future__ import annotations

import json

import pytest
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError


async def test_client_can_list_the_full_tool_surface(mcp_client) -> None:
    tools = {t.name for t in await mcp_client.list_tools()}

    assert {
        "context_bootstrap",
        "context_search",
        "memory_save",
        "memory_search",
        "session_checkpoint",
        "project_list",
        "inbox_add",
        "artifact_save_bundle",
        "artifact_register_reference",
        "review_list",
        "sync_status",
    } <= tools


async def test_tool_descriptions_tell_the_model_when_not_to_call(mcp_client) -> None:
    tools = {t.name: t for t in await mcp_client.list_tools()}
    bootstrap = tools["context_bootstrap"].description or ""
    assert "Skip for trivial questions" in bootstrap


async def test_artifact_tools_warn_against_claiming_capture(mcp_client) -> None:
    tools = {t.name: t for t in await mcp_client.list_tools()}
    reference = tools["artifact_register_reference"].description or ""
    assert "WITHOUT claiming" in reference
    assert "not captured" in reference.lower()


async def test_prompts_are_exposed_for_clients_without_skills(mcp_client) -> None:
    names = {p.name for p in await mcp_client.list_prompts()}
    assert {"recall_context", "checkpoint_session", "capture_artifacts"} <= names


async def test_status_resource_reports_counts_not_content(mcp_client) -> None:
    await mcp_client.call_tool("memory_save", {"content": "A private thought."})
    contents = await mcp_client.read_resource("subzero://status")
    payload = json.loads(contents[0].text)

    assert payload["memories"] == 1
    assert "A private thought" not in contents[0].text


async def test_skill_guides_are_served_as_resources(mcp_client) -> None:
    contents = await mcp_client.read_resource("subzero://guide/recall-context")
    assert "Recall context" in contents[0].text


async def test_guide_resource_refuses_path_traversal(mcp_client) -> None:
    try:
        contents = await mcp_client.read_resource("subzero://guide/..%2F..%2F..%2Fetc%2Fpasswd")
    except MCPError:
        return
    assert "root:" not in contents[0].text
    assert "No guide named" in contents[0].text


async def test_read_only_client_sees_only_read_tools(read_only_mcp_client) -> None:
    names = {t.name for t in await read_only_mcp_client.list_tools()}

    assert {"context_bootstrap", "memory_search", "project_list"} <= names
    assert not ({"memory_save", "session_checkpoint", "artifact_save_bundle"} & names)


async def test_read_only_client_is_refused_a_write_tool(read_only_mcp_client) -> None:
    with pytest.raises(ToolError):
        await read_only_mcp_client.call_tool("memory_save", {"content": "should never be written"})
