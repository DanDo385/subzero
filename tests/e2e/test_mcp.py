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
        "context_read_guidance",
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
    # Bootstrap runs once per chat; the query terms are what to leave out.
    assert "Call once at the start of every chat" in bootstrap
    assert "leave them empty for a standalone question" in bootstrap


async def test_standing_context_reaches_text_only_clients(mcp_client, settings, tmp_path) -> None:
    """What a chat app sees: the standing context and the handoff in the text block."""
    checkout = tmp_path / "dotfiles"
    (checkout / "subzero").mkdir(parents=True)
    (checkout / "subzero" / "CONTEXT.md").write_text("# Context header\n\nFILE-MAP")
    (checkout / "subzero" / "SOUL.md").write_text("# SOUL\n\nVOICE-RULE")
    agents = checkout / "subzero" / "AGENTS.md"
    agents.write_text("# Subzero chat instructions\n\nCHAT-RULE")
    (checkout / "subzero" / "MEMORY.md").write_text("# Memory (pointer)")
    (checkout / "subzero" / "PRE-PROMPT.md").write_text("reference-only marker")
    (checkout / "MACHINES.md").write_text("# Machine details")
    (checkout / "cli").mkdir()
    (checkout / "cli" / "AGENTS.md").write_text("CLI-ONLY landing rules")
    link = tmp_path / "linked-dotfiles"
    link.symlink_to(checkout, target_is_directory=True)
    settings.dotfiles_root = link

    first = await mcp_client.call_tool("context_bootstrap", {})
    text = first.content[0].text
    payload = first.structured_content
    assert text.index("FILE-MAP") < text.index("VOICE-RULE") < text.index("CHAT-RULE")
    ids = {d["document"]: d["content_id"] for d in payload["standing"]}
    assert list(ids) == ["context", "soul", "agents"]
    assert f"subzero/SOUL.md (id {ids['soul']}, local)" in text
    # Only the three standing documents: no reference copy, no machines, no CLI rules.
    for absent in ("reference-only marker", "Machine details", "CLI-ONLY", "Memory (pointer)"):
        assert absent not in text
        assert absent not in json.dumps(payload)
    # The standing context is on top of the retrieval budget, not inside it.
    assert payload["estimated_tokens"] <= payload["token_budget"]

    machines = await mcp_client.call_tool("context_read_guidance", {"document": "machines"})
    assert machines.structured_content["content"] == "# Machine details"
    assert machines.structured_content["path"] == "MACHINES.md"
    memory = await mcp_client.call_tool("context_read_guidance", {"document": "memory"})
    assert memory.structured_content["content"] == "# Memory (pointer)"

    # A local edit is visible on the next call, with a new id.
    agents.write_text("# Subzero chat instructions\n\nUPDATED-RULE")
    second = await mcp_client.call_tool("context_bootstrap", {})
    assert "UPDATED-RULE" in second.content[0].text
    assert second.structured_content["standing"][2]["content_id"] != ids["agents"]

    bad = await mcp_client.call_tool(
        "context_read_guidance", {"document": "../PRE-PROMPT.md"}, raise_on_error=False
    )
    assert bad.is_error


async def test_bootstrap_without_dotfiles_says_why(mcp_client, settings) -> None:
    settings.dotfiles_root = None
    result = await mcp_client.call_tool("context_bootstrap", {})
    assert "standing" not in result.structured_content
    assert "No dotfiles source is configured on this server." in result.content[0].text


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
