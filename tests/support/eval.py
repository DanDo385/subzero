"""Seeded eval corpus and high rate limits for retrieval journeys."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from eval.fixture import Seeded, seed


@pytest.fixture
def unthrottled(settings):
    settings.rate_limit_per_minute = 1_000_000
    settings.rate_limit_burst = 1_000_000
    return settings


@pytest.fixture
async def seeded_mcp_world(
    unthrottled, db, mcp_client
) -> AsyncIterator[tuple[Any, Seeded]]:
    seeded = await seed(db)
    yield mcp_client, seeded


async def bootstrap(client: Any, **args: Any) -> dict[str, Any]:
    result = await client.call_tool("context_bootstrap", args)
    return result.structured_content or {}


def layer(package: dict[str, Any], name: str) -> dict[str, Any]:
    return next(layer for layer in package["layers"] if layer["name"] == name)


def item_keys(package: dict[str, Any], seeded: Seeded) -> list[str | None]:
    return [seeded.key_for(i["id"]) for i in package.get("items", [])]