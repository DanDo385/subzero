"""context_bootstrap and context_search answer a query-only call through real MCP.

Gemini and ChatGPT reported INVALID_REQUEST / INVALID_ARGUMENT for
``context_bootstrap(query=...)`` and ``context_search(query=...)`` on
2026-11-25 while other read tools worked. The server answered those calls with
HTTP 200, so this pins the server side: a call carrying only ``query`` (every
optional field omitted, as those clients send it) must succeed, return the
structured result its output schema promises, and leave archived notes out.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from conftest import requires_db

pytestmark = requires_db


@pytest.fixture
async def mcp_client(services, live_server):
    """A real MCP client over a real socket, with real auth."""
    async with Client(
        StreamableHttpTransport(
            url=f"{live_server.base_url}{live_server.mcp_path}", auth="test-token"
        )
    ) as client:
        yield client


async def _notes(services) -> tuple[Any, Any]:
    live, _, _ = await services.notes.upsert(
        title="Treasure Coast agency pilot",
        content="Digital marketing agency pilot with UGC creative for local businesses.",
        note_class="resource",
    )
    archived, _, _ = await services.notes.upsert(
        title="Retired agency gateway",
        content="Digital marketing agency gateway from a retired tool, kept as history.",
        note_class="resource",
    )
    await services.db.execute("UPDATE notes SET note_class = 'archive' WHERE id = $1", archived)
    return str(live), str(archived)


def _ids(items: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("id")) for item in items}


async def test_context_search_with_only_a_query(services, mcp_client) -> None:
    live, archived = await _notes(services)

    result = await mcp_client.call_tool("context_search", {"query": "digital marketing agency UGC"})

    assert not result.is_error
    items = result.structured_content["result"]
    assert isinstance(items, list)
    assert live in _ids(items)
    assert archived not in _ids(items)
    # Clients that read the text block instead of structuredContent get the same list.
    assert json.loads(result.content[0].text) == items


async def test_context_bootstrap_with_only_a_query(services, mcp_client) -> None:
    live, archived = await _notes(services)

    result = await mcp_client.call_tool(
        "context_bootstrap", {"query": "digital marketing agency UGC portfolio"}
    )

    assert not result.is_error
    package = result.structured_content
    assert isinstance(package, dict)
    assert package["query"] == "digital marketing agency UGC portfolio"
    ids = _ids(package["items"])
    assert live in ids
    assert archived not in ids


async def test_context_bootstrap_with_no_arguments(mcp_client) -> None:
    result = await mcp_client.call_tool("context_bootstrap", {})

    assert not result.is_error
    assert isinstance(result.structured_content, dict)
