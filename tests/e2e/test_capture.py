"""Capture path: pair a browser, ingest extension fixtures, recall through MCP."""

from __future__ import annotations

import asyncio

import httpx

from support.ingest import load_fixture
from support.pair import pair_device


async def test_unauthenticated_ingest_returns_401(http_client: httpx.AsyncClient) -> None:
    response = await http_client.post(
        "/api/v1/ingest/conversation",
        json={"provider": "claude", "provider_conversation_id": "x"},
    )
    assert response.status_code == 401


async def test_pairing_code_is_single_use(http_client: httpx.AsyncClient, services) -> None:
    from support.pair import insert_pairing_code

    code = await insert_pairing_code(services.db, code="E2EONCE42")

    async def pair():
        return await http_client.post("/api/v1/auth/pair-device", json={"pairing_code": code})

    first, second = await asyncio.gather(pair(), pair())
    assert sorted([first.status_code, second.status_code]) == [200, 403]


async def test_ingest_fixture_then_recall_via_mcp(
    http_client: httpx.AsyncClient, mcp_client, services
) -> None:
    token = await pair_device(http_client, services.db, code="E2ECAPTURE")
    payload = load_fixture("claude")
    headers = {"Authorization": f"Bearer {token}"}

    first = await http_client.post("/api/v1/ingest/conversation", json=payload, headers=headers)
    assert first.status_code == 200
    data1 = first.json()
    assert data1["status"] == "created"
    assert data1["messages_inserted"] == len(payload["messages"])

    second = await http_client.post("/api/v1/ingest/conversation", json=payload, headers=headers)
    assert second.status_code == 200
    data2 = second.json()
    assert data2["conversation_id"] == data1["conversation_id"]
    assert data2["status"] == "updated"
    assert data2["messages_inserted"] == 0
    assert data2["messages_skipped"] == len(payload["messages"])

    # A human message from the fixture should be searchable once ingested.
    snippet = payload["messages"][0]["content"][:40]
    found = await mcp_client.call_tool(
        "context_search", {"query": snippet, "limit": 5, "include_messages": True}
    )
    texts = [getattr(i, "content", "") or str(i) for i in found.data]
    assert any(snippet[:20] in t for t in texts)
