"""Integration tests for the browser extension ingest endpoint.

These tests run against a real database (PC_TEST_DATABASE_URL).
They verify idempotent upsert, message dedup, and device token auth.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from conftest import requires_db
from subzero.auth.principal import Principal

pytestmark = requires_db


@pytest.fixture
async def client(services, live_server):
    """Use the disposable migrated database and real app lifespan."""
    async with httpx.AsyncClient(base_url=str(live_server.base_url)) as client:
        yield client


@pytest.fixture
async def pairing_code(services):
    code = "TESTPAIR42"
    await services.db.execute(
        "INSERT INTO pairing_codes (code, device_name) VALUES ($1, $2)",
        code,
        "test-browser",
    )
    return code


@pytest.fixture
async def device_token(client, pairing_code):
    response = await client.post("/api/v1/auth/pair-device", json={"pairing_code": pairing_code})
    assert response.status_code == 200
    return response.json()["device_token"]


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "extension" / "tests" / "fixtures"


def _load_fixture(provider: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{provider}-conversation.json").read_text())


async def test_legacy_unauthenticated_registration_is_disabled(client):
    response = await client.post("/api/v1/auth/register-device", json={"device_name": "attacker"})
    assert response.status_code == 404


async def test_pairing_code_is_single_use_under_concurrent_requests(client, pairing_code):
    async def pair():
        return await client.post("/api/v1/auth/pair-device", json={"pairing_code": pairing_code})

    first, second = await asyncio.gather(pair(), pair())
    assert sorted([first.status_code, second.status_code]) == [200, 403]


async def test_pairing_rejects_unknown_code(client):
    response = await client.post("/api/v1/auth/pair-device", json={"pairing_code": "UNKNOWN42"})
    assert response.status_code == 403


async def test_unauthorized_ingest(client):
    """Ingest without a token returns 401."""
    resp = await client.post(
        "/api/v1/ingest/conversation",
        json={"provider": "claude", "provider_conversation_id": "x"},
    )
    assert resp.status_code == 401


async def test_unauthorized_handoff(client):
    """Handoff without a token returns 401."""
    resp = await client.get("/api/v1/handoff")
    assert resp.status_code == 401


async def test_ingest_missing_fields(client, device_token):
    """Ingest without required fields returns 400."""
    resp = await client.post(
        "/api/v1/ingest/conversation",
        json={"messages": []},
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert resp.status_code == 400


@pytest.mark.parametrize("provider", ["claude", "chatgpt", "gemini", "grok"])
async def test_ingest_new_conversation(client, device_token, provider):
    """Ingest a new conversation creates it."""
    payload = _load_fixture(provider)
    resp = await client.post(
        "/api/v1/ingest/conversation",
        json=payload,
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert data["messages_inserted"] == len(payload["messages"])
    assert data["messages_skipped"] == 0


@pytest.mark.parametrize("provider", ["claude", "chatgpt", "gemini", "grok"])
async def test_ingest_idempotent(client, device_token, provider):
    """Re-sending the same conversation is idempotent."""
    payload = _load_fixture(provider)
    # First send.
    resp1 = await client.post(
        "/api/v1/ingest/conversation",
        json=payload,
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert resp1.status_code == 200
    data1 = resp1.json()

    # Second send — same payload.
    resp2 = await client.post(
        "/api/v1/ingest/conversation",
        json=payload,
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()

    # Same conversation ID.
    assert data2["conversation_id"] == data1["conversation_id"]
    # Status is "updated" (upsert hit the existing row).
    assert data2["status"] == "updated"
    # No new messages inserted (all skipped by dedup).
    assert data2["messages_inserted"] == 0
    assert data2["messages_skipped"] == len(payload["messages"])


async def test_ingest_accepts_muse(client, device_token):
    """The extension captures Muse; the server must know the provider."""
    payload = _load_fixture("grok")
    payload["provider"] = "muse"
    payload["provider_conversation_id"] = "muse-conversation-1"
    resp = await client.post(
        "/api/v1/ingest/conversation",
        json=payload,
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["messages_inserted"] == len(payload["messages"])


@pytest.mark.parametrize("provider", ["claude", "chatgpt"])
async def test_ingest_append_messages(client, device_token, provider, services):
    """Sending a conversation with additional messages appends only the new ones."""
    payload = _load_fixture(provider)
    # First send with original messages.
    resp1 = await client.post(
        "/api/v1/ingest/conversation",
        json=payload,
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert resp1.status_code == 200

    # Add a new message.
    payload["messages"].append(
        {
            "provider_message_id": f"{provider}-new-msg-001",
            "role": "assistant",
            "content": "This is a follow-up response that was added later.",
            "created_at": "2026-10-15T12:00:00Z",
            "model": payload.get("model"),
        }
    )

    resp2 = await client.post(
        "/api/v1/ingest/conversation",
        json=payload,
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["status"] == "updated"
    assert data["messages_inserted"] == 1  # Only the new message.
    assert data["messages_skipped"] == len(payload["messages"]) - 1
    # The appended message takes the next sequence slot: stored order has no gaps.
    sequences = await services.db.fetch(
        "SELECT sequence FROM messages WHERE conversation_id = $1 ORDER BY sequence",
        UUID(data["conversation_id"]),
    )
    assert [row["sequence"] for row in sequences] == list(range(len(payload["messages"])))


async def test_revoke_device(client, device_token, services):
    """Revoking a device token invalidates it."""
    # The fixture token is paired through the one-time-code endpoint.
    device_token_new = device_token
    device_id = await services.db.fetchval(
        "SELECT device_id FROM device_tokens WHERE token = $1", device_token
    )
    assert device_id is not None

    # Revoke it.
    resp = await client.delete(
        f"/api/v1/auth/devices/{device_id}",
        headers={"Authorization": f"Bearer {device_token_new}"},
    )
    assert resp.status_code == 200

    # Token should now be invalid.
    resp = await client.get(
        "/api/v1/handoff",
        headers={"Authorization": f"Bearer {device_token_new}"},
    )
    assert resp.status_code == 401


async def test_handoff_returns_data(client, device_token):
    """Handoff endpoint returns JSON even when empty."""
    resp = await client.get(
        "/api/v1/handoff",
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "handoff" in data


async def test_handoff_contains_a_saved_checkpoint(client, device_token, services):
    await services.sessions.checkpoint(
        provider="claude",
        provider_conversation_id="handoff-test",
        principal=Principal(subject="tester@example.com", provider="claude"),
        title="Editor project handoff",
        summary="Next step: configure the editor language server.",
        queue_for_vault=False,
    )
    response = await client.get(
        "/api/v1/handoff", headers={"Authorization": f"Bearer {device_token}"}
    )
    assert response.status_code == 200
    assert "configure the editor language server" in response.json()["handoff"]


async def test_recapture_never_removes_history(client, device_token, services):
    """A later, thinner capture (fewer messages, blank title, fewer attachments)
    only ever adds: stored messages, title, model and attachments survive."""
    headers = {"Authorization": f"Bearer {device_token}"}
    full = _load_fixture("claude")
    full["provider_conversation_id"] = "history-guard-1"
    full["title"] = "The real title"
    full["model"] = "claude-test-model"
    full["attachments"] = [
        {"provider_attachment_id": "att-1", "file_name": "a.pdf"},
        {"provider_attachment_id": "att-2", "file_name": "b.png"},
    ]
    first = await client.post("/api/v1/ingest/conversation", json=full, headers=headers)
    assert first.status_code == 200, first.text
    conv_id = UUID(first.json()["conversation_id"])
    stored = len(full["messages"])

    thin = {
        "provider": "claude",
        "provider_conversation_id": "history-guard-1",
        "title": "  ",
        "model": "",
        "messages": [
            full["messages"][-1],
            {"provider_message_id": "later-1", "role": "user", "content": "a new turn"},
        ],
        "attachments": [{"provider_attachment_id": "att-3", "file_name": "c.txt"}],
    }
    second = await client.post("/api/v1/ingest/conversation", json=thin, headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["messages_inserted"] == 1

    row = await services.db.fetchrow(
        "SELECT title, model, metadata FROM conversations WHERE id = $1", conv_id
    )
    assert row["title"] == "The real title"
    assert row["model"] == "claude-test-model"
    ids = [a["provider_attachment_id"] for a in row["metadata"]["_attachments"]]
    assert ids == ["att-1", "att-2", "att-3"]

    messages = await services.db.fetch(
        "SELECT provider_message_id FROM messages WHERE conversation_id = $1 ORDER BY sequence",
        conv_id,
    )
    assert len(messages) == stored + 1
    assert [m["provider_message_id"] for m in messages][:stored] == [
        m["provider_message_id"] for m in full["messages"]
    ]
    assert messages[-1]["provider_message_id"] == "later-1"


async def test_sync_state_reports_what_the_server_has(client, device_token):
    """The extension asks which conversations exist and how fresh they are,
    then fetches only newer ones. Provider update times only move forward."""
    headers = {"Authorization": f"Bearer {device_token}"}
    unauthorized = await client.get("/api/v1/ingest/sync-state?provider=claude")
    assert unauthorized.status_code == 401

    payload = _load_fixture("claude")
    payload["provider_conversation_id"] = "sync-1"
    payload["updated_at"] = "2026-11-13T10:00:00Z"
    assert (
        await client.post("/api/v1/ingest/conversation", json=payload, headers=headers)
    ).status_code == 200
    # A stale capture (older update time) must not move the watermark back.
    payload["updated_at"] = "2026-11-01T00:00:00Z"
    assert (
        await client.post("/api/v1/ingest/conversation", json=payload, headers=headers)
    ).status_code == 200

    state = (await client.get("/api/v1/ingest/sync-state?provider=claude", headers=headers)).json()
    assert state["count"] == 1
    assert state["conversations"]["sync-1"].startswith("2026-11-13T10:00:00")
    assert state["latest"] == state["conversations"]["sync-1"]

    empty = (await client.get("/api/v1/ingest/sync-state?provider=grok", headers=headers)).json()
    assert empty == {"provider": "grok", "count": 0, "latest": None, "conversations": {}}
