"""A human edit in the vault reaches Postgres after the helper restarts.

The helper remembered what it had written only in memory. A fresh process,
which is every ``obsidian-sync run --once`` on the Linux timer and every
restart of the long-running helper, started with nothing to compare against,
so it never noticed an edit and never pushed one upstream.
"""

from __future__ import annotations

from conftest import requires_db
from obsidian_sync.config import WorkerSettings
from obsidian_sync.worker import ObsidianSyncWorker

pytestmark = requires_db


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
    services, live_server, vault, tmp_path
) -> None:
    state_dir = tmp_path / "state"
    # Chat apps no longer write notes through a tool, so the note is written
    # the way the server writes one: upsert the row, then queue the vault job.
    vault_path = services.vault_paths.note(
        note_class="resource", title="Editor setup", project_slug=None
    )
    note_id, _created, _version = await services.notes.upsert(
        title="Editor setup",
        content="Use LSP completion.",
        note_class="resource",
        vault_path=vault_path,
        provenance="system",
        author="system",
    )
    await services.sync_queue.enqueue(
        job_type="write_note",
        object_type="note",
        object_id=note_id,
        vault_path=vault_path,
        payload={"note_id": str(note_id), "reason": "restart-test"},
    )

    first = _worker(services, live_server, vault, state_dir)
    try:
        assert len((await first._tick()).jobs) == 1
    finally:
        await first._client.aclose()

    path = vault / vault_path
    path.write_text(path.read_text().replace("Use LSP completion.", "Use LSP and snippets."))

    second = _worker(services, live_server, vault, state_dir)
    try:
        await second._tick()
    finally:
        await second._client.aclose()

    content = await services.db.fetchval("SELECT content FROM notes WHERE id = $1::uuid", note_id)
    assert "Use LSP and snippets." in content
