"""Integration: MCP transport quirks not covered by tests/e2e/test_mcp.py.

Probe handshake, JSON-only Accept headers, and similar wire-level checks stay
here. Journeys (tools, scopes, prompts, resources) live in tests/e2e/.
"""

from __future__ import annotations

import asyncio

import httpx

from conftest import requires_db

pytestmark = requires_db


async def test_probe_handshake_passes_with_zero_schema_failures(services, live_server) -> None:
    """initialize, tools/list with every schema checked, and the read-only calls."""
    from subzero.probe import run_probe

    lines: list[str] = []
    report = await asyncio.to_thread(
        run_probe,
        f"{live_server.base_url}{live_server.mcp_path}",
        token="test-token",
        oauth=False,
        echo=lines.append,
    )
    assert report.failures == []
    assert "schema validation failures: 0" in lines


async def test_json_only_accept_header_is_served(services, live_server) -> None:
    """Clients that accept only application/json get a JSON reply, not 406."""
    async with httpx.AsyncClient() as http:
        for path in (live_server.mcp_path, f"{live_server.mcp_path}/"):
            response = await http.post(
                f"{live_server.base_url}{path}",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"authorization": "Bearer test-token", "accept": "application/json"},
            )
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("application/json")
            assert response.json()["result"]["tools"]
