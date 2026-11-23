"""A human edit in the vault reaches Postgres after the helper restarts.

The helper remembered what it had written only in memory. A fresh process,
which is every ``obsidian-sync run --once`` on the Linux timer and every
restart of the long-running helper, started with nothing to compare against,
so it never noticed an edit and never pushed one upstream.
"""

from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from conftest import requires_db
from obsidian_sync.config import WorkerSettings
from obsidian_sync.worker import ObsidianSyncWorker

pytestmark = requires_db


@pytest.fixture
async def client(services, live_server):
    async with Client(
        StreamableHttpTransport(
            url=f"{live_server.base_url}{live_server.mcp_path}", auth="test-token"
        )
    ) as mcp:
        yield mcp


def _worker(services, live_server, vault, state_dir) -> ObsidianSyncWorker:
    return ObsidianSyncWorker(
        WorkerSettings(
            api_url=str(live_server.base_url).rstrip("/"),
            api_key=services.settings.worker_api_key,
            vault_path=vault,
            vault_roots=services.settings.obsidian_vault_roots,
            worker_id="restart-test-worker",
            state_dir=state_dir,
        )
    )


async def test_an_edit_made_after_a_restart_is_pushed(
    client, services, live_server, vault, tmp_path
) -> None:
    state_dir = tmp_path / "state"
    written = (
        await client.call_tool(
            "note_write",
            {
                "title": "Editor setup",
                "content": "Use LSP completion.",
                "home": "setup/neovim",
                "note_class": "resource",
            },
        )
    ).data

    first = _worker(services, live_server, vault, state_dir)
    try:
        assert await first._tick() == 1
    finally:
        await first._client.aclose()

    path = vault / written["vault_path"]
    path.write_text(path.read_text().replace("Use LSP completion.", "Use LSP and snippets."))

    second = _worker(services, live_server, vault, state_dir)
    try:
        await second._tick()
    finally:
        await second._client.aclose()

    content = await services.db.fetchval(
        "SELECT content FROM notes WHERE id = $1::uuid", written["note_id"]
    )
    assert "Use LSP and snippets." in content
