"""``captured`` means the stored bytes were read back and their hash matched.

Before this, ``save_files`` compared the hash of the local buffer with itself
(``put`` hashes what it sent, not what was stored), ``artifact_complete_upload``
recorded whatever hash the caller claimed after checking only that an object
existed, two saves of one slug could pick the same version, and a bundle that
failed part-way stayed ``referenced`` for ever with no sign of what happened.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from conftest import requires_db
from subzero.errors import IntegrityError

pytestmark = requires_db


def _corrupting(store):
    """A store that keeps a different byte string from the one it was given."""
    original = store.put

    async def put(object_key, data, **kwargs):
        stored = await original(object_key, data[:-1] + b"?", **kwargs)
        return dataclasses.replace(stored, sha256=hashlib.sha256(data).hexdigest())

    return put


async def test_bytes_that_differ_in_storage_are_not_captured(services, monkeypatch) -> None:
    monkeypatch.setattr(services.artifacts._store, "put", _corrupting(services.artifacts._store))

    with pytest.raises(IntegrityError):
        await services.artifacts.save_files(title="Notes", files={"a.txt": b"hello"})

    assert (
        await services.db.fetchval(
            "SELECT storage_status::text FROM artifacts WHERE slug = 'notes'"
        )
        == "failed"
    )


async def test_concurrent_saves_of_one_slug_get_distinct_versions(services) -> None:
    records = await asyncio.gather(
        *(
            services.artifacts.save_files(title="Report", files={"r.md": f"v{n}".encode()})
            for n in range(5)
        )
    )

    assert sorted(r.version for r in records) == [1, 2, 3, 4, 5]
    assert all(r.storage_status == "captured" for r in records)


async def test_a_bundle_that_fails_part_way_is_marked_failed(services, monkeypatch) -> None:
    original = services.artifacts._store.put

    async def put(object_key, data, **kwargs):
        if object_key.endswith("b.txt"):
            raise OSError("storage went away")
        return await original(object_key, data, **kwargs)

    monkeypatch.setattr(services.artifacts._store, "put", put)

    with pytest.raises(OSError):
        await services.artifacts.save_files(
            title="Bundle", files={"a.txt": b"first", "b.txt": b"second"}
        )

    assert (
        await services.db.fetchval(
            "SELECT storage_status::text FROM artifacts WHERE slug = 'bundle'"
        )
        == "failed"
    )


@pytest.fixture
async def client(services, live_server):
    async with Client(
        StreamableHttpTransport(
            url=f"{live_server.base_url}{live_server.mcp_path}", auth="test-token"
        )
    ) as mcp:
        yield mcp


async def test_completing_an_upload_checks_the_bytes_against_the_hash(client, services) -> None:
    begun = (
        await client.call_tool(
            "artifact_begin_upload",
            {"title": "Scan", "filename": "scan.pdf", "byte_size": 5},
        )
    ).data
    key = await services.db.fetchval(
        "SELECT object_key FROM artifact_files WHERE artifact_id = $1::uuid",
        begun["artifact_id"],
    )
    # What the client actually PUT to the signed URL.
    await services.store.put(key, b"other", mime_type="application/pdf")

    claimed = hashlib.sha256(b"hello").hexdigest()
    result = (
        await client.call_tool(
            "artifact_complete_upload",
            {
                "artifact_id": begun["artifact_id"],
                "relative_path": begun["relative_path"],
                "sha256": claimed,
            },
        )
    ).data

    assert result["captured"] is False
    assert (
        await services.db.fetchval(
            "SELECT storage_status::text FROM artifact_files WHERE artifact_id = $1::uuid",
            begun["artifact_id"],
        )
        != "captured"
    )
