"""Loopback server and MCP/HTTP client fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from subzero.config import Settings

MCP_AUTH_TOKEN = "test-token"


@pytest.fixture
async def http_client(live_server: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=str(live_server.base_url)) as client:
        yield client


@pytest.fixture
async def mcp_client(services, live_server: Settings) -> AsyncIterator[Client]:
    async with Client(
        StreamableHttpTransport(
            url=f"{live_server.base_url}{live_server.mcp_path}",
            auth=MCP_AUTH_TOKEN,
        )
    ) as client:
        yield client


@pytest.fixture
async def read_only_mcp_client(services, live_server: Settings) -> AsyncIterator[Client]:
    services.settings.client_scope_policy = {"*": ["read"]}
    async with Client(
        StreamableHttpTransport(
            url=f"{live_server.base_url}{live_server.mcp_path}",
            auth=MCP_AUTH_TOKEN,
        )
    ) as client:
        yield client


def mcp_url(settings: Settings) -> str:
    return f"{settings.base_url}{settings.mcp_path}"