"""One way into the Inbox and one way out, over real MCP, HTTP and Postgres.

inbox_add parks an unplaceable item deterministically: no Jev call, no
embedding. review_reclassify is the way out: it files the note into its new
folder and queues exactly one vault write for the new path.
"""

from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from conftest import requires_db

pytestmark = requires_db


@pytest.fixture
async def client(services, live_server):
    async with Client(
        StreamableHttpTransport(
            url=f"{live_server.base_url}{live_server.mcp_path}", auth="test-token"
        )
    ) as mcp:
        yield mcp


async def test_inbox_add_parks_one_note_one_job_one_review_row(client, services) -> None:
    args = {
        "title": "Idea about coffee automation",
        "content": "A pour-over rig with a scale that logs every brew to Subzero.",
        "why_uncertain": "No clear project or folder yet.",
    }
    first = (await client.call_tool("inbox_add", args)).data
    second = (await client.call_tool("inbox_add", args)).data

    assert first["note_id"] == second["note_id"]
    assert first["queued"] is True
    assert second["queued"] is False, "an unchanged re-park queues nothing new"
    assert first["vault_path"].startswith("00-Inbox/")
    assert first["vault_path"].endswith(".md")

    assert await services.db.fetchval("SELECT count(*) FROM notes") == 1
    note = await services.db.fetchrow(
        "SELECT note_class, vault_path, sensitivity, metadata FROM notes WHERE id = $1::uuid",
        first["note_id"],
    )
    assert note["note_class"] == "inbox"
    assert note["vault_path"] == first["vault_path"]
    assert note["sensitivity"] == "normal"
    assert note["metadata"]["why_uncertain"] == "No clear project or folder yet."
    assert note["metadata"]["parked_by"] == "unknown"

    jobs = await services.db.fetch(
        "SELECT job_type, vault_path FROM sync_jobs WHERE object_type = 'note'"
    )
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "write_note"
    assert jobs[0]["vault_path"] == first["vault_path"]

    reviews = await services.db.fetch(
        "SELECT * FROM review_queue WHERE object_type = 'note' AND object_id = $1::uuid",
        first["note_id"],
    )
    assert len(reviews) == 1
    review = reviews[0]
    assert str(review["id"]) == first["review_id"]
    assert review["status"] == "open"
    assert review["reason"] == "unplaceable"
    assert review["question"] == "Where does this belong?"
    assert [option["destination"] for option in review["options"]] == [
        "project",
        "area",
        "resource",
        "archive",
        "permanent",
    ]
    assert review["suggested"] is None
    assert review["confidence"] is None

    assert (
        await services.db.fetchval(
            "SELECT count(*) FROM audit_log WHERE tool = 'inbox_add' AND object_id = $1::uuid",
            first["note_id"],
        )
    ) == 2


async def test_inbox_add_with_a_project_suggests_the_project_bucket(client, services) -> None:
    await services.projects.create(name="Shared Memory")
    result = (
        await client.call_tool(
            "inbox_add",
            {
                "title": "Handoff draft",
                "content": "Might belong to the shared memory project, might not.",
                "project": "shared-memory",
            },
        )
    ).data
    note = await services.db.fetchrow(
        "SELECT project_id, metadata FROM notes WHERE id = $1::uuid", result["note_id"]
    )
    project_id = await services.projects.require("shared-memory")
    assert note["project_id"] == project_id
    assert note["metadata"]["home"] == "projects/shared-memory"
    review = await services.db.fetchrow(
        "SELECT suggested FROM review_queue WHERE id = $1::uuid", result["review_id"]
    )
    assert review["suggested"] == {"destination": "project"}


async def test_sensitive_inbox_add_stores_the_note_and_queues_nothing(client, services) -> None:
    result = (
        await client.call_tool(
            "inbox_add",
            {
                "title": "Question about a scan",
                "content": "The doctor suggested another scan next month.",
                "sensitivity": "highly_sensitive",
            },
        )
    ).data
    assert result["queued"] is False
    assert result["vault_path"] is None
    assert "SUBZERO_NO_EXTERNAL_SENSITIVITY" in result["detail"]
    assert "not sent to the vault" in result["detail"]

    note = await services.db.fetchrow(
        "SELECT sensitivity, vault_path FROM notes WHERE id = $1::uuid", result["note_id"]
    )
    assert note["sensitivity"] == "highly_sensitive"
    assert note["vault_path"] is None
    assert await services.db.fetchval("SELECT count(*) FROM sync_jobs") == 0

    # The review row exists, but filing the note into the vault is refused.
    with pytest.raises(ToolError) as refused:
        await client.call_tool(
            "review_reclassify",
            {"review_id": result["review_id"], "destination": "resource"},
        )
    assert "never" in str(refused.value)
    assert await services.db.fetchval("SELECT count(*) FROM sync_jobs") == 0
    assert (
        await services.db.fetchval(
            "SELECT status FROM review_queue WHERE id = $1::uuid", result["review_id"]
        )
    ) == "open"


async def test_review_reclassify_files_the_note_out_of_the_inbox(client, services) -> None:
    parked = (
        await client.call_tool(
            "inbox_add",
            {
                "title": "LSP article notes",
                "content": "How language servers resolve completion candidates.",
                "why_uncertain": "Resource or permanent note, unclear.",
            },
        )
    ).data
    filed = (
        await client.call_tool(
            "review_reclassify",
            {
                "review_id": parked["review_id"],
                "destination": "resource",
                "note": "Reference material.",
            },
        )
    ).data
    assert filed["status"] == "reclassified"

    note = await services.db.fetchrow(
        "SELECT note_class, vault_path FROM notes WHERE id = $1::uuid", parked["note_id"]
    )
    assert note["note_class"] == "resource"
    assert note["vault_path"].startswith("60-Resources/")
    assert not note["vault_path"].startswith("00-Inbox/")

    jobs = await services.db.fetch(
        "SELECT job_type, vault_path, status FROM sync_jobs "
        "WHERE object_type = 'note' ORDER BY created_at"
    )
    assert len(jobs) == 2, "filing queues a second write for the new folder"
    assert jobs[0]["status"] == "cancelled", (
        "the unwritten Inbox job must not survive filing: the worker would "
        "otherwise create the file again at the old path"
    )
    assert jobs[1]["job_type"] == "write_note"
    assert jobs[1]["vault_path"] == note["vault_path"]
    assert jobs[1]["status"] == "approved"

    assert (
        await services.db.fetchval(
            "SELECT count(*) FROM review_queue WHERE object_id = $1::uuid AND status = 'open'",
            parked["note_id"],
        )
    ) == 0


async def test_review_reclassify_refuses_inbox_as_a_destination(client, services) -> None:
    parked = (
        await client.call_tool(
            "inbox_add",
            {"title": "Loose thread", "content": "Something about caching headers."},
        )
    ).data
    with pytest.raises(ToolError) as refused:
        await client.call_tool(
            "review_reclassify",
            {"review_id": parked["review_id"], "destination": "inbox"},
        )
    assert "already" in str(refused.value)
    note = await services.db.fetchrow(
        "SELECT note_class, vault_path FROM notes WHERE id = $1::uuid", parked["note_id"]
    )
    assert note["note_class"] == "inbox"
    assert note["vault_path"].startswith("00-Inbox/")
    assert (
        await services.db.fetchval(
            "SELECT status FROM review_queue WHERE id = $1::uuid", parked["review_id"]
        )
    ) == "open"
    assert await services.db.fetchval("SELECT count(*) FROM sync_jobs") == 1


async def test_filing_a_note_whose_inbox_file_reached_the_vault_says_so(client, services) -> None:
    parked = (
        await client.call_tool(
            "inbox_add",
            {"title": "Already written", "content": "The worker got to this one first."},
        )
    ).data
    # The worker ran, so the Inbox file is on disk and its job is done. Nothing
    # deletes vault files, so filing has to tell the person to move it.
    await services.db.execute(
        "UPDATE sync_jobs SET status = 'done', completed_at = now() WHERE vault_path = $1",
        parked["vault_path"],
    )

    filed = (
        await client.call_tool(
            "review_reclassify",
            {"review_id": parked["review_id"], "destination": "resource"},
        )
    ).data

    assert parked["vault_path"] in filed["tell_the_user"]
    assert "move or remove" in filed["tell_the_user"]
    assert (
        await services.db.fetchval(
            "SELECT count(*) FROM sync_jobs "
            "WHERE vault_path = $1 AND status IN ('queued', 'approved', 'leased')",
            parked["vault_path"],
        )
    ) == 0
    assert (
        await services.db.fetchval(
            "SELECT count(*) FROM sync_jobs "
            "WHERE vault_path LIKE '60-Resources/%' AND status = 'approved'"
        )
    ) == 1
