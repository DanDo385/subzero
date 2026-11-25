"""Artifacts over MCP: capture honesty."""

from __future__ import annotations


async def test_save_text_is_captured(mcp_client) -> None:
    out = await mcp_client.call_tool(
        "artifact_save_text",
        {
            "filename": "notes.md",
            "content": "# Notes\n\nCaptured through MCP.",
            "title": "E2E notes",
        },
    )
    assert out.data["capture_status"] == "captured"

    found = await mcp_client.call_tool("artifact_search", {"query": "E2E notes"})
    assert any(a.title == "E2E notes" and a.storage_status == "captured" for a in found.data)


async def test_register_reference_is_not_captured(mcp_client) -> None:
    out = await mcp_client.call_tool(
        "artifact_register_reference",
        {
            "title": "Missing export file",
            "description": "Seen in chat only",
            "expected_files": [{"filename": "export.zip", "mime_type": "application/zip"}],
        },
    )
    data = out.data if isinstance(out.data, dict) else out.data.model_dump()
    assert data.get("storage_status") in ("referenced", "pending_import", None)
    assert data.get("capture_status") != "captured"


async def test_save_bundle_is_captured(mcp_client) -> None:
    out = await mcp_client.call_tool(
        "artifact_save_bundle",
        {
            "title": "E2E dashboard",
            "files": {
                "index.html": "<!doctype html><h1>E2E</h1>",
                "styles.css": "body{margin:0}",
                "src/app.js": "console.log('e2e')",
            },
        },
    )
    payload = out.structured_content or out.data
    assert payload["capture_status"] == "captured"
    artifact = payload["artifact"]
    assert artifact["file_count"] == 3
    assert artifact["artifact_type"] == "web_project"
