"""Vault path: enqueue, worker lease, ack, sync_status."""

from __future__ import annotations

import httpx

from subzero.errors import PathSecurityError


async def test_traversal_path_is_rejected_before_enqueue(services) -> None:
    note_id, _, _ = await services.notes.upsert(title="A note", content="body")
    try:
        await services.sync_queue.enqueue(
            job_type="write_note",
            object_type="note",
            object_id=note_id,
            vault_path="../../escape.md",
            payload={},
        )
    except PathSecurityError:
        pass
    else:
        raise AssertionError("traversal path was accepted")
    assert await services.db.fetchval("SELECT count(*) FROM sync_jobs") == 0


async def test_worker_lease_and_ack(services, worker_client: httpx.AsyncClient) -> None:
    note_id, _, _ = await services.notes.upsert(
        title="Worker lease",
        content="Rendered body.",
        note_class="research",
        vault_path="30-Research/worker-lease.md",
    )
    await services.sync_queue.enqueue(
        job_type="write_note",
        object_type="note",
        object_id=note_id,
        vault_path="30-Research/worker-lease.md",
        payload={"note_id": str(note_id)},
    )

    lease = await worker_client.post("/worker/jobs/lease", json={"worker_id": "e2e-w1", "limit": 1})
    assert lease.status_code == 200
    jobs = lease.json()["jobs"]
    assert len(jobs) == 1
    job_id = jobs[0]["id"]
    sha = jobs[0].get("expected_sha256") or "abc"

    ack = await worker_client.post(
        "/worker/jobs/ack",
        json={"worker_id": "e2e-w1", "job_id": job_id, "status": "done", "sha256": sha},
    )
    assert ack.status_code == 200
    assert (
        await services.db.fetchval("SELECT status FROM sync_jobs WHERE id = $1::uuid", job_id)
        == "done"
    )


async def test_sync_status_over_mcp(mcp_client) -> None:
    report = await mcp_client.call_tool("sync_status", {})
    payload = report.data if isinstance(report.data, dict) else report.data.model_dump()
    assert "by_status" in payload
    assert "note" in payload
