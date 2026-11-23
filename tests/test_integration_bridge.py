"""The CLI harness publishes and the paired browser reads the bridge contract."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from conftest import requires_db
from subzero.config import Settings

pytestmark = requires_db


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.harness_api_key = SecretStr("test-harness-key")
    return settings


async def test_bridge_sender_auth_and_device_receive_contract(services, live_server):
    """Use the real app and a device paired through the one-time-code endpoint."""
    await services.db.execute(
        "INSERT INTO pairing_codes (code, device_name) VALUES ($1, $2)",
        "BRIDGETEST42",
        "bridge-browser",
    )
    async with httpx.AsyncClient(base_url=str(live_server.base_url)) as client:
        paired = await client.post(
            "/api/v1/auth/pair-device", json={"pairing_code": "BRIDGETEST42"}
        )
        assert paired.status_code == 200
        device_token = paired.json()["device_token"]
        sender_headers = {"Authorization": "Bearer test-harness-key"}
        reader_headers = {"Authorization": f"Bearer {device_token}"}

        unauth = await client.post("/api/v1/sessions", json={"title": "secret"})
        assert unauth.status_code == 401
        assert "secret" not in unauth.text

        sender = await client.post(
            "/api/v1/sessions",
            json={"provider": "firstmate", "title": "Harness task", "model": "codex"},
            headers=sender_headers,
        )
        assert sender.status_code == 200
        created = sender.json()
        session_id = created["session_id"]
        assert created["link"] == f"subzero://session/{session_id}"

        sender_read = await client.get(f"/api/v1/sessions/{session_id}", headers=sender_headers)
        assert sender_read.status_code == 401
        for text in ("first", "second"):
            response = await client.post(
                f"/api/v1/sessions/{session_id}/messages",
                json={"text": text},
                headers=sender_headers,
            )
            assert response.status_code == 200

        browser_write = await client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"text": "browser direction remains out of scope"},
            headers=reader_headers,
        )
        assert browser_write.status_code == 401

        duplicate = await client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"text": "second"},
            headers=sender_headers,
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["status"] == "duplicate"

        no_token = await client.get(f"/api/v1/sessions/{session_id}")
        assert no_token.status_code == 401
        received = await client.get(f"/api/v1/sessions/{session_id}", headers=reader_headers)
        assert received.status_code == 200
        data = received.json()
        assert {key: data[key] for key in ("session_id", "provider", "title", "model")} == {
            "session_id": session_id,
            "provider": "firstmate",
            "title": "Harness task",
            "model": "codex",
        }
        assert [message["content"] for message in data["messages"]] == ["first", "second"]
        assert [message["role"] for message in data["messages"]] == ["assistant", "assistant"]
        assert [message["sequence"] for message in data["messages"]] == [0, 1]
        assert all(message["id"] and message["created_at"] for message in data["messages"])
