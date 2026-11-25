"""A database behind the code is visible, and tool errors are readable.

On 2026-11-24 the MBP server restarted on code that reads the ``tasks`` table
from migration 0020 while production still had the old view. For a day
``context_bootstrap`` and ``context_search`` failed with ``column
x.provenance does not exist``, clients saw only "Error calling tool", the
request log said HTTP 200, and ``/readyz`` said ready. Each of those is pinned
here.
"""

from __future__ import annotations

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from conftest import requires_db
from subzero.db import schema_status
from subzero.db.migrate import load_migrations, pending_migrations

pytestmark = requires_db


@pytest.fixture(autouse=True)
def _fresh_schema_status():
    schema_status.reset()
    yield
    schema_status.reset()


@pytest.fixture
async def mcp_client(services, live_server):
    async with Client(
        StreamableHttpTransport(
            url=f"{live_server.base_url}{live_server.mcp_path}", auth="test-token"
        )
    ) as client:
        yield client


async def _forget(db, version: str) -> None:
    await db.execute("DELETE FROM schema_migrations WHERE version = $1", version)


async def test_pending_migrations_reads_without_creating_anything(services) -> None:
    assert await pending_migrations(services.db) == []

    latest = load_migrations()[-1].version
    await _forget(services.db, latest)
    assert await pending_migrations(services.db) == [latest]

    await services.db.execute("DROP TABLE schema_migrations")
    assert await pending_migrations(services.db) == [m.version for m in load_migrations()]
    assert not await services.db.fetchval("SELECT to_regclass('schema_migrations') IS NOT NULL")


async def test_readyz_is_not_ready_while_migrations_are_pending(services, live_server) -> None:
    async with httpx.AsyncClient(base_url=live_server.base_url) as http:
        assert (await http.get("/readyz")).json() == {"ready": True}

        await _forget(services.db, load_migrations()[-1].version)
        schema_status.reset()
        response = await http.get("/readyz")
        assert response.status_code == 503
        # Only a count: /readyz may be public, the versions are in the log.
        assert response.json() == {"ready": False, "reason": "migrations_pending", "pending": 1}


async def test_a_missing_column_says_the_schema_is_behind(services, mcp_client, caplog) -> None:
    caplog.set_level("INFO")
    await services.db.execute("ALTER TABLE tasks DROP COLUMN provenance")

    for name, args in (
        ("context_search", {"query": "digital marketing agency UGC"}),
        ("context_bootstrap", {"query": "digital marketing agency UGC portfolio"}),
    ):
        result = await mcp_client.call_tool_mcp(name, args)
        text = result.content[0].text
        assert result.isError
        assert "schema is behind" in text
        assert "subzero migrate" in text
        assert "not your arguments" in text
        # No SQL or column names reach the client.
        assert "provenance" not in text

    failed = [r for r in caplog.records if r.getMessage() == "tool failed"]
    assert {r.tool for r in failed} >= {"context_search", "context_bootstrap"}
    assert all(r.error_kind == "schema" for r in failed)
    assert all(r.error_class == "UndefinedColumnError" for r in failed)

    requests = [r for r in caplog.records if r.getMessage() == "http request"]
    failures = [f for r in requests for f in getattr(r, "tool_failures", [])]
    assert {
        "tool": "context_search",
        "error_class": "UndefinedColumnError",
        "error_kind": "schema",
    } in failures


async def test_a_subzero_error_reaches_the_client_as_written(mcp_client) -> None:
    result = await mcp_client.call_tool_mcp("project_get", {"project": "no-such-project"})
    assert result.isError
    assert "No project matches" in result.content[0].text


async def test_bad_arguments_are_still_reported_by_fastmcp(mcp_client) -> None:
    result = await mcp_client.call_tool_mcp(
        "context_bootstrap", {"query": "x", "title": "Startup tracking"}
    )
    assert result.isError
    assert "title" in result.content[0].text
