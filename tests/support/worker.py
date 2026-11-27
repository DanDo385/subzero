"""HTTP client for the obsidian worker API."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from subzero.server import create_app


@pytest.fixture
async def worker_client(services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services.settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"x-worker-key": services.settings.worker_api_key.get_secret_value()},
    ) as client:
        yield client
