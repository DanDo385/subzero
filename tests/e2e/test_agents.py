"""Agents: risky dispatch and agent-scoped HTTP."""

from __future__ import annotations

import httpx

from support.pair import pair_device


async def test_risky_ship_starts_as_scout(services) -> None:
    job = await services.agents.dispatch(
        "ship this to production tonight", target="crew", mode="ship"
    )
    assert job["mode"] == "scout"
    assert job["notes"]


async def test_agent_start_over_http(http_client: httpx.AsyncClient, services) -> None:
    token = await pair_device(
        http_client,
        services.db,
        code="E2EAGENT1",
        device_name="mbp",
        scopes=["ingest", "agents"],
    )
    headers = {"Authorization": f"Bearer {token}"}

    session = await http_client.post(
        "/api/v1/agents/start",
        json={"harness": "cursor", "machine": "mbp", "cwd": "/tmp"},
        headers=headers,
    )
    assert session.status_code == 200
    assert session.json().get("id")
